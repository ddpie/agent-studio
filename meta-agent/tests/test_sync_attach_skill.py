"""Unit tests for sync_agent_skill and attach_agent_skill.

Both tools are thin choreography on top of S3 + DDB; these tests mock those
and verify the branches that are easy to get wrong:

  - name-based library resolution (1 match / 0 match / N match)
  - agent-not-in-workspace refusal
  - empty library refusal
  - noop detection when hashes already match
  - attach refusal when the skill name is already attached
  - redeploy=False skips update_agent

We deliberately do NOT test update_agent's redeploy behavior — that's
covered by its own tests. Here we just assert it gets called with the
right args and its return is surfaced.
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch


# ── Module stubs so `from strands import tool` etc. work in-process ──────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands
_mock_strands_models = sys.modules.get("strands.models") or types.ModuleType("strands.models")
if not hasattr(_mock_strands_models, "BedrockModel"):
    _mock_strands_models.BedrockModel = MagicMock
sys.modules["strands.models"] = _mock_strands_models


def _install_config_stub():
    mod = sys.modules.get("config") or types.ModuleType("config")
    defaults = {
        "MODEL_ID": "mock-model",
        "REGION": "us-east-1",
        "ACCOUNT_ID": "000000000000",
        "S3_BUCKET": "test-bucket",
        "AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/test-role",
        "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/agent",
        "AGENTS_TABLE": "agent-studio-agents",
        "TOOLS_TABLE": "agent-studio-tools",
        "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
        "SUB_AGENT_BASE_DEPLOYMENT_KEY": "base/deployment.zip",
        "PERMISSION_TIER_ROLES": {"readonly": "arn:aws:iam::000:role/x"},
        "DEFAULT_PERMISSION_TIER": "readonly",
        "MCP_GATEWAY_URL": "",
        "SCHEDULER_TARGET_ROLE_ARN": "arn:aws:iam::000:role/sched",
        "CODE_INTERPRETER_ID": "",
        "BROWSER_ID": "",
    }
    for k, v in defaults.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    sys.modules["config"] = mod


_install_config_stub()


# Import the modules under test up-front so `patch("tools.X.Y")` can
# resolve the attribute. Without this, patch() calls pkgutil.resolve_name
# which tries to getattr(tools, "sync_agent_skill") before anything has
# imported that submodule — AttributeError.
from tools import sync_agent_skill as _sync_mod  # noqa: E402
from tools import attach_agent_skill as _attach_mod  # noqa: E402


# ── Shared fakes ─────────────────────────────────────────────────────────

AGENT_ID = "DataAnalyst-bCBR743Mvj"
WS_ID = "ws-test"
USER_ID = "user-test"


def _agent_record():
    return {"agentId": AGENT_ID, "workspace_id": WS_ID, "created_by": USER_ID}


def _metadata(skills=None):
    return {
        "agent_id": AGENT_ID,
        "name": "DataAnalyst",
        "display_name": "DataAnalyst",
        "description": "desc",
        "model_id": "mock-model",
        "system_prompt": "prompt",
        "skills": skills if skills is not None else [],
        "deployedSkillHashes": {},
    }


def _library_files():
    # Keep content small and deterministic so hash is stable in assertions.
    return {
        "SKILL.md": "---\nname: ppt-generator\n---\n# New body",
        "scripts/render.py": "print('render')",
    }


class FakeS3:
    """In-memory S3 stub covering get/put/list/copy/delete used by the tools."""

    def __init__(self, initial: dict[str, bytes]):
        self.store: dict[str, bytes] = dict(initial)
        self.copied: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.puts: list[str] = []

    # Mimic boto3 client methods used by the tools.
    def get_object(self, Bucket, Key):
        if Key not in self.store:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": MagicMock(read=lambda: self.store[Key])}

    def put_object(self, Bucket, Key, Body, **_):
        self.store[Key] = Body if isinstance(Body, bytes) else Body.encode("utf-8")
        self.puts.append(Key)

    def copy_object(self, Bucket, CopySource, Key, **_):
        src = CopySource["Key"]
        if src in self.store:
            self.store[Key] = self.store[src]
        self.copied.append((src, Key))

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            k = obj["Key"]
            self.store.pop(k, None)
            self.deleted.append(k)

    def get_paginator(self, _name):
        store = self.store

        class _P:
            def paginate(self, Bucket, Prefix):
                contents = [{"Key": k} for k in store if k.startswith(Prefix)]
                yield {"Contents": contents}

        return _P()


def _seed_s3(agent_skills, library_id, library_files):
    """Seed S3 with agent metadata + library files + (maybe) old agent copy."""
    data: dict[str, bytes] = {}
    data[f"agents/{AGENT_ID}/metadata.json"] = json.dumps(
        _metadata(agent_skills)
    ).encode("utf-8")
    for rel, content in library_files.items():
        data[f"skills/{library_id}/{rel}"] = content.encode("utf-8")
    # Pre-seed one old file under the attached skill's local prefix so we
    # can assert "delete before copy" actually fires.
    if agent_skills:
        local_id = agent_skills[0]["id"]
        data[f"agents/{AGENT_ID}/skills/{local_id}/stale_leftover.py"] = b"# old"
    return data


class _FakeDDBTable:
    def __init__(self, skills: list[dict], agents: dict | None = None):
        self._skills = skills
        self._agents = agents or {AGENT_ID: _agent_record()}

    def get_item(self, Key):
        if "skillId" in Key:
            for s in self._skills:
                if s.get("skillId") == Key["skillId"]:
                    return {"Item": s}
            return {}
        if "agentId" in Key:
            item = self._agents.get(Key["agentId"])
            return {"Item": item} if item else {}
        return {}

    def query(self, **kwargs):
        # Only supports the workspace-index flow used by _resolve_library_skill.
        from boto3.dynamodb.conditions import Key as _Key  # noqa: F401
        # The ConditionExpression is an opaque Key object; we just return all
        # skills whose workspace matches — the tool applies the name filter.
        return {"Items": [s for s in self._skills if s.get("workspace_id") == WS_ID]}


def _patch_env(s3: FakeS3, skills: list[dict], agents: dict | None = None):
    """Install patches for boto3 calls + workspace scope + update_agent.

    Workspace scope is set per-test rather than at module load because
    other test files (e.g. test_scope) clear ``_scope._workspace_id`` as
    part of their own setup/teardown, and pytest test ordering isn't
    deterministic between invocations.
    """
    table = _FakeDDBTable(skills, agents)

    ddb_resource = MagicMock()
    ddb_resource.Table = MagicMock(return_value=table)

    def _boto3_client(service, **_):
        if service == "s3":
            return s3
        return MagicMock()

    # Patch current_workspace directly rather than setting the module
    # attribute. test_scope deletes + re-imports tools._scope mid-suite,
    # which rebinds sys.modules["tools._scope"] to a fresh module while
    # sync_agent_skill.py's imported-at-top reference still points to the
    # old module. Setting _workspace_id on the new one doesn't help the
    # old one. Patching the bound name in our tool's own namespace works
    # regardless of which _scope module object is live.
    patches = [
        patch("tools.sync_agent_skill.boto3.client", side_effect=_boto3_client),
        patch("tools.sync_agent_skill.boto3.resource", return_value=ddb_resource),
        patch("tools.attach_agent_skill.boto3.client", side_effect=_boto3_client),
        patch("tools.sync_agent_skill.current_workspace", return_value=WS_ID),
        patch(
            "tools.sync_agent_skill.ensure_agent_in_workspace",
            return_value=(_agent_record(), None),
        ),
        patch(
            "tools.attach_agent_skill.ensure_agent_in_workspace",
            return_value=(_agent_record(), None),
        ),
    ]
    return patches


# ── sync_agent_skill tests ───────────────────────────────────────────────

def test_sync_happy_path_by_name():
    """Attached skill's sourceSkillId is stale; library has a new version
    under the same name — tool rebinds + refreshes files + redeploys."""
    OLD_SOURCE = "old-deleted-skill-id"
    NEW_SOURCE = "new-library-skill-id"
    LOCAL = "ab12cd34"
    attached = [{
        "id": LOCAL,
        "name": "ppt-generator",
        "sourceSkillId": OLD_SOURCE,
        "sourceContentHash": "oldhash01",
        "contentHash": "oldhash01",
        "description": "",
        "files": ["SKILL.md"],
    }]
    library = [{
        "skillId": NEW_SOURCE,
        "workspace_id": WS_ID,
        "name": "ppt-generator",
        "description": "SVG-based PPT generator",
    }]
    s3 = FakeS3(_seed_s3(attached, NEW_SOURCE, _library_files()))
    patches = _patch_env(s3, library)
    fake_redeploy = MagicMock(return_value=json.dumps({"status": "QUEUED"}))
    patches.append(patch(
        "tools.sync_agent_skill._update_agent",
        fake_redeploy,
    ))

    for p in patches:
        p.start()
    try:
        from tools.sync_agent_skill import sync_agent_skill
        raw = sync_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()

    out = json.loads(raw)
    assert out["status"] == "synced"
    assert out["old_source_skill_id"] == OLD_SOURCE
    assert out["new_source_skill_id"] == NEW_SOURCE
    assert out["files_copied"] == 2  # SKILL.md + scripts/render.py
    assert out["files_deleted"] >= 1  # stale_leftover.py
    assert out["redeploy"]["status"] == "QUEUED"
    fake_redeploy.assert_called_once()

    # Metadata was written with the new hash and source id.
    written_meta = json.loads(
        s3.store[f"agents/{AGENT_ID}/metadata.json"].decode("utf-8")
    )
    entry = written_meta["skills"][0]
    assert entry["sourceSkillId"] == NEW_SOURCE
    assert entry["sourceContentHash"] == out["new_content_hash"]
    assert entry["files"] == ["SKILL.md", "scripts/render.py"]


def test_sync_errors_when_skill_not_attached():
    s3 = FakeS3(_seed_s3([], "any-id", _library_files()))
    patches = _patch_env(s3, [])
    for p in patches:
        p.start()
    try:
        from tools.sync_agent_skill import sync_agent_skill
        raw = sync_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert "error" in out
    assert "no attached skill" in out["error"]


def test_sync_errors_when_name_ambiguous():
    attached = [{
        "id": "aa11bb22",
        "name": "ppt-generator",
        "sourceSkillId": "x",
        "sourceContentHash": "h1",
        "contentHash": "h1",
        "description": "",
        "files": [],
    }]
    # Two library entries with the same name.
    library = [
        {"skillId": "lib-a", "workspace_id": WS_ID, "name": "ppt-generator", "description": ""},
        {"skillId": "lib-b", "workspace_id": WS_ID, "name": "ppt-generator", "description": ""},
    ]
    s3 = FakeS3(_seed_s3(attached, "lib-a", _library_files()))
    patches = _patch_env(s3, library)
    for p in patches:
        p.start()
    try:
        from tools.sync_agent_skill import sync_agent_skill
        raw = sync_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert "error" in out
    assert "Multiple library skills" in out["error"]


def test_sync_explicit_new_source_disambiguates():
    attached = [{
        "id": "aa11bb22",
        "name": "ppt-generator",
        "sourceSkillId": "lib-a",
        "sourceContentHash": "h1",
        "contentHash": "h1",
        "description": "",
        "files": [],
    }]
    library = [
        {"skillId": "lib-a", "workspace_id": WS_ID, "name": "ppt-generator", "description": ""},
        {"skillId": "lib-b", "workspace_id": WS_ID, "name": "ppt-generator", "description": "newer"},
    ]
    s3 = FakeS3(_seed_s3(attached, "lib-b", _library_files()))
    patches = _patch_env(s3, library)
    patches.append(patch(
        "tools.sync_agent_skill._update_agent",
        MagicMock(return_value=json.dumps({"status": "QUEUED"})),
    ))
    for p in patches:
        p.start()
    try:
        from tools.sync_agent_skill import sync_agent_skill
        raw = sync_agent_skill(
            AGENT_ID, "ppt-generator", new_source_skill_id="lib-b",
        )
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert out["status"] == "synced"
    assert out["new_source_skill_id"] == "lib-b"


def test_sync_refuses_empty_library():
    attached = [{
        "id": "aa11bb22",
        "name": "ppt-generator",
        "sourceSkillId": "lib-empty",
        "sourceContentHash": "h1",
        "contentHash": "h1",
        "description": "",
        "files": [],
    }]
    library = [{"skillId": "lib-empty", "workspace_id": WS_ID, "name": "ppt-generator", "description": ""}]
    # Seed zero files under the library prefix.
    s3 = FakeS3({
        f"agents/{AGENT_ID}/metadata.json": json.dumps(_metadata(attached)).encode("utf-8"),
    })
    patches = _patch_env(s3, library)
    for p in patches:
        p.start()
    try:
        from tools.sync_agent_skill import sync_agent_skill
        raw = sync_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert "error" in out
    assert "no files" in out["error"]


def test_sync_noop_when_hash_matches():
    """Pre-seed attached with the exact hash the library produces."""
    from tools.sync_agent_skill import _compute_content_hash
    expected_hash = _compute_content_hash(_library_files())
    attached = [{
        "id": "aa11bb22",
        "name": "ppt-generator",
        "sourceSkillId": "lib-match",
        "sourceContentHash": expected_hash,
        "contentHash": expected_hash,
        "description": "",
        "files": [],
    }]
    library = [{"skillId": "lib-match", "workspace_id": WS_ID, "name": "ppt-generator", "description": ""}]
    s3 = FakeS3(_seed_s3(attached, "lib-match", _library_files()))
    patches = _patch_env(s3, library)
    for p in patches:
        p.start()
    try:
        from tools.sync_agent_skill import sync_agent_skill
        raw = sync_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert out["status"] == "noop"


def test_sync_redeploy_false_skips_update_agent():
    attached = [{
        "id": "aa11bb22",
        "name": "ppt-generator",
        "sourceSkillId": "lib-new",
        "sourceContentHash": "oldhash",
        "contentHash": "oldhash",
        "description": "",
        "files": [],
    }]
    library = [{"skillId": "lib-new", "workspace_id": WS_ID, "name": "ppt-generator", "description": ""}]
    s3 = FakeS3(_seed_s3(attached, "lib-new", _library_files()))
    patches = _patch_env(s3, library)
    fake_redeploy = MagicMock()
    patches.append(patch("tools.sync_agent_skill._update_agent", fake_redeploy))
    for p in patches:
        p.start()
    try:
        from tools.sync_agent_skill import sync_agent_skill
        raw = sync_agent_skill(AGENT_ID, "ppt-generator", redeploy=False)
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert out["status"] == "synced"
    assert out["redeploy"]["skipped"] is True
    fake_redeploy.assert_not_called()


# ── attach_agent_skill tests ────────────────────────────────────────────

def test_attach_happy_path():
    library = [{"skillId": "lib-new", "workspace_id": WS_ID, "name": "ppt-generator", "description": "svg"}]
    s3 = FakeS3(_seed_s3([], "lib-new", _library_files()))
    patches = _patch_env(s3, library)
    fake_redeploy = MagicMock(return_value=json.dumps({"status": "QUEUED"}))
    patches.append(patch("tools.attach_agent_skill._update_agent", fake_redeploy))
    for p in patches:
        p.start()
    try:
        from tools.attach_agent_skill import attach_agent_skill
        raw = attach_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert out["status"] == "attached"
    assert out["source_skill_id"] == "lib-new"
    assert len(out["local_skill_id"]) == 8
    assert out["files_copied"] == 2
    fake_redeploy.assert_called_once()

    written_meta = json.loads(
        s3.store[f"agents/{AGENT_ID}/metadata.json"].decode("utf-8")
    )
    assert len(written_meta["skills"]) == 1
    assert written_meta["skills"][0]["name"] == "ppt-generator"
    assert written_meta["skills"][0]["id"] == out["local_skill_id"]


def test_attach_refuses_duplicate_name():
    attached = [{
        "id": "aa11bb22",
        "name": "ppt-generator",
        "sourceSkillId": "lib-existing",
        "sourceContentHash": "h1",
        "contentHash": "h1",
        "description": "",
        "files": [],
    }]
    library = [{"skillId": "lib-existing", "workspace_id": WS_ID, "name": "ppt-generator", "description": ""}]
    s3 = FakeS3(_seed_s3(attached, "lib-existing", _library_files()))
    patches = _patch_env(s3, library)
    for p in patches:
        p.start()
    try:
        from tools.attach_agent_skill import attach_agent_skill
        raw = attach_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert "error" in out
    assert "already has a skill" in out["error"]
    assert "sync_agent_skill" in out["error"]


def test_attach_errors_when_library_missing_name():
    library = [{"skillId": "lib-x", "workspace_id": WS_ID, "name": "other-skill", "description": ""}]
    s3 = FakeS3(_seed_s3([], "lib-x", _library_files()))
    patches = _patch_env(s3, library)
    for p in patches:
        p.start()
    try:
        from tools.attach_agent_skill import attach_agent_skill
        raw = attach_agent_skill(AGENT_ID, "ppt-generator")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(raw)
    assert "error" in out
    assert "No library skill named" in out["error"]

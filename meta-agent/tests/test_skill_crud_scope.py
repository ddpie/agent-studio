"""Workspace + role scoping tests for create/update/delete/import_skill.

Regression coverage for the gap where Meta-Agent's skill CRUD tools wrote
only to S3 under a shared ``skills/`` prefix and never checked which
workspace the caller belonged to. Concretely we prove:

  - create_skill / import_skill now write a DDB row keyed on workspace_id
  - update_skill / delete_skill refuse skills owned by another workspace
    (returning the same "not found in this workspace" message as agent
    scoping so ids can't be enumerated)
  - all four refuse when the caller's role is below editor
  - create_skill refuses when no workspace context is in scope

We mock boto3.client/resource directly instead of hitting moto because
the tools share module-level bindings set by main.py (_scope) and are
otherwise ordinary functions — the fakes below are simple enough that
the test stays fast and robust to AWS SDK version drift.
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


from tools import (
    create_skill as _create_mod,
    delete_skill as _delete_mod,
    import_skill as _import_mod,
    update_skill as _update_mod,
)

WS_ID = "ws-alpha"
OTHER_WS = "ws-beta"
USER_ID = "user-test"


# ── In-memory S3 / DDB fakes ─────────────────────────────────────────────


class FakeS3:
    def __init__(self, initial: dict[str, bytes] | None = None):
        self.store: dict[str, bytes] = dict(initial or {})
        self.puts: list[str] = []
        self.deleted: list[str] = []

    def get_object(self, Bucket, Key):
        if Key not in self.store:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": MagicMock(read=lambda: self.store[Key])}

    def put_object(self, Bucket, Key, Body, **_):
        self.store[Key] = Body if isinstance(Body, bytes) else Body.encode("utf-8")
        self.puts.append(Key)

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.store.pop(obj["Key"], None)
            self.deleted.append(obj["Key"])

    def get_paginator(self, _name):
        store = self.store

        class _P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k} for k in store if k.startswith(Prefix)]}

        return _P()


class _CondFail(Exception):
    pass


class FakeSkillsTable:
    def __init__(self, items: list[dict] | None = None):
        self.items: dict[str, dict] = {i["skillId"]: i for i in (items or [])}
        self.put_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.delete_calls: list[dict] = []

        class _Exceptions:
            ConditionalCheckFailedException = _CondFail

        self.meta = MagicMock()
        self.meta.client.exceptions = _Exceptions

    def put_item(self, Item):
        self.items[Item["skillId"]] = Item
        self.put_calls.append(Item)

    def get_item(self, Key):
        item = self.items.get(Key["skillId"])
        return {"Item": item} if item else {}

    def update_item(self, **kwargs):
        key = kwargs["Key"]["skillId"]
        self.update_calls.append(kwargs)
        item = self.items.get(key)
        expr_values = kwargs.get("ExpressionAttributeValues", {})
        # Evaluate the condition: ConditionExpression always ends with
        # "workspace_id = :ws" in our tools; cheap to check manually.
        if not item:
            raise _CondFail("no item")
        if item.get("workspace_id") != expr_values.get(":ws"):
            raise _CondFail("workspace mismatch")
        # Apply SET parts heuristically (enough for our asserts).
        expr = kwargs.get("UpdateExpression", "")
        if "SET" in expr:
            for part in expr.replace("SET", "").split(","):
                lhs, _, rhs = part.strip().partition("=")
                lhs, rhs = lhs.strip(), rhs.strip()
                # Resolve ExpressionAttributeNames (#n → real name)
                names = kwargs.get("ExpressionAttributeNames", {})
                real_lhs = names.get(lhs, lhs)
                if rhs in expr_values:
                    item[real_lhs] = expr_values[rhs]
        return {"Attributes": item}

    def delete_item(self, **kwargs):
        key = kwargs["Key"]["skillId"]
        self.delete_calls.append(kwargs)
        item = self.items.get(key)
        expr_values = kwargs.get("ExpressionAttributeValues", {})
        if not item or item.get("workspace_id") != expr_values.get(":ws"):
            raise _CondFail("workspace mismatch")
        del self.items[key]


def _patches(module, s3: FakeS3, table: FakeSkillsTable, role="editor", workspace=WS_ID):
    """Build a list of patches for the module's boto3 + _scope bindings.

    Patches ``require_role`` / ``current_workspace`` / ``current_caller``
    as bound names inside the tool's own module namespace. This isolates
    the test from other suites (notably test_scope) that delete and
    re-import ``tools._scope`` mid-run — after such a reload, setting
    module globals on the new ``_scope`` has no effect on tools whose
    bindings still point to the old module object.
    """
    ddb_resource = MagicMock()
    ddb_resource.Table = MagicMock(return_value=table)

    def _boto3_client(service, **_):
        if service == "s3":
            return s3
        return MagicMock()

    # Emulate _scope.require_role semantics: empty workspace → "No
    # workspace context"; role below editor → "Permission denied".
    def _fake_require_role(min_role):
        if not workspace:
            return {"error": "No workspace context — refusing to scope this operation."}
        levels = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
        if role is None or levels.get(role, -1) < levels.get(min_role, 99):
            have = role if role else "(not a member)"
            return {
                "error": (
                    f"Permission denied: operation requires '{min_role}' "
                    f"role or higher, you have '{have}'."
                )
            }
        return None

    patches = [
        patch(f"tools.{module}.boto3.client", side_effect=_boto3_client),
        patch(f"tools.{module}.boto3.resource", return_value=ddb_resource),
        patch(f"tools.{module}.require_role", side_effect=_fake_require_role),
        patch(f"tools.{module}.current_workspace", return_value=workspace),
    ]
    # Only create_skill and import_skill import current_caller; patching
    # a missing attribute would raise AttributeError.
    if module in {"create_skill", "import_skill"}:
        patches.append(patch(f"tools.{module}.current_caller", return_value=USER_ID))
    return patches


def _start(patches):
    started = [p.start() for p in patches]
    return started, patches


def _stop(patches):
    for p in patches:
        p.stop()


# ── Tests: create_skill ──────────────────────────────────────────────────


def test_create_skill_writes_ddb_row_with_workspace_id():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("create_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_create_mod.create_skill(
            skill_name="demo",
            description="desc",
            skill_type="prompt",
            instructions="Do X.",
        ))
    finally:
        _stop(patches)

    assert out["status"] == "created"
    assert out["workspace_id"] == WS_ID
    # DDB row has workspace_id, and S3 has SKILL.md
    assert len(table.put_calls) == 1
    item = table.put_calls[0]
    assert item["workspace_id"] == WS_ID
    assert item["name"] == "demo"
    assert item["type"] == "prompt"
    assert item["approved"] is True   # prompt skills auto-approved
    assert item["deleted"] is False
    assert any(k.endswith("/SKILL.md") for k in s3.puts)


def test_create_skill_marks_script_unapproved():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("create_skill", s3, table, role="editor")
    _, patches = _start(patches)
    try:
        _create_mod.create_skill(
            skill_name="run",
            description="d",
            skill_type="script",
            instructions="runs",
            script_code="print('x')",
        )
    finally:
        _stop(patches)
    assert table.put_calls[0]["approved"] is False


def test_create_skill_refuses_viewer_role():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("create_skill", s3, table, role="viewer")
    _, patches = _start(patches)
    try:
        out = json.loads(_create_mod.create_skill("n", "d", "prompt", "i"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Permission denied" in out["error"]
    assert table.put_calls == []    # never wrote DDB
    assert s3.puts == []            # never wrote S3


def test_create_skill_refuses_without_workspace():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("create_skill", s3, table, role="editor", workspace="")
    _, patches = _start(patches)
    try:
        out = json.loads(_create_mod.create_skill("n", "d", "prompt", "i"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "No workspace context" in out["error"]
    assert table.put_calls == []


# ── Tests: update_skill / delete_skill cross-workspace ───────────────────


def _seed_other_ws_skill(table: FakeSkillsTable, s3: FakeS3, skill_id="abcd1234"):
    table.items[skill_id] = {
        "skillId": skill_id,
        "workspace_id": OTHER_WS,
        "name": "stolen",
        "description": "not yours",
        "type": "prompt",
        "source": "natural-language",
        "deleted": False,
    }
    s3.store[f"skills/{skill_id}/SKILL.md"] = b'---\nname: "stolen"\n---\n# body'


def test_update_skill_refuses_cross_workspace():
    s3 = FakeS3()
    table = FakeSkillsTable()
    _seed_other_ws_skill(table, s3)
    patches = _patches("update_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_update_mod.update_skill("abcd1234", skill_name="hijacked"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "not found in this workspace" in out["error"]
    # S3 untouched
    assert s3.puts == []
    assert table.update_calls == []
    # Original DDB row unchanged
    assert table.items["abcd1234"]["name"] == "stolen"


def test_delete_skill_refuses_cross_workspace():
    s3 = FakeS3()
    table = FakeSkillsTable()
    _seed_other_ws_skill(table, s3)
    patches = _patches("delete_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_delete_mod.delete_skill("abcd1234"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "not found in this workspace" in out["error"]
    # S3 files untouched — no delete_objects calls
    assert s3.deleted == []
    assert "abcd1234" in table.items


def test_delete_skill_succeeds_in_own_workspace():
    s3 = FakeS3({
        "skills/own1234/SKILL.md": b"body",
        "skills/own1234/scripts/run.py": b"print(1)",
    })
    table = FakeSkillsTable([{
        "skillId": "own1234",
        "workspace_id": WS_ID,
        "name": "own",
        "type": "script",
        "deleted": False,
    }])
    patches = _patches("delete_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_delete_mod.delete_skill("own1234"))
    finally:
        _stop(patches)
    assert out["status"] == "deleted"
    assert "own1234" not in table.items
    assert "skills/own1234/SKILL.md" in s3.deleted


def test_update_skill_succeeds_in_own_workspace():
    s3 = FakeS3({
        "skills/own1234/SKILL.md": b'---\nname: "own"\ndescription: "d"\ntype: "prompt"\n---\n# body',
    })
    table = FakeSkillsTable([{
        "skillId": "own1234",
        "workspace_id": WS_ID,
        "name": "own",
        "description": "d",
        "type": "prompt",
        "source": "natural-language",
        "deleted": False,
    }])
    patches = _patches("update_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_update_mod.update_skill(
            "own1234", skill_name="renamed", description="new"
        ))
    finally:
        _stop(patches)
    assert out["status"] == "updated"
    assert "name" in out["updated_fields"]
    assert table.items["own1234"]["name"] == "renamed"


# ── Tests: import_skill ──────────────────────────────────────────────────


def test_import_skill_writes_ddb_row_with_workspace_id():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_import_mod.import_skill(
            content='---\nname: "imp"\ndescription: "d"\ntype: "prompt"\n---\n# body',
        ))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    assert out["workspace_id"] == WS_ID
    assert len(table.put_calls) == 1
    assert table.put_calls[0]["workspace_id"] == WS_ID


def test_import_skill_refuses_viewer():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="viewer")
    _, patches = _start(patches)
    try:
        out = json.loads(_import_mod.import_skill(
            content='---\nname: "imp"\ndescription: "d"\n---\n# body',
        ))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Permission denied" in out["error"]
    assert table.put_calls == []
    assert s3.puts == []


# ── Tests: import_skill helpers + URL flows ──────────────────────────────


def test_should_skip_path_filters_noise():
    """_should_skip_path drops VCS/cache/OS cruft but keeps __init__.py."""
    skip = _import_mod._should_skip_path
    # noise should skip
    assert skip("__pycache__/foo.pyc")
    assert skip("__MACOSX/anything")
    assert skip(".git/HEAD")
    assert skip(".github/workflows/x.yml")
    assert skip(".idea/x.iml")
    assert skip("node_modules/lib/index.js")
    assert skip("src/foo.pyc")
    assert skip("src/foo.pyo")
    assert skip(".DS_Store")
    assert skip("")
    assert skip(".dotfile")
    # Windows-style separator
    assert skip("foo\\__pycache__\\bar.pyc")

    # legit files survive
    assert not skip("SKILL.md")
    assert not skip("scripts/__init__.py")
    assert not skip("scripts/run.py")


def test_parse_frontmatter_extracts_name():
    parse = _import_mod._parse_frontmatter

    # Valid
    meta = parse('---\nname: "demo"\ndescription: "d"\n---\n# body')
    assert meta is not None
    assert meta["name"] == "demo"
    assert meta["description"] == "d"

    # Missing leading ---
    assert parse("# body only") is None

    # Only one delimiter
    assert parse("---\nname: x") is None

    # Bad YAML returns None (caught silently)
    assert parse("---\n: : : :\n---\n# body") is None

    # Frontmatter without name → invalid (treated as None)
    assert parse('---\ndescription: "d"\n---\n# body') is None


def test_wrap_with_frontmatter_emits_valid_yaml():
    wrap = _import_mod._wrap_with_frontmatter
    out = wrap("# hello", "demo", "desc here", source="github-file")
    # Re-parse as frontmatter
    meta = _import_mod._parse_frontmatter(out)
    assert meta is not None
    assert meta["name"] == "demo"
    assert meta["description"] == "desc here"
    assert meta["source"] == "github-file"
    assert meta["type"] == "prompt"


def test_import_skill_requires_content_or_url():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_import_mod.import_skill())
    finally:
        _stop(patches)
    assert "error" in out
    assert "No content provided" in out["error"]


def test_import_skill_requires_name_when_no_frontmatter():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_import_mod.import_skill(content="# raw markdown"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "no YAML frontmatter" in out["error"]
    assert "kebab-case" in out["hint"]


def test_import_skill_wraps_raw_content_with_frontmatter():
    """Plain markdown + name → autogenerated frontmatter and SKILL.md saved."""
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_import_mod.import_skill(
            content="# Hello world",
            name="demo-skill",
            description="A demo",
        ))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    assert out["had_frontmatter"] is False
    assert out["files"] == ["SKILL.md"]

    # SKILL.md was written, with frontmatter wrapped in
    md_keys = [k for k in s3.store if k.endswith("SKILL.md")]
    assert len(md_keys) == 1
    body = s3.store[md_keys[0]].decode("utf-8")
    assert body.startswith("---")
    assert "demo-skill" in body
    # DDB row stamped with workspace
    assert table.put_calls[0]["workspace_id"] == WS_ID


def test_import_skill_url_handles_fetch_failure(monkeypatch):
    """URL fetch failure → error JSON, no DDB write."""
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        # Force _http_get to fail
        monkeypatch.setattr(
            _import_mod, "_http_get",
            lambda *a, **kw: (_ for _ in ()).throw(Exception("network down")),
        )
        out = json.loads(_import_mod.import_skill(url="https://example.com/skill.md"))
    finally:
        _stop(patches)

    assert "error" in out
    assert "Failed to fetch" in out["error"]
    assert "network down" in out["error"]
    assert table.put_calls == []


def test_import_skill_url_raw_fetches_and_wraps(monkeypatch):
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        # Raw URL returns markdown without frontmatter; we provide name
        monkeypatch.setattr(_import_mod, "_http_get", lambda *a, **kw: "# Hi")
        out = json.loads(_import_mod.import_skill(
            url="https://example.com/raw.md",
            name="raw-skill",
            description="from raw url",
        ))
    finally:
        _stop(patches)

    assert out["status"] == "imported"
    assert out["source"] == "url"
    assert out["files_count"] == 1
    assert table.put_calls[0]["source"] == "url"


def test_import_skill_clawhub_zip_writes_files(monkeypatch):
    """ClawHub URL → zip extraction → multi-file import path."""
    import io as _io
    import zipfile

    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    # Build an in-memory zip with a SKILL.md (with frontmatter) + a script
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "myskill/SKILL.md",
            '---\nname: "claw-skill"\ndescription: "d"\ntype: "script"\n---\n# body',
        )
        zf.writestr("myskill/scripts/run.py", "print('hi')")
        zf.writestr("myskill/__pycache__/skip.pyc", b"\x00")  # noise — must skip
    zip_bytes = buf.getvalue()

    def fake_http_get(url, accept="*/*", binary=False):
        if binary:
            return zip_bytes
        return ""

    monkeypatch.setattr(_import_mod, "_http_get", fake_http_get)

    try:
        out = json.loads(_import_mod.import_skill(
            url="https://clawhub.ai/author/myskill",
        ))
    finally:
        _stop(patches)

    assert out["status"] == "imported"
    assert out["source"] == "clawhub"
    # Both extracted files plus skipped pyc not present
    assert "SKILL.md" in out["files"]
    assert "scripts/run.py" in out["files"]
    assert "__pycache__/skip.pyc" not in out["files"]
    # script skill auto-marked unapproved
    assert table.put_calls[0]["approved"] is False
    assert table.put_calls[0]["type"] == "script"


def test_import_skill_clawhub_invalid_url_raises_to_error(monkeypatch):
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        # URL that contains "clawhub.ai/" but no slug → ValueError inside _fetch_clawhub
        out = json.loads(_import_mod.import_skill(
            url="https://clawhub.ai/",
        ))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Failed to fetch" in out["error"]


def test_import_skill_clawhub_zip_without_skill_md(monkeypatch):
    """Zip with no SKILL.md → fail loudly."""
    import io as _io
    import zipfile

    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("only/some.txt", "content")
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(
        _import_mod, "_http_get",
        lambda *a, **kw: zip_bytes if kw.get("binary") else "",
    )

    try:
        out = json.loads(_import_mod.import_skill(url="https://clawhub.ai/a/b"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "No SKILL.md found" in out["error"]


def test_import_skill_github_file_url(monkeypatch):
    """Single GitHub blob URL → fetched as raw, treated as one-file content."""
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    raw_md = '---\nname: "gh-file"\ndescription: "from gh"\n---\n# body'
    monkeypatch.setattr(_import_mod, "_http_get", lambda *a, **kw: raw_md)

    try:
        out = json.loads(_import_mod.import_skill(
            url="https://github.com/owner/repo/blob/main/SKILL.md",
        ))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    assert out["source"] == "github-file"


def test_import_skill_github_dir_url(monkeypatch):
    """GitHub directory URL → recursive contents API → multi-file import."""
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    skill_md = '---\nname: "gh-dir"\ndescription: "d"\n---\n# body'

    def fake_http_get(url, accept="*/*", binary=False):
        # First call is the contents API, subsequent calls are raw download URLs
        if "api.github.com" in url:
            return json.dumps([
                {
                    "type": "file",
                    "path": "skills/myskill/SKILL.md",
                    "download_url": "https://raw/SKILL.md",
                },
            ])
        # raw download
        return skill_md

    monkeypatch.setattr(_import_mod, "_http_get", fake_http_get)

    try:
        out = json.loads(_import_mod.import_skill(
            url="https://github.com/owner/repo/tree/main/skills/myskill",
        ))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    assert out["source"] == "github-dir"
    assert "SKILL.md" in out["files"]


def test_write_skill_files_strips_unsafe_paths():
    """_write_skill_files sanitizes paths and rejects traversal."""
    s3 = FakeS3()
    written = _import_mod._write_skill_files(s3, "abc1234", {
        "SKILL.md": "body",
        "scripts/run.py": "print(1)",
        "../escape.txt": "bad",  # must be skipped
    })
    # SKILL.md not in written list (it's special-cased), only extras
    assert "scripts/run.py" in written
    assert "SKILL.md" not in written
    # No traversal slipped through to S3
    assert all(".." not in k for k in s3.store.keys())


# ── Additional update_skill / delete_skill / import_skill branch coverage ──


def test_update_skill_refuses_viewer_role():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("update_skill", s3, table, role="viewer")
    _, patches = _start(patches)
    try:
        out = json.loads(_update_mod.update_skill("nope", skill_name="x"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Permission denied" in out["error"]


def test_update_skill_returns_error_when_get_skill_raises():
    """_get_skill swallows DDB errors → returns None → update_skill returns 'not found'."""
    s3 = FakeS3()

    class _BadTable(FakeSkillsTable):
        def get_item(self, Key):
            raise RuntimeError("ddb dropped the call")

    table = _BadTable()
    patches = _patches("update_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_update_mod.update_skill("ghost"))
    finally:
        _stop(patches)
    # _get_skill catches the exception and returns None, so the tool says not found
    assert "error" in out
    assert "not found" in out["error"]


def test_update_skill_errors_when_skill_md_missing():
    """update_skill should return an error if S3 has no SKILL.md."""
    s3 = FakeS3()  # no SKILL.md seeded
    table = FakeSkillsTable([{
        "skillId": "ownmissing",
        "workspace_id": WS_ID,
        "name": "missing",
        "description": "d",
        "type": "prompt",
        "source": "natural-language",
        "deleted": False,
    }])
    patches = _patches("update_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_update_mod.update_skill("ownmissing", description="x"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Skill file not found" in out["error"]


def test_update_skill_instructions_only_replaces_body_only():
    """instructions= should rewrite the body but not change name/description."""
    s3 = FakeS3({
        "skills/own1234/SKILL.md": (
            b'---\nname: "own"\ndescription: "d"\ntype: "prompt"\n'
            b'source: "natural-language"\n---\n# old body'
        ),
    })
    table = FakeSkillsTable([{
        "skillId": "own1234",
        "workspace_id": WS_ID,
        "name": "own",
        "description": "d",
        "type": "prompt",
        "source": "natural-language",
        "deleted": False,
    }])
    patches = _patches("update_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_update_mod.update_skill(
            "own1234", instructions="# brand new body",
        ))
    finally:
        _stop(patches)
    assert out["status"] == "updated"
    assert out["updated_fields"] == ["instructions"]
    new_md = s3.store["skills/own1234/SKILL.md"].decode("utf-8")
    assert "# brand new body" in new_md
    assert "# old body" not in new_md


def test_update_skill_ddb_failure_returns_error():
    """If DDB update fails, update_skill returns the error JSON."""
    s3 = FakeS3({
        "skills/own1234/SKILL.md": (
            b'---\nname: "own"\ndescription: "d"\ntype: "prompt"\n---\n# body'
        ),
    })

    class _BadTable(FakeSkillsTable):
        def update_item(self, **kwargs):
            raise RuntimeError("ddb update boom")

    table = _BadTable([{
        "skillId": "own1234",
        "workspace_id": WS_ID,
        "name": "own",
        "description": "d",
        "type": "prompt",
        "source": "natural-language",
        "deleted": False,
    }])
    patches = _patches("update_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_update_mod.update_skill("own1234", skill_name="renamed"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Skill metadata update failed" in out["error"]


# ── delete_skill additional branches ──────────────────────────────────────


def test_delete_skill_refuses_viewer_role():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("delete_skill", s3, table, role="viewer")
    _, patches = _start(patches)
    try:
        out = json.loads(_delete_mod.delete_skill("any"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Permission denied" in out["error"]


def test_delete_skill_handles_get_item_failure():
    """If DDB get_item raises, delete_skill returns Skill lookup failed."""
    s3 = FakeS3()

    class _BadTable(FakeSkillsTable):
        def get_item(self, Key):
            raise RuntimeError("ddb is down")

    table = _BadTable()
    patches = _patches("delete_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_delete_mod.delete_skill("any"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Skill lookup failed" in out["error"]


def test_delete_skill_handles_s3_failure():
    """S3 delete failure surfaces an error (DDB row stays intact)."""
    class _BadS3(FakeS3):
        def delete_objects(self, Bucket, Delete):
            raise RuntimeError("s3 blew up")

    s3 = _BadS3({"skills/own1234/SKILL.md": b"body"})
    table = FakeSkillsTable([{
        "skillId": "own1234",
        "workspace_id": WS_ID,
        "name": "own",
        "type": "prompt",
        "deleted": False,
    }])
    patches = _patches("delete_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_delete_mod.delete_skill("own1234"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Failed to delete skill files" in out["error"]
    # DDB row not deleted
    assert "own1234" in table.items


def test_delete_skill_handles_ddb_delete_unexpected_failure():
    """When DDB delete_item raises a non-conditional error."""
    class _AngryTable(FakeSkillsTable):
        def delete_item(self, **kwargs):
            raise RuntimeError("kaboom")

    s3 = FakeS3({"skills/own1234/SKILL.md": b"body"})
    table = _AngryTable([{
        "skillId": "own1234",
        "workspace_id": WS_ID,
        "name": "own",
        "type": "prompt",
        "deleted": False,
    }])
    patches = _patches("delete_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_delete_mod.delete_skill("own1234"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Skill metadata delete failed" in out["error"]


# ── import_skill: additional helpers + branches ────────────────────────────


def test_import_skill_no_workspace_returns_error():
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace="")
    _, patches = _start(patches)
    try:
        out = json.loads(_import_mod.import_skill(content="# x", name="y", description="d"))
    finally:
        _stop(patches)
    assert "error" in out
    assert "No workspace context" in out["error"]


def test_import_skill_clawhub_non_text_files_skipped(monkeypatch):
    """Binary entries in the zip must be skipped (UnicodeDecodeError path)."""
    import io as _io
    import zipfile

    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill/SKILL.md",
            '---\nname: "binclaw"\ndescription: "d"\n---\n# body',
        )
        # A binary blob — utf-8 decode raises
        zf.writestr("skill/asset.bin", b"\xff\xfe\x00\x01")
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(
        _import_mod, "_http_get",
        lambda *a, **kw: zip_bytes if kw.get("binary") else "",
    )

    try:
        out = json.loads(_import_mod.import_skill(url="https://clawhub.ai/auth/skill"))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    # Binary file is not present in the file list
    assert "asset.bin" not in out["files"]


def test_import_skill_clawhub_empty_zip_raises_clean_error(monkeypatch):
    """Zip with only directories or only filtered noise → ValueError → error JSON."""
    import io as _io
    import zipfile

    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Directory entry only — info.is_dir() True
        zf.writestr("skill/", "")
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(
        _import_mod, "_http_get",
        lambda *a, **kw: zip_bytes if kw.get("binary") else "",
    )
    try:
        out = json.loads(_import_mod.import_skill(url="https://clawhub.ai/a/b"))
    finally:
        _stop(patches)
    assert "error" in out
    # _fetch_clawhub raises "ClawHub zip is empty or contains no readable files"
    assert "Failed to fetch" in out["error"]


def test_import_skill_metadata_write_failure_surfaces(monkeypatch):
    """If DDB put_item fails after S3 was written, return clear error."""
    s3 = FakeS3()
    class _AngryTable(FakeSkillsTable):
        def put_item(self, Item):
            raise RuntimeError("ddb 5xx")
    table = _AngryTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)
    try:
        out = json.loads(_import_mod.import_skill(
            content='---\nname: "imp"\ndescription: "d"\n---\n# body',
        ))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Skill metadata write failed" in out["error"]


def test_import_skill_reads_skill_md_from_subdirectory(monkeypatch):
    """Multi-file import where SKILL.md lives in a subdir (not top-level)."""
    import io as _io
    import zipfile

    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Make sure no plain "SKILL.md" exists at the top, but it's in a
        # nested directory after the leading-dir-strip — force the zip to
        # have multiple top-level dirs so the strip doesn't remove the prefix.
        zf.writestr(
            "skill/extra/SKILL.md",
            '---\nname: "nested-skill"\ndescription: "d"\n---\n# body',
        )
        zf.writestr("other/asset.txt", "hi")
    zip_bytes = buf.getvalue()

    monkeypatch.setattr(
        _import_mod, "_http_get",
        lambda *a, **kw: zip_bytes if kw.get("binary") else "",
    )

    try:
        out = json.loads(_import_mod.import_skill(url="https://clawhub.ai/x/y"))
    finally:
        _stop(patches)
    # Either it found SKILL.md in the strip or in nested location — both
    # branches mean "imported".
    assert out["status"] == "imported"


def test_import_skill_github_dir_recurses_into_subdirs(monkeypatch):
    """GitHub dir with subdir → recursion path covered."""
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    skill_md = '---\nname: "gh-deep"\ndescription: "d"\n---\n# body'
    api_calls = {"top": 0, "sub": 0}

    def fake_http_get(url, accept="*/*", binary=False):
        if "api.github.com" in url and "subdir" in url:
            api_calls["sub"] += 1
            return json.dumps([
                {
                    "type": "file",
                    "path": "skills/myskill/subdir/SKILL.md",
                    "download_url": "https://raw/SKILL.md",
                }
            ])
        if "api.github.com" in url:
            api_calls["top"] += 1
            return json.dumps([
                {
                    "type": "dir",
                    "path": "skills/myskill/subdir",
                    "download_url": None,
                }
            ])
        return skill_md

    monkeypatch.setattr(_import_mod, "_http_get", fake_http_get)

    try:
        out = json.loads(_import_mod.import_skill(
            url="https://github.com/o/r/tree/main/skills/myskill",
        ))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    assert api_calls["sub"] >= 1


def test_import_skill_github_file_url_invalid(monkeypatch):
    """A 'github.com/.../blob/' URL with bad shape → _fetch_github_file raises."""
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    monkeypatch.setattr(
        _import_mod, "_http_get",
        lambda *a, **kw: (_ for _ in ()).throw(Exception("network")),
    )

    try:
        # /blob/ but malformed — _fetch_github_file's regex needs branch + path
        out = json.loads(_import_mod.import_skill(
            url="https://github.com/owner/repo/blob/missing",
        ))
    finally:
        _stop(patches)
    assert "error" in out
    assert "Failed to fetch" in out["error"]


def test_import_skill_raw_url_fetches_text(monkeypatch):
    """Plain https URL → source_type='url'; happy path with frontmatter."""
    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    body = '---\nname: "rawurl-skill"\ndescription: "d"\n---\n# raw'
    monkeypatch.setattr(_import_mod, "_http_get", lambda *a, **kw: body)
    try:
        out = json.loads(_import_mod.import_skill(url="https://example.org/some.md"))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    assert out["source"] == "url"


def test_import_skill_clawhub_zip_with_only_subdir_strip(monkeypatch):
    """Zip with one shared parent dir gets stripped."""
    import io as _io
    import zipfile

    s3 = FakeS3()
    table = FakeSkillsTable()
    patches = _patches("import_skill", s3, table, role="editor", workspace=WS_ID)
    _, patches = _start(patches)

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill_pkg/SKILL.md",
            '---\nname: "wrap"\ndescription: "d"\n---\n# body',
        )
    zip_bytes = buf.getvalue()
    monkeypatch.setattr(
        _import_mod, "_http_get",
        lambda *a, **kw: zip_bytes if kw.get("binary") else "",
    )
    try:
        out = json.loads(_import_mod.import_skill(url="https://clawhub.ai/a/b"))
    finally:
        _stop(patches)
    assert out["status"] == "imported"
    assert "SKILL.md" in out["files"]


def test_should_skip_path_explicit_branches():
    skip = _import_mod._should_skip_path
    # branch: __MACOSX
    assert skip("project/__MACOSX/foo")
    # branch: leaf .pyc handled even when nested in valid path
    assert skip("legit/dir/junk.pyc")
    # blank should skip
    assert skip("")

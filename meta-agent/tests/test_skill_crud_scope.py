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
        "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/sub-agent",
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


from tools import create_skill as _create_mod  # noqa: E402
from tools import update_skill as _update_mod  # noqa: E402
from tools import delete_skill as _delete_mod  # noqa: E402
from tools import import_skill as _import_mod  # noqa: E402


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

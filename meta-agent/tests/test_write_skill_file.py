"""Tests for write_skill_file / delete_skill_file.

Regression cover for the gap that left Meta-Agent unable to edit any
file except SKILL.md — the edit-assistant would loop on read_skill_file
without a write path, and users saw the optimize-skill flow stall
forever. See the feishu-mcp-operator transcript where the assistant
tried to "optimize script.py" five times and always fell back to
re-explaining its plan.

Mocks boto3 + _skill_in_workspace directly (same style as
test_skill_crud_scope) so the tests stay fast and don't need real AWS.
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch


_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "TOOLS_TABLE": "agent-studio-tools",
    "ACCOUNT_ID": "000000000000",
    "AGENT_ROLE_ARN": "arn:aws:iam::000:role/r",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
    "BASE_DEPLOYMENT_KEY": "k",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "k",
    "MODEL_ID": "mock",
    "MCP_GATEWAY_URL": "",
    "SCHEDULER_TARGET_ROLE_ARN": "arn:aws:iam::000:role/sched",
    "PERMISSION_TIER_ROLES": {"readonly": "arn:aws:iam::000:role/r"},
    "DEFAULT_PERMISSION_TIER": "readonly",
    "CODE_INTERPRETER_ID": "",
    "BROWSER_ID": "",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


from tools import write_skill_file as _wsf  # noqa: E402


WS = "ws-alpha"
OTHER_WS = "ws-beta"


class FakeS3:
    def __init__(self):
        self.puts: list[dict] = []
        self.deletes: list[dict] = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType})

    def delete_object(self, Bucket, Key):
        self.deletes.append({"Bucket": Bucket, "Key": Key})


class FakeSkillsTable:
    def __init__(self, item: dict | None):
        self.item = item
        self.updates: list[dict] = []

    def get_item(self, Key, **_):
        return {"Item": self.item} if self.item else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


def _patches(s3: FakeS3, table: FakeSkillsTable, *, role="editor", workspace=WS):
    ddb_resource = MagicMock()
    ddb_resource.Table = MagicMock(return_value=table)

    def _client(service, **_):
        if service == "s3":
            return s3
        return MagicMock()

    def _fake_require_role(min_role):
        if not workspace:
            return {"error": "No workspace context — refusing to scope this operation."}
        levels = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
        if role is None or levels.get(role, -1) < levels.get(min_role, 99):
            have = role if role else "(not a member)"
            return {"error": f"Permission denied: requires '{min_role}', have '{have}'."}
        return None

    return [
        patch("tools.write_skill_file.boto3.client", side_effect=_client),
        patch("tools.write_skill_file.boto3.resource", return_value=ddb_resource),
        patch("tools.write_skill_file.require_role", side_effect=_fake_require_role),
        patch("tools.write_skill_file.current_workspace", return_value=workspace),
    ]


def _start(ps):
    [p.start() for p in ps]
    return ps


def _stop(ps):
    for p in ps:
        p.stop()


def _skill(ws=WS, skill_id="skid-1", deleted=False):
    return {"skillId": skill_id, "workspace_id": ws, "deleted": deleted, "name": "foo"}


# ── write_skill_file ──


def test_write_writes_to_s3_with_correct_key_and_content_type():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "script.py", "print('hi')"))
    finally:
        _stop(ps)
    assert out["status"] == "written"
    assert out["bytes"] == len("print('hi')")
    assert len(s3.puts) == 1
    put = s3.puts[0]
    assert put["Bucket"] == "test-bucket"
    assert put["Key"] == "skills/skid-1/script.py"
    assert put["ContentType"] == "text/x-python"
    # updated_at bumped
    assert len(tbl.updates) == 1


def test_write_refuses_cross_workspace():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill(ws=OTHER_WS))
    ps = _start(_patches(s3, tbl, workspace=WS))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "script.py", "x"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "not found in this workspace" in out["error"]
    assert s3.puts == []
    assert tbl.updates == []


def test_write_refuses_viewer_role():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl, role="viewer"))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "script.py", "x"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "Permission denied" in out["error"]
    assert s3.puts == []


def test_write_rejects_path_traversal():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "../../etc/passwd", "x"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "traversal" in out["error"].lower()
    assert s3.puts == []


def test_write_rejects_absolute_path():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "/etc/shadow", "x"))
    finally:
        _stop(ps)
    assert "error" in out
    assert s3.puts == []


def test_write_refuses_oversize_content():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        huge = "a" * (1_000_001)
        out = json.loads(_wsf.write_skill_file("skid-1", "big.py", huge))
    finally:
        _stop(ps)
    assert "error" in out
    assert "exceeds" in out["error"]
    assert s3.puts == []


def test_write_refuses_on_deleted_skill():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill(deleted=True))
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "script.py", "x"))
    finally:
        _stop(ps)
    assert "error" in out
    assert s3.puts == []


def test_write_content_type_varies_by_extension():
    tbl = FakeSkillsTable(_skill())
    cases = [
        ("SKILL.md", "text/markdown"),
        ("script.py", "text/x-python"),
        ("config.json", "application/json"),
        ("run.sh", "text/x-shellscript"),
        ("note.txt", "text/plain"),
        ("assets/image.bin", "text/plain"),  # unknown → plain
    ]
    for path, expected in cases:
        s3 = FakeS3()
        ps = _start(_patches(s3, tbl))
        try:
            _wsf.write_skill_file("skid-1", path, "x")
        finally:
            _stop(ps)
        assert s3.puts[0]["ContentType"] == expected, path


# ── delete_skill_file ──


def test_delete_removes_s3_object():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.delete_skill_file("skid-1", "scripts/obsolete.py"))
    finally:
        _stop(ps)
    assert out["status"] == "deleted"
    assert len(s3.deletes) == 1
    assert s3.deletes[0]["Key"] == "skills/skid-1/scripts/obsolete.py"


def test_delete_refuses_skill_md():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.delete_skill_file("skid-1", "SKILL.md"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "delete_skill" in out["error"]  # points user to the right tool
    assert s3.deletes == []


def test_delete_refuses_cross_workspace():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill(ws=OTHER_WS))
    ps = _start(_patches(s3, tbl, workspace=WS))
    try:
        out = json.loads(_wsf.delete_skill_file("skid-1", "scripts/old.py"))
    finally:
        _stop(ps)
    assert "error" in out
    assert s3.deletes == []


# ── Path validation branches ──


def test_validate_path_helper_rejects_empty():
    assert _wsf._validate_path("") == "path is required"


def test_validate_path_helper_rejects_control_chars():
    assert _wsf._validate_path("foo\x00bar") is not None


def test_validate_path_helper_rejects_double_slash():
    err = _wsf._validate_path("a//b")
    assert err is not None
    assert "double-slash" in err


def test_validate_path_helper_rejects_too_long():
    err = _wsf._validate_path("a" * 513)
    assert err is not None
    assert "too long" in err


# ── _skill_in_workspace branches ──


def test_skill_in_workspace_rejects_empty_id():
    item, err = _wsf._skill_in_workspace("")
    assert item is None
    assert err == "skill_id is required"


def test_skill_in_workspace_rejects_no_workspace_context(monkeypatch):
    monkeypatch.setattr(_wsf, "current_workspace", lambda: "")
    item, err = _wsf._skill_in_workspace("anything")
    assert item is None
    assert "No workspace context" in err


def test_skill_in_workspace_handles_ddb_failure(monkeypatch):
    """If boto3.resource(...).Table(...).get_item raises, return clean error."""
    fake_resource = MagicMock()
    fake_resource.Table.side_effect = RuntimeError("ddb dropped")
    monkeypatch.setattr(_wsf.boto3, "resource", lambda *a, **kw: fake_resource)
    monkeypatch.setattr(_wsf, "current_workspace", lambda: WS)
    item, err = _wsf._skill_in_workspace("skid-1")
    assert item is None
    assert "Skill lookup failed" in err


# ── Write-side error branches ──


def test_write_no_workspace_context():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl, workspace=""))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "x.py", "data"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "No workspace context" in out["error"]


def test_write_rejects_non_string_content():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        # type: ignore[arg-type] — feeding non-str on purpose
        out = json.loads(_wsf.write_skill_file("skid-1", "a.py", 12345))  # type: ignore
    finally:
        _stop(ps)
    assert "error" in out
    assert "string" in out["error"]


def test_write_handles_s3_failure():
    """When put_object raises, the tool returns 'S3 write failed: ...'."""
    class _BadS3(FakeS3):
        def put_object(self, Bucket, Key, Body, ContentType):
            raise RuntimeError("s3 boom")

    s3 = _BadS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.write_skill_file("skid-1", "x.py", "ok"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "S3 write failed" in out["error"]


def test_write_touch_updated_at_swallows_exceptions(monkeypatch):
    """A DDB update failure during _touch_updated_at must NOT surface — best effort."""
    s3 = FakeS3()

    class _BadTbl(FakeSkillsTable):
        def update_item(self, **kwargs):
            raise RuntimeError("ddb update fail")

    tbl = _BadTbl(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        # Tool should still return success since S3 write succeeded
        out = json.loads(_wsf.write_skill_file("skid-1", "x.py", "ok"))
    finally:
        _stop(ps)
    assert out["status"] == "written"


def test_delete_no_workspace_context():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl, workspace=""))
    try:
        out = json.loads(_wsf.delete_skill_file("skid-1", "x.py"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "No workspace context" in out["error"]


def test_delete_rejects_invalid_path():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.delete_skill_file("skid-1", "../escape.txt"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "traversal" in out["error"].lower()


def test_delete_rejects_viewer_role():
    s3 = FakeS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl, role="viewer"))
    try:
        out = json.loads(_wsf.delete_skill_file("skid-1", "scripts/x.py"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "Permission denied" in out["error"]


def test_delete_handles_s3_failure():
    """When delete_object raises, the tool returns clean error JSON."""
    class _BadS3(FakeS3):
        def delete_object(self, Bucket, Key):
            raise RuntimeError("s3 boom")

    s3 = _BadS3()
    tbl = FakeSkillsTable(_skill())
    ps = _start(_patches(s3, tbl))
    try:
        out = json.loads(_wsf.delete_skill_file("skid-1", "scripts/x.py"))
    finally:
        _stop(ps)
    assert "error" in out
    assert "S3 delete failed" in out["error"]

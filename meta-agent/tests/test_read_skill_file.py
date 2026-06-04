"""Tests for list_skill_files and read_skill_file tools.

Coverage focuses on:
  - workspace scoping (no cross-workspace reads, no path traversal)
  - viewer-role gate
  - happy paths for list + read
  - 200KB truncation behavior on large files
"""
import json
import sys
import types
from unittest.mock import MagicMock

# ── Module stubs so `from strands import tool` works in-process ───────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


WS_ID = "ws-alpha"
OTHER_WS = "ws-beta"


# ── Helpers ───────────────────────────────────────────────────────────────
class _FakeSkillsTable:
    def __init__(self, items: dict):
        self._items = items  # skill_id → DDB Item dict

    def get_item(self, Key):
        item = self._items.get(Key.get("skillId"))
        return {"Item": item} if item else {}


class _FakeS3:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        # Rich exceptions namespace? not needed for these tests.

    def get_paginator(self, _name):
        files = self.files

        class _P:
            def paginate(self, Bucket, Prefix):
                contents = [
                    {"Key": k, "Size": len(v)}
                    for k, v in files.items() if k.startswith(Prefix)
                ]
                yield {"Contents": contents}

        return _P()

    def head_object(self, Bucket, Key):
        if Key not in self.files:
            raise Exception("NoSuchKey")
        return {"ContentLength": len(self.files[Key])}

    def get_object(self, Bucket, Key, Range=None):
        if Key not in self.files:
            raise Exception("NoSuchKey")
        body = self.files[Key]
        if Range:
            # "bytes=0-N" parser; only used here when truncating
            end = int(Range.split("-")[1])
            body = body[: end + 1]
        return {"Body": MagicMock(read=lambda: body)}


def _patch_module(monkeypatch, *, role="viewer", workspace=WS_ID,
                  skills_table=None, s3=None):
    """Wire the module's boto3 + scope helpers to in-memory fakes."""
    from tools import read_skill_file as mod

    ddb_resource = MagicMock()
    ddb_resource.Table = MagicMock(return_value=skills_table or _FakeSkillsTable({}))

    def _boto3_client(service, **_):
        if service == "s3":
            return s3 or _FakeS3({})
        return MagicMock()

    monkeypatch.setattr(mod.boto3, "client", _boto3_client)
    monkeypatch.setattr(mod.boto3, "resource", lambda *a, **kw: ddb_resource)
    monkeypatch.setattr(mod, "current_workspace", lambda: workspace)

    def _fake_require_role(min_role):
        if not workspace:
            return {"error": "No workspace context"}
        levels = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
        if role is None or levels.get(role, -1) < levels.get(min_role, 99):
            return {"error": "forbidden"}
        return None

    monkeypatch.setattr(mod, "require_role", _fake_require_role)


# ── list_skill_files tests ───────────────────────────────────────────────


def test_list_skill_files_returns_files(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": False},
    })
    s3 = _FakeS3({
        "skills/s-1/SKILL.md": b"# body",
        "skills/s-1/scripts/run.py": b"print('x')",
    })
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.list_skill_files("s-1"))
    assert out["skill_id"] == "s-1"
    paths = [f["path"] for f in out["files"]]
    assert "SKILL.md" in paths
    assert "scripts/run.py" in paths


def test_list_skill_files_refuses_cross_workspace(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-x": {"skillId": "s-x", "workspace_id": OTHER_WS, "deleted": False},
    })
    s3 = _FakeS3({"skills/s-x/SKILL.md": b"# body"})
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.list_skill_files("s-x"))
    assert "error" in out
    assert "not found in this workspace" in out["error"]


def test_list_skill_files_refuses_when_deleted(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": True},
    })
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID, skills_table=table)
    out = json.loads(mod.list_skill_files("s-1"))
    assert "error" in out


def test_list_skill_files_refuses_no_role(monkeypatch):
    from tools import read_skill_file as mod

    _patch_module(monkeypatch, role=None, workspace=WS_ID)
    out = json.loads(mod.list_skill_files("s-1"))
    assert out["error"] == "forbidden"


def test_list_skill_files_returns_empty_for_no_files(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-empty": {"skillId": "s-empty", "workspace_id": WS_ID, "deleted": False},
    })
    s3 = _FakeS3({})
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.list_skill_files("s-empty"))
    assert out["files"] == []


def test_list_skill_files_handles_s3_failure(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": False},
    })

    class _BrokenS3:
        def get_paginator(self, _name):
            class _P:
                def paginate(self, **kw):
                    raise RuntimeError("boom")
            return _P()

    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=_BrokenS3())
    out = json.loads(mod.list_skill_files("s-1"))
    assert "error" in out
    assert "S3 list failed" in out["error"]


# ── read_skill_file tests ────────────────────────────────────────────────


def test_read_skill_file_returns_content(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": False},
    })
    s3 = _FakeS3({"skills/s-1/SKILL.md": b"# Hello"})
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.read_skill_file("s-1", "SKILL.md"))
    assert out["skill_id"] == "s-1"
    assert out["path"] == "SKILL.md"
    assert out["content"] == "# Hello"
    assert out["truncated"] is False
    assert out["size"] == 7


def test_read_skill_file_rejects_path_traversal(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": False},
    })
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID, skills_table=table)

    for bad in ("/abs/path", "", "..", "scripts/../escape", "../sibling"):
        out = json.loads(mod.read_skill_file("s-1", bad))
        assert "error" in out, f"path {bad!r} should be rejected"
        assert "Invalid path" in out["error"]


def test_read_skill_file_returns_not_found_for_missing(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": False},
    })
    s3 = _FakeS3({})
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.read_skill_file("s-1", "nope.md"))
    assert "error" in out
    assert "File not found" in out["error"]


def test_read_skill_file_truncates_large_files(monkeypatch):
    from tools import read_skill_file as mod

    big = b"a" * (mod._MAX_FILE_BYTES + 1000)
    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": False},
    })
    s3 = _FakeS3({"skills/s-1/big.txt": big})
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.read_skill_file("s-1", "big.txt"))
    assert out["truncated"] is True
    assert out["size"] == len(big)
    # Content is truncated to _MAX_FILE_BYTES
    assert len(out["content"]) == mod._MAX_FILE_BYTES


def test_read_skill_file_rejects_binary(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-1": {"skillId": "s-1", "workspace_id": WS_ID, "deleted": False},
    })
    s3 = _FakeS3({"skills/s-1/img.bin": b"\xff\xfe\xfd\xfc"})
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.read_skill_file("s-1", "img.bin"))
    assert "error" in out
    assert "not UTF-8" in out["error"]


def test_read_skill_file_refuses_cross_workspace(monkeypatch):
    from tools import read_skill_file as mod

    table = _FakeSkillsTable({
        "s-x": {"skillId": "s-x", "workspace_id": OTHER_WS, "deleted": False},
    })
    s3 = _FakeS3({"skills/s-x/SKILL.md": b"# secret"})
    _patch_module(monkeypatch, role="viewer", workspace=WS_ID,
                  skills_table=table, s3=s3)

    out = json.loads(mod.read_skill_file("s-x", "SKILL.md"))
    assert "error" in out
    assert "not found in this workspace" in out["error"]


def test_read_skill_file_refuses_no_role(monkeypatch):
    from tools import read_skill_file as mod

    _patch_module(monkeypatch, role=None, workspace=WS_ID)
    out = json.loads(mod.read_skill_file("s-1", "SKILL.md"))
    assert out["error"] == "forbidden"


def test_skill_in_workspace_returns_false_when_no_workspace(monkeypatch):
    """Module helper: empty current_workspace blocks lookup entirely."""
    from tools import read_skill_file as mod

    monkeypatch.setattr(mod, "current_workspace", lambda: "")
    assert mod._skill_in_workspace("s-1") is False


def test_skill_in_workspace_returns_false_for_blank_id(monkeypatch):
    """Empty skill_id short-circuits."""
    from tools import read_skill_file as mod

    monkeypatch.setattr(mod, "current_workspace", lambda: WS_ID)
    assert mod._skill_in_workspace("") is False


def test_skill_in_workspace_handles_ddb_failure(monkeypatch):
    """DDB exception → False (don't leak existence)."""
    from tools import read_skill_file as mod

    monkeypatch.setattr(mod, "current_workspace", lambda: WS_ID)

    class _Broken:
        def Table(self, name):
            class _T:
                def get_item(self, **kw):
                    raise RuntimeError("ddb down")
            return _T()

    monkeypatch.setattr(mod.boto3, "resource", lambda *a, **kw: _Broken())
    assert mod._skill_in_workspace("s-1") is False

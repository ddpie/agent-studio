"""Tests for get_agent_detail — slim metadata view of a deployed agent."""
import json
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ── Module stubs ───────────────────────────────────────────────────────────
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

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-1", raising=False)


def _grant_membership(monkeypatch, role="viewer", agent_workspace="ws-1"):
    """Patch _scope so ensure_agent_in_workspace passes for given role."""
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": role}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    agents_table = MagicMock()
    agents_table.get_item.return_value = {
        "Item": {"agentId": "a-1", "workspace_id": agent_workspace},
    }
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)


# ── Helper unit tests ─────────────────────────────────────────────────────

def test_iso_utc_handles_none_and_empty():
    from tools.get_agent_detail import _iso_utc
    assert _iso_utc(None) == ""
    assert _iso_utc("") == ""


def test_iso_utc_naive_datetime_treated_as_utc():
    from tools.get_agent_detail import _iso_utc
    dt = datetime(2024, 1, 1, 12, 0, 0)  # naive
    out = _iso_utc(dt)
    assert "+00:00" in out


def test_iso_utc_passes_through_strings():
    from tools.get_agent_detail import _iso_utc
    assert _iso_utc("2024-01-01T00:00:00Z") == "2024-01-01T00:00:00Z"


def test_compact_tool_definitions_empty_returns_list():
    from tools.get_agent_detail import _compact_tool_definitions
    assert _compact_tool_definitions("") == []
    assert _compact_tool_definitions("  \n") == []


def test_compact_tool_definitions_finds_tool_decorated():
    from tools.get_agent_detail import _compact_tool_definitions
    src = (
        "from strands import tool\n"
        "@tool\n"
        "def greet(name: str = '') -> str:\n"
        "    \"\"\"Say hello.\n\n    More stuff.\"\"\"\n"
        "    return name\n"
    )
    out = _compact_tool_definitions(src)
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["name"] == "greet"
    assert "name" in out[0]["signature"]
    assert "->" in out[0]["signature"]
    assert out[0]["summary"] == "Say hello."


def test_compact_tool_definitions_skips_non_tool():
    from tools.get_agent_detail import _compact_tool_definitions
    src = "def helper(x):\n    return x\n"
    out = _compact_tool_definitions(src)
    assert out == []


def test_compact_tool_definitions_handles_syntax_error():
    from tools.get_agent_detail import _compact_tool_definitions
    src = "def broken(:\n    pass"
    out = _compact_tool_definitions(src)
    assert isinstance(out, dict)
    assert "_parse_error" in out
    assert "raw_truncated" in out
    assert "raw_size_bytes" in out
    # truncated, not full source
    assert len(out["raw_truncated"]) <= len(src)


def test_compact_tool_definitions_picks_attribute_decorator():
    from tools.get_agent_detail import _compact_tool_definitions
    src = (
        "import strands\n"
        "@strands.tool\n"
        "def via_attr() -> str:\n"
        "    \"\"\"Doc.\"\"\"\n"
        "    return ''\n"
    )
    out = _compact_tool_definitions(src)
    assert len(out) == 1
    assert out[0]["name"] == "via_attr"


def test_compact_tool_definitions_async_function():
    from tools.get_agent_detail import _compact_tool_definitions
    src = (
        "@tool\n"
        "async def alfa() -> str:\n"
        "    \"\"\"async tool.\"\"\"\n"
        "    return ''\n"
    )
    out = _compact_tool_definitions(src)
    assert len(out) == 1
    assert out[0]["name"] == "alfa"


def test_slim_skill_collapses_files_to_count():
    from tools.get_agent_detail import _slim_skill
    skill = {
        "name": "ppt",
        "files": ["scripts/a.py", "scripts/b.py", "scripts/c.py", "scripts/d.py"],
    }
    out = _slim_skill(skill)
    assert "files" not in out
    assert out["file_count"] == 4
    assert len(out["files_preview"]) == 3


def test_slim_skill_passes_through_non_dict():
    from tools.get_agent_detail import _slim_skill
    assert _slim_skill("just-a-string") == "just-a-string"


def test_slim_metadata_collapses_skills_and_tools():
    from tools.get_agent_detail import _slim_metadata
    metadata = {
        "system_prompt": "Be helpful.",
        "skills": [
            {"name": "s1", "files": ["a", "b"]},
        ],
        "tool_definitions": "@tool\ndef foo() -> str:\n    \"\"\"Foo.\"\"\"\n    return ''\n",
        "extra": "passthrough",
    }
    out = _slim_metadata(metadata)
    assert out["system_prompt"] == "Be helpful."
    assert out["skills"][0]["file_count"] == 2
    assert isinstance(out["tool_definitions"], list)
    assert out["tool_definitions"][0]["name"] == "foo"
    assert out["extra"] == "passthrough"


def test_slim_metadata_passes_through_non_dict():
    from tools.get_agent_detail import _slim_metadata
    assert _slim_metadata("not-a-dict") == "not-a-dict"


# ── @tool integration tests ───────────────────────────────────────────────

def test_get_agent_detail_denies_when_agent_not_in_workspace(monkeypatch):
    """ensure_agent_in_workspace blocks cross-workspace agents."""
    from tools import get_agent_detail as mod

    _grant_membership(monkeypatch, agent_workspace="ws-OTHER")

    out = json.loads(mod.get_agent_detail("a-1"))
    assert "error" in out
    assert "not found in this workspace" in out["error"]


def test_get_agent_detail_handles_runtime_lookup_failure(monkeypatch):
    """If get_agent_runtime fails, returns clean error JSON (never raises)."""
    from tools import get_agent_detail as mod

    _grant_membership(monkeypatch)

    fake_control = MagicMock()
    fake_control.get_agent_runtime.side_effect = Exception("boom")

    with patch("boto3.client") as mc, patch("boto3.resource"):
        mc.return_value = fake_control
        out = json.loads(mod.get_agent_detail("a-1"))

    assert "error" in out
    assert "Agent runtime lookup failed" in out["error"]


def test_get_agent_detail_happy_path(monkeypatch):
    """Returns slim view: header + slimmed metadata + DDB-only fields."""
    from tools import get_agent_detail as mod

    _grant_membership(monkeypatch)

    runtime_resp = {
        "agentRuntimeName": "myAgent",
        "status": "READY",
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:000:runtime/a-1",
        "createdAt": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "lastUpdatedAt": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "description": "A demo agent",
    }

    metadata = {
        "system_prompt": "Be helpful.",
        "tool_definitions": "@tool\ndef foo() -> str:\n    \"\"\"Foo.\"\"\"\n    return ''\n",
        "skills": [{"name": "s1", "files": ["a.py", "b.py"]}],
    }

    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = runtime_resp

    fake_s3 = MagicMock()
    body = MagicMock()
    body.read.return_value = json.dumps(metadata).encode("utf-8")
    fake_s3.get_object.return_value = {"Body": body}

    fake_table = MagicMock()
    fake_table.get_item.return_value = {
        "Item": {
            "display_name": "My Agent",
            "linked_agents": ["x"],
            "memory": {"enabled": True},
            "default_model_id": "m",
            "runtime_type": "zip",
            "knowledge_bases": ["kb-1"],
        }
    }
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    def client_factory(svc, **kw):
        if svc == "bedrock-agentcore-control":
            return fake_control
        if svc == "s3":
            return fake_s3
        return MagicMock()

    with patch("boto3.client", side_effect=client_factory), \
         patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.get_agent_detail("a-1"))

    assert out["agent_id"] == "a-1"
    assert out["name"] == "myAgent"
    assert out["status"] == "READY"
    assert out["display_name"] == "My Agent"
    assert out["linked_agents"] == ["x"]
    assert out["runtime_type"] == "zip"
    assert out["knowledge_bases"] == ["kb-1"]
    assert isinstance(out["metadata"]["tool_definitions"], list)
    assert out["metadata"]["skills"][0]["file_count"] == 2


def test_get_agent_detail_handles_missing_metadata(monkeypatch):
    """Missing metadata.json doesn't crash the tool."""
    from tools import get_agent_detail as mod

    _grant_membership(monkeypatch)

    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = {
        "agentRuntimeName": "x", "status": "READY",
        "agentRuntimeArn": "arn:x", "createdAt": "", "lastUpdatedAt": "",
    }

    fake_s3 = MagicMock()
    fake_s3.get_object.side_effect = Exception("NoSuchKey")

    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": {}}
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    def client_factory(svc, **kw):
        if svc == "bedrock-agentcore-control":
            return fake_control
        if svc == "s3":
            return fake_s3
        return MagicMock()

    with patch("boto3.client", side_effect=client_factory), \
         patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.get_agent_detail("a-1"))

    assert out["metadata"] is None
    assert "metadata_note" in out


def test_get_agent_detail_swallows_ddb_errors(monkeypatch):
    """DDB lookup failure doesn't break the tool — DDB-only fields just absent."""
    from tools import get_agent_detail as mod

    _grant_membership(monkeypatch)

    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = {
        "agentRuntimeName": "x", "status": "READY",
        "agentRuntimeArn": "arn:x", "createdAt": "", "lastUpdatedAt": "",
    }

    fake_s3 = MagicMock()
    fake_s3.get_object.side_effect = Exception("NoSuchKey")

    def client_factory(svc, **kw):
        if svc == "bedrock-agentcore-control":
            return fake_control
        if svc == "s3":
            return fake_s3
        return MagicMock()

    fake_resource = MagicMock()
    fake_resource.Table.side_effect = Exception("ddb explode")

    with patch("boto3.client", side_effect=client_factory), \
         patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.get_agent_detail("a-1"))

    # No display_name etc. but tool didn't crash
    assert out["agent_id"] == "a-1"
    assert "display_name" not in out

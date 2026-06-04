"""Tests for delete_harness_agent tool."""
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
_mock_strands_models = sys.modules.get("strands.models") or types.ModuleType("strands.models")
if not hasattr(_mock_strands_models, "BedrockModel"):
    _mock_strands_models.BedrockModel = MagicMock
sys.modules["strands.models"] = _mock_strands_models

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000",
    "S3_BUCKET": "b",
    "AGENTS_TABLE": "agent-studio-agents",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


def _existing(**overrides):
    base = {
        "agentId": "myBot-abc",
        "workspace_id": "ws-1",
        "name": "myBot",
        "runtime_type": "harness",
        "harness_arn": "arn:aws:bedrock-agentcore:us-east-1:123:harness/myBot-abc",
        "status": "active",
    }
    base.update(overrides)
    return base


def test_delete_harness_agent_calls_cp_and_archives_ddb(monkeypatch):
    from tools import delete_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {"Item": _existing()}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    fake_cp = MagicMock()
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.delete_harness_agent(agent_id="myBot-abc"))
    assert out["ok"] is True
    fake_cp.delete_harness.assert_called_once_with(harnessId="myBot-abc")
    fake_ddb.update_item.assert_called_once()
    values = fake_ddb.update_item.call_args.kwargs["ExpressionAttributeValues"]
    assert values[":status"] == "archived"


def test_delete_harness_agent_rejects_non_harness(monkeypatch):
    from tools import delete_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {"Item": _existing(runtime_type="zip")}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    out = json.loads(mod.delete_harness_agent(agent_id="myBot-abc"))
    assert "error" in out


def test_delete_harness_agent_continues_on_already_deleted_harness(monkeypatch):
    """ResourceNotFound from control-plane means harness is gone; still soft-delete DDB."""
    from tools import delete_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {"Item": _existing()}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    class _ResourceNotFound(Exception):
        pass

    fake_cp = MagicMock()
    fake_cp.exceptions.ResourceNotFoundException = _ResourceNotFound
    fake_cp.delete_harness.side_effect = _ResourceNotFound("gone")
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.delete_harness_agent(agent_id="myBot-abc"))
    assert out["ok"] is True
    fake_ddb.update_item.assert_called_once()


def test_delete_harness_agent_returns_error_when_agent_not_found(monkeypatch):
    """get_item returns no Item → "agent not found"."""
    from tools import delete_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    out = json.loads(mod.delete_harness_agent(agent_id="ghost"))
    assert "error" in out
    assert "not found" in out["error"]


def test_delete_harness_agent_wraps_unexpected_errors(monkeypatch):
    """Outer try/except converts unexpected errors into JSON."""
    from tools import delete_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.side_effect = Exception("ddb 5xx")
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    out = json.loads(mod.delete_harness_agent(agent_id="anything"))
    assert "error" in out
    assert "ddb 5xx" in out["error"]


def test_delete_harness_agent_factories_use_boto3(monkeypatch):
    """Cover _get_control_client / _get_agents_table thin wrappers."""
    from tools import delete_harness_agent as mod
    captured = {"clients": [], "resources": []}

    def fake_client(svc, region_name=None):
        captured["clients"].append(svc)
        return MagicMock()

    def fake_resource(svc, region_name=None):
        captured["resources"].append(svc)
        m = MagicMock()
        m.Table.return_value = MagicMock()
        return m

    monkeypatch.setattr(mod.boto3, "client", fake_client)
    monkeypatch.setattr(mod.boto3, "resource", fake_resource)

    assert mod._get_control_client() is not None
    assert mod._get_agents_table() is not None
    assert "bedrock-agentcore-control" in captured["clients"]
    assert "dynamodb" in captured["resources"]

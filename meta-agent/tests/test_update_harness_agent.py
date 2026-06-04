"""Tests for update_harness_agent tool."""
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


def test_update_harness_agent_updates_prompt_and_model(monkeypatch):
    from tools import update_harness_agent as mod

    monkeypatch.setattr(mod, "_read_staging", lambda k: {
        "system_prompt": "You are now grumpy.",
        "model_id": "us.anthropic.claude-sonnet-4-6-20250929-v1:0",
    })

    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {"Item": _existing()}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    fake_cp = MagicMock()
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.update_harness_agent(agent_id="myBot-abc", staging_key="k"))
    assert out["ok"] is True

    fake_cp.update_harness.assert_called_once()
    kwargs = fake_cp.update_harness.call_args.kwargs
    assert kwargs["harnessId"] == "myBot-abc"
    assert kwargs["model"]["bedrockModelConfig"]["modelId"] == "us.anthropic.claude-sonnet-4-6-20250929-v1:0"
    assert kwargs["systemPrompt"] == [{"text": "You are now grumpy."}]
    fake_ddb.update_item.assert_called_once()


def test_update_harness_agent_rejects_non_harness(monkeypatch):
    from tools import update_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {"Item": _existing(runtime_type="zip")}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    out = json.loads(mod.update_harness_agent(agent_id="myBot-abc", staging_key="k"))
    assert "error" in out
    assert "runtime_type" in out["error"]


def test_update_harness_agent_rejects_missing_agent(monkeypatch):
    from tools import update_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    out = json.loads(mod.update_harness_agent(agent_id="doesnotexist", staging_key="k"))
    assert "error" in out
    assert "not found" in out["error"].lower()


def test_update_harness_agent_metadata_only_skip_cp(monkeypatch):
    """If staging has no system_prompt/model_id, only DDB updates (no CP call)."""
    from tools import update_harness_agent as mod
    monkeypatch.setattr(mod, "_read_staging", lambda k: {
        "display_name": "new display",
    })
    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {"Item": _existing()}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    fake_cp = MagicMock()
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.update_harness_agent(agent_id="myBot-abc", staging_key="k"))
    assert out["ok"] is True
    fake_cp.update_harness.assert_not_called()
    fake_ddb.update_item.assert_called_once()


def test_update_harness_agent_wraps_unexpected_errors(monkeypatch):
    """Outer try/except converts any error into JSON {"error": ...}."""
    from tools import update_harness_agent as mod
    fake_ddb = MagicMock()
    fake_ddb.get_item.side_effect = Exception("ddb 5xx")
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)
    out = json.loads(mod.update_harness_agent(agent_id="anything", staging_key="k"))
    assert "error" in out
    assert "ddb 5xx" in out["error"]


def test_update_harness_agent_helper_factories_use_boto3(monkeypatch):
    """Cover _get_control_client / _get_agents_table / _read_staging branches."""
    from tools import update_harness_agent as mod

    captured = {}

    def fake_client(svc, region_name=None):
        captured.setdefault("clients", []).append(svc)
        m = MagicMock()
        if svc == "s3":
            m.get_object.return_value = {
                "Body": MagicMock(read=lambda: b'{"x": 1}'),
            }
        return m

    def fake_resource(svc, region_name=None):
        captured.setdefault("resources", []).append(svc)
        m = MagicMock()
        m.Table.return_value = MagicMock()
        return m

    monkeypatch.setattr(mod.boto3, "client", fake_client)
    monkeypatch.setattr(mod.boto3, "resource", fake_resource)

    cp = mod._get_control_client()
    table = mod._get_agents_table()
    payload = mod._read_staging("staging/x.json")

    assert cp is not None
    assert table is not None
    assert payload == {"x": 1}
    assert "bedrock-agentcore-control" in captured["clients"]
    assert "s3" in captured["clients"]
    assert "dynamodb" in captured["resources"]


def test_update_harness_agent_persists_mcp_targets(monkeypatch):
    """mcp_targets in staging.json is forwarded to DDB even though CP doesn't get them."""
    from tools import update_harness_agent as mod
    monkeypatch.setattr(mod, "_read_staging", lambda k: {
        "system_prompt": "still helpful.",
        "mcp_targets": ["mcp-cloudwatch"],
    })

    fake_ddb = MagicMock()
    fake_ddb.get_item.return_value = {"Item": _existing()}
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    fake_cp = MagicMock()
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.update_harness_agent(agent_id="myBot-abc", staging_key="k"))
    assert out["ok"] is True

    update_kwargs = fake_ddb.update_item.call_args.kwargs
    values = update_kwargs["ExpressionAttributeValues"]
    assert values[":mcp_targets"] == ["mcp-cloudwatch"]
    assert values[":system_prompt"] == "still helpful."
    # CP got system_prompt only — no model
    cp_kwargs = fake_cp.update_harness.call_args.kwargs
    assert cp_kwargs["systemPrompt"] == [{"text": "still helpful."}]
    assert "model" not in cp_kwargs

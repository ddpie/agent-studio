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

"""Tests for create_harness_agent tool."""
import json
import sys
import types
from unittest.mock import MagicMock

import pytest


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
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    """Meta-Agent's apply_scope sets these at each invoke."""
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-1", raising=False)


def _fake_staging(**overrides):
    base = {
        "name": "myBot",
        "display_name": "My Bot",
        "description": "A test bot",
        "system_prompt": "You are friendly.",
        "model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        "runtime_type": "harness",
        "workspace_id": "ws-1",
    }
    base.update(overrides)
    return base


def test_create_harness_agent_happy_path(monkeypatch):
    from tools import create_harness_agent as mod

    monkeypatch.setattr(mod, "_read_staging", lambda k: _fake_staging())
    monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:aws:iam::1:role/ws1")

    fake_ddb = MagicMock()
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    fake_cp = MagicMock()
    fake_cp.create_harness.return_value = {
        "harness": {
            "arn": "arn:aws:bedrock-agentcore:us-east-1:123:harness/myBot-abc",
            "harnessId": "myBot-abc",
        }
    }
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.create_harness_agent(staging_key="staging/x.json"))

    assert out["ok"] is True
    assert out["harnessArn"].endswith("harness/myBot-abc")
    assert out["agentId"] == "myBot-abc"

    fake_cp.create_harness.assert_called_once()
    kwargs = fake_cp.create_harness.call_args.kwargs
    assert kwargs["harnessName"] == "myBot"
    assert kwargs["executionRoleArn"] == "arn:aws:iam::1:role/ws1"
    assert kwargs["model"] == {"bedrockModelConfig": {"modelId": "us.anthropic.claude-haiku-4-5-20251001-v1:0"}}
    assert kwargs["systemPrompt"] == [{"text": "You are friendly."}]

    fake_ddb.put_item.assert_called_once()
    item = fake_ddb.put_item.call_args.kwargs["Item"]
    assert item["runtime_type"] == "harness"
    assert item["harness_arn"].endswith("harness/myBot-abc")
    assert item["status"] == "active"
    assert item["created_by"] == "user-1"
    assert item["workspace_id"] == "ws-1"


def test_create_harness_agent_rejects_wrong_runtime_type(monkeypatch):
    from tools import create_harness_agent as mod
    monkeypatch.setattr(mod, "_read_staging", lambda k: _fake_staging(runtime_type="zip"))
    out = json.loads(mod.create_harness_agent(staging_key="staging/x.json"))
    assert "error" in out
    assert "runtime_type" in out["error"]


def test_create_harness_agent_rejects_missing_fields(monkeypatch):
    from tools import create_harness_agent as mod
    monkeypatch.setattr(mod, "_read_staging", lambda k: _fake_staging(name=""))
    out = json.loads(mod.create_harness_agent(staging_key="staging/x.json"))
    assert "error" in out


def test_create_harness_agent_wraps_boto_errors(monkeypatch):
    from tools import create_harness_agent as mod
    monkeypatch.setattr(mod, "_read_staging", lambda k: _fake_staging())
    monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:aws:iam::1:role/ws1")

    fake_cp = MagicMock()
    fake_cp.create_harness.side_effect = Exception("quota exceeded")
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.create_harness_agent(staging_key="staging/x.json"))
    assert "error" in out
    assert "quota" in out["error"]


def test_create_harness_agent_direct_params_conversational_mode(monkeypatch):
    """Conversational path: Meta-Agent passes name/prompt/model_id directly
    (no staging_key). Scope provides workspace_id."""
    from tools import create_harness_agent as mod

    monkeypatch.setattr(mod, "_get_agent_role_arn", lambda ws: "arn:aws:iam::1:role/ws1")

    fake_ddb = MagicMock()
    monkeypatch.setattr(mod, "_get_agents_table", lambda: fake_ddb)

    fake_cp = MagicMock()
    fake_cp.create_harness.return_value = {
        "harness": {
            "arn": "arn:aws:bedrock-agentcore:us-east-1:123:harness/chatBot-xyz",
            "harnessId": "chatBot-xyz",
        }
    }
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.create_harness_agent(
        name="chatBot",
        system_prompt="Be concise.",
        model_id="us.anthropic.claude-sonnet-4-6-20250929-v1:0",
        description="direct-params sanity",
    ))

    assert out["ok"] is True
    assert out["agentId"] == "chatBot-xyz"

    kwargs = fake_cp.create_harness.call_args.kwargs
    assert kwargs["harnessName"] == "chatBot"
    assert kwargs["systemPrompt"] == [{"text": "Be concise."}]

    item = fake_ddb.put_item.call_args.kwargs["Item"]
    assert item["runtime_type"] == "harness"
    assert item["workspace_id"] == "ws-1"  # from scope
    assert item["description"] == "direct-params sanity"
    assert item["display_name"] == "chatBot"  # defaults to name when empty


def test_create_harness_agent_direct_params_missing_name(monkeypatch):
    """Direct-params mode: missing name returns a clean error, no boto call."""
    from tools import create_harness_agent as mod
    fake_cp = MagicMock()
    monkeypatch.setattr(mod, "_get_control_client", lambda: fake_cp)

    out = json.loads(mod.create_harness_agent(
        system_prompt="hi", model_id="m",
    ))
    assert "error" in out
    assert "name" in out["error"].lower()
    fake_cp.create_harness.assert_not_called()

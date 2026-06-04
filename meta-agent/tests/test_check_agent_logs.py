"""Tests for check_agent_logs — CloudWatch log retrieval for deployed agents."""
import json
import sys
import types
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


def _grant_agent(monkeypatch, agent_id="myAgent-abc1234567890XYZ",
                  agent_name="myAgent", agent_workspace="ws-1", role="viewer"):
    """Patch scope so list_workspace_agents + ensure_agent_in_workspace pass."""
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": role}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    agents_table = MagicMock()
    # query (used by list_workspace_agents) + get_item used by ensure_*.
    agents_table.query.return_value = {
        "Items": [{
            "agentId": agent_id, "name": agent_name, "agentName": agent_name,
            "workspace_id": agent_workspace, "status": "active",
        }],
    }
    agents_table.get_item.return_value = {
        "Item": {
            "agentId": agent_id, "name": agent_name,
            "workspace_id": agent_workspace, "status": "active",
        },
    }
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)
    return agent_id


# ── Helper unit tests ─────────────────────────────────────────────────────

def test_extract_text_picks_first_message_field():
    from tools.check_agent_logs import _extract_text
    parsed = {"level": "INFO", "message": "Hello", "body": "ignored"}
    level, msg = _extract_text(parsed)
    assert level == "INFO"
    assert msg == "Hello"


def test_extract_text_falls_back_to_body():
    from tools.check_agent_logs import _extract_text
    parsed = {"severityText": "ERROR", "body": "from body"}
    level, msg = _extract_text(parsed)
    assert level == "ERROR"
    assert msg == "from body"


def test_extract_text_handles_otel_nested_body():
    from tools.check_agent_logs import _extract_text
    parsed = {"body": {"stringValue": "nested OTEL message"}}
    _, msg = _extract_text(parsed)
    assert msg == "nested OTEL message"


def test_extract_text_returns_empty_when_nothing():
    from tools.check_agent_logs import _extract_text
    level, msg = _extract_text({})
    assert level == ""
    assert msg == ""


def test_resolve_agent_id_returns_id_directly_when_long(monkeypatch):
    """If the input looks like a runtime ID (has '-' and len>20), use as-is."""
    from tools import check_agent_logs as mod
    # Don't even need workspace agents listed here
    monkeypatch.setattr(mod, "list_workspace_agents", list)
    out = mod._resolve_agent_id("myAgent-abc1234567890XYZ")
    assert out == "myAgent-abc1234567890XYZ"


def test_resolve_agent_id_resolves_by_name(monkeypatch):
    """A short name resolves to the full agentId via workspace listing."""
    from tools import check_agent_logs as mod
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": "myBot-abc123", "name": "myBot", "agentName": "myBot"},
    ])
    out = mod._resolve_agent_id("myBot")
    assert out == "myBot-abc123"


def test_resolve_agent_id_returns_none_when_no_match(monkeypatch):
    from tools import check_agent_logs as mod
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": "other-1", "name": "other", "agentName": "other"},
    ])
    assert mod._resolve_agent_id("nope") is None


# ── @tool integration tests ───────────────────────────────────────────────

def test_check_agent_logs_unknown_agent_returns_error(monkeypatch):
    from tools import check_agent_logs as mod
    monkeypatch.setattr(mod, "list_workspace_agents", list)

    out = json.loads(mod.check_agent_logs("ghost"))
    assert "error" in out
    assert "not found" in out["error"]


def test_check_agent_logs_denies_cross_workspace(monkeypatch):
    """ensure_agent_in_workspace blocks foreign agents."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch, agent_workspace="ws-OTHER")
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    out = json.loads(mod.check_agent_logs(aid))
    assert "error" in out
    assert "not found in this workspace" in out["error"]


def test_check_agent_logs_no_events_returns_friendly_message(monkeypatch):
    """Empty event list → friendly message, not a crash."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch)
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    fake_logs = MagicMock()
    fake_logs.exceptions = MagicMock()
    fake_logs.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    fake_logs.filter_log_events.return_value = {"events": []}

    with patch("boto3.client", return_value=fake_logs):
        out = json.loads(mod.check_agent_logs(aid, minutes=15))

    assert "No logs found" in out["message"]
    assert out["resolved_agent_id"] == aid


def test_check_agent_logs_log_group_not_found(monkeypatch):
    """ResourceNotFoundException returns helpful error JSON, not raise."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch)
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    rnf = type("ResourceNotFoundException", (Exception,), {})
    fake_logs = MagicMock()
    fake_logs.exceptions = MagicMock()
    fake_logs.exceptions.ResourceNotFoundException = rnf
    fake_logs.filter_log_events.side_effect = rnf("nope")

    with patch("boto3.client", return_value=fake_logs):
        out = json.loads(mod.check_agent_logs(aid))

    assert "error" in out
    assert "Log group not found" in out["error"]
    assert "hint" in out


def test_check_agent_logs_other_exception_returns_error(monkeypatch):
    """Generic exception is wrapped in JSON error response."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch)
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    fake_logs = MagicMock()
    fake_logs.exceptions = MagicMock()
    fake_logs.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    fake_logs.filter_log_events.side_effect = Exception("ThrottlingException")

    with patch("boto3.client", return_value=fake_logs):
        out = json.loads(mod.check_agent_logs(aid))

    assert "error" in out
    assert "ThrottlingException" in out["error"]


def test_check_agent_logs_formats_json_log_entries(monkeypatch):
    """JSON log records get parsed into [timestamp] LEVEL message format."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch)
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    fake_logs = MagicMock()
    fake_logs.exceptions = MagicMock()
    fake_logs.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    fake_logs.filter_log_events.return_value = {
        "events": [
            {"timestamp": 1700000000000,
             "message": json.dumps({"level": "INFO", "message": "started"})},
            {"timestamp": 1700000001000,
             "message": json.dumps({"errorType": "RuntimeError",
                                     "errorMessage": "boom"})},
            {"timestamp": 1700000002000, "message": "plain text line"},
        ],
    }

    with patch("boto3.client", return_value=fake_logs):
        out = mod.check_agent_logs(aid)

    # Tool returns raw text (joined lines), not JSON
    assert "started" in out
    assert "RuntimeError" in out
    assert "boom" in out
    assert "plain text line" in out
    # Default tz=UTC
    assert "UTC" in out


def test_check_agent_logs_supports_custom_tz(monkeypatch):
    """tz parameter changes timestamp formatting label."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch)
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    fake_logs = MagicMock()
    fake_logs.exceptions = MagicMock()
    fake_logs.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    fake_logs.filter_log_events.return_value = {
        "events": [
            {"timestamp": 1700000000000,
             "message": json.dumps({"level": "INFO", "message": "ok"})},
        ],
    }

    with patch("boto3.client", return_value=fake_logs):
        out = mod.check_agent_logs(aid, tz="Asia/Shanghai")

    assert "Asia/Shanghai" in out


def test_check_agent_logs_invalid_tz_falls_back_to_utc(monkeypatch):
    """Bogus tz strings fall back to UTC instead of raising."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch)
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    fake_logs = MagicMock()
    fake_logs.exceptions = MagicMock()
    fake_logs.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    fake_logs.filter_log_events.return_value = {
        "events": [{"timestamp": 1700000000000,
                    "message": json.dumps({"level": "INFO", "message": "x"})}],
    }

    with patch("boto3.client", return_value=fake_logs):
        out = mod.check_agent_logs(aid, tz="Not/A_Real_TZ")

    assert "UTC" in out


def test_check_agent_logs_filters_invalid_http_noise(monkeypatch):
    """'Invalid HTTP request' lines are filtered as noise."""
    from tools import check_agent_logs as mod
    aid = _grant_agent(monkeypatch)
    monkeypatch.setattr(mod, "list_workspace_agents", lambda: [
        {"agentId": aid, "name": "myAgent", "agentName": "myAgent"},
    ])

    fake_logs = MagicMock()
    fake_logs.exceptions = MagicMock()
    fake_logs.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    fake_logs.filter_log_events.return_value = {
        "events": [
            {"timestamp": 1700000000000, "message": "Invalid HTTP request"},
        ],
    }

    with patch("boto3.client", return_value=fake_logs):
        out = mod.check_agent_logs(aid)

    out_data = json.loads(out)
    assert "Only noise" in out_data["message"]

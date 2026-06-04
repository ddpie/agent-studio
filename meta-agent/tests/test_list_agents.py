"""Tests for list_agents — workspace-scoped agent enumeration."""

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


def test_list_agents_returns_empty_when_not_workspace_member(monkeypatch):
    """require_role should reject the caller when no workspace context."""
    from tools import _scope, list_agents as mod

    # No workspace context
    monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)

    out = json.loads(mod.list_agents())
    assert out == []


def test_list_agents_returns_empty_when_not_member(monkeypatch):
    """require_role rejects when caller has no membership record."""
    from tools import _scope, list_agents as mod

    workspaces = MagicMock()
    workspaces.get_item.return_value = {}  # no Item → not a member
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    out = json.loads(mod.list_agents())
    assert out == []


def test_list_agents_happy_path(monkeypatch):
    """Returns workspace agents enriched with live AgentCore status."""
    from tools import _scope, list_agents as mod

    # Caller has viewer role
    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": "viewer"}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    # Fake agents table query
    agents_table = MagicMock()
    agents_table.query.return_value = {
        "Items": [
            {
                "agentId": "a-1",
                "name": "Alpha",
                "description": "first",
                "status": "active",
                "workspace_id": "ws-1",
            },
            {
                "agentId": "a-2",
                "agentName": "Beta",
                "description": "second",
                "status": "active",
                "workspace_id": "ws-1",
            },
        ],
    }
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)

    fake_control = MagicMock()
    # First lookup returns READY, second raises
    fake_control.get_agent_runtime.side_effect = [
        {"status": "READY"},
        Exception("ResourceNotFoundException"),
    ]

    with patch("boto3.client") as mc:
        mc.return_value = fake_control
        out = json.loads(mod.list_agents())

    assert isinstance(out, list)
    assert len(out) == 2
    # Order preserved from records list
    assert out[0]["id"] == "a-1"
    assert out[0]["name"] == "Alpha"
    assert out[0]["status"] == "READY"  # live status overrides
    assert out[1]["id"] == "a-2"
    assert out[1]["name"] == "Beta"  # falls back from agentName
    # Second agent failed runtime lookup → keeps DDB record's status
    assert out[1]["status"] == "active"


def test_list_agents_with_no_records(monkeypatch):
    """Empty workspace returns []."""
    from tools import _scope, list_agents as mod

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": "editor"}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    agents_table = MagicMock()
    agents_table.query.return_value = {"Items": []}
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)

    out = json.loads(mod.list_agents())
    assert out == []


def test_list_agents_filters_archived_by_default(monkeypatch):
    """Archived agents (status='archived') are filtered out."""
    from tools import _scope, list_agents as mod

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": "admin"}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    agents_table = MagicMock()
    agents_table.query.return_value = {
        "Items": [
            {"agentId": "live-1", "name": "L", "status": "active", "workspace_id": "ws-1"},
            {"agentId": "archived-1", "name": "A", "status": "archived", "workspace_id": "ws-1"},
        ],
    }
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)

    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = {"status": "READY"}

    with patch("boto3.client") as mc:
        mc.return_value = fake_control
        out = json.loads(mod.list_agents())

    ids = [a["id"] for a in out]
    assert "live-1" in ids
    assert "archived-1" not in ids

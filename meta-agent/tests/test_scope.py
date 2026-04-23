"""Tests for the Meta-Agent workspace + RBAC scoping helpers."""
import json
import sys
import types
from unittest.mock import MagicMock, patch


# Minimal config stub so tools._scope can import
_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "REGION": "us-east-1",
    "AGENTS_TABLE": "agent-studio-agents",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


def _reset_scope():
    """Re-import tools._scope with cleared state."""
    # Evict cached module so _workspace_id/_caller_id defaults reset
    for m in list(sys.modules):
        if m.startswith("tools._scope") or m == "tools._scope":
            del sys.modules[m]
    import tools._scope as scope
    return scope


def test_role_constants_match_lambda_shared_auth():
    """Role constants must stay in sync with lambda/shared/auth.ROLE_LEVEL."""
    scope = _reset_scope()
    assert scope.ROLE_VIEWER == "viewer"
    assert scope.ROLE_EDITOR == "editor"
    assert scope.ROLE_ADMIN == "admin"
    assert scope.ROLE_OWNER == "owner"
    assert scope._ROLE_LEVEL == {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}


def test_ensure_agent_refuses_without_workspace_context():
    scope = _reset_scope()
    # _workspace_id defaults to empty — no auth context
    record, err = scope.ensure_agent_in_workspace("some-agent")
    assert record is None
    assert "No workspace context" in err["error"]


def test_ensure_agent_refuses_cross_workspace():
    scope = _reset_scope()
    scope._workspace_id = "ws-mine"
    scope._caller_id = "user-1"

    table = MagicMock()
    table.get_item.return_value = {
        "Item": {"agentId": "a1", "workspace_id": "ws-other"}
    }
    with patch.object(scope, "_agents_table", return_value=table):
        record, err = scope.ensure_agent_in_workspace("a1")
    assert record is None
    # Must NOT say "different workspace" — indistinguishable from "not found"
    assert "not found in this workspace" in err["error"]
    assert "ws-other" not in err["error"]


def test_ensure_agent_denies_insufficient_role():
    scope = _reset_scope()
    scope._workspace_id = "ws-mine"
    scope._caller_id = "user-1"

    agents = MagicMock()
    agents.get_item.return_value = {
        "Item": {"agentId": "a1", "workspace_id": "ws-mine"}
    }
    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": scope.ROLE_VIEWER}}

    with patch.object(scope, "_agents_table", return_value=agents), \
         patch.object(scope, "_workspaces_table", return_value=workspaces):
        record, err = scope.ensure_agent_in_workspace("a1", min_role=scope.ROLE_ADMIN)
    assert record is None
    assert "admin" in err["error"].lower()
    assert "viewer" in err["error"].lower()


def test_ensure_agent_passes_with_sufficient_role():
    scope = _reset_scope()
    scope._workspace_id = "ws-mine"
    scope._caller_id = "user-1"

    agents = MagicMock()
    agents.get_item.return_value = {
        "Item": {"agentId": "a1", "workspace_id": "ws-mine", "name": "my-agent"}
    }
    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": scope.ROLE_OWNER}}

    with patch.object(scope, "_agents_table", return_value=agents), \
         patch.object(scope, "_workspaces_table", return_value=workspaces):
        record, err = scope.ensure_agent_in_workspace("a1", min_role=scope.ROLE_EDITOR)
    assert err is None
    assert record["name"] == "my-agent"


def test_require_role_without_workspace():
    scope = _reset_scope()
    err = scope.require_role(scope.ROLE_VIEWER)
    assert err is not None
    assert "No workspace context" in err["error"]


def test_require_role_non_member():
    scope = _reset_scope()
    scope._workspace_id = "ws-mine"
    scope._caller_id = "user-1"
    workspaces = MagicMock()
    workspaces.get_item.return_value = {}  # no Item
    with patch.object(scope, "_workspaces_table", return_value=workspaces):
        err = scope.require_role(scope.ROLE_VIEWER)
    assert err is not None
    assert "not a member" in err["error"]


def test_list_workspace_agents_paginates():
    scope = _reset_scope()
    scope._workspace_id = "ws-mine"
    table = MagicMock()
    # First page returns 2 items + a continuation token; second page returns 1.
    table.query.side_effect = [
        {"Items": [
            {"agentId": "a1", "status": "active"},
            {"agentId": "a2", "status": "archived"},  # filtered by default
        ], "LastEvaluatedKey": {"agentId": "a2"}},
        {"Items": [{"agentId": "a3", "status": "active"}]},
    ]
    with patch.object(scope, "_agents_table", return_value=table):
        items = scope.list_workspace_agents()
    assert [i["agentId"] for i in items] == ["a1", "a3"]
    assert table.query.call_count == 2


def test_list_workspace_agents_empty_without_workspace():
    scope = _reset_scope()
    assert scope.list_workspace_agents() == []

"""Tests for workspace creation + memory integration."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_ws_table():
    with patch("crud.workspaces._get_table") as g:
        t = MagicMock()
        t.name = "test-workspaces"
        t.put_item.return_value = {}
        t.update_item.return_value = {}
        t.meta.client.transact_write_items.return_value = {}
        g.return_value = t
        yield t


@pytest.fixture
def mock_agentcore_control():
    with patch("crud.workspaces._get_control") as g:
        c = MagicMock()
        g.return_value = c
        yield c


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "test-origin")


def _apigw(method, path, user_id="u1", body=None, headers=None):
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "requestContext": {
            "stage": "test",
            "requestId": "req-1",
            "identity": {"sourceIp": "127.0.0.1"},
        },
        "headers": {
            "Authorization": "Bearer X",
            "x-origin-verify": "test-origin",
            "Content-Type": "application/json",
            **(headers or {}),
        },
        "body": json.dumps(body) if body else None,
        "queryStringParameters": None,
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


# ── POST /api/workspaces ──────────────────────────────────────


def test_create_workspace_memory_success(mock_jwt, mock_ws_table, mock_agentcore_control):
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "mem-abc"}}
    resp = _invoke(_apigw("POST", "/api/workspaces", body={"name": "WS"}))
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["memory_id"] == "mem-abc"
    mock_ws_table.update_item.assert_called_once()
    call_kw = mock_ws_table.update_item.call_args.kwargs
    assert call_kw["Key"] == {"workspaceId": body["workspaceId"], "sk": "META"}
    assert call_kw["ExpressionAttributeValues"][":m"] == "mem-abc"


def test_create_workspace_memory_failure_does_not_block(mock_jwt, mock_ws_table, mock_agentcore_control):
    mock_agentcore_control.create_memory.side_effect = Exception("boom")
    resp = _invoke(_apigw("POST", "/api/workspaces", body={"name": "WS2"}))
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body.get("memory_id") is None
    mock_ws_table.update_item.assert_not_called()


def test_create_workspace_passes_default_strategies(mock_jwt, mock_ws_table, mock_agentcore_control):
    from shared.memory_strategies import DEFAULT_MEMORY_STRATEGIES
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "x"}}
    _invoke(_apigw("POST", "/api/workspaces", body={"name": "WS"}))
    call_kw = mock_agentcore_control.create_memory.call_args.kwargs
    assert call_kw["memoryStrategies"] == DEFAULT_MEMORY_STRATEGIES


# ── POST /api/onboarding ──────────────────────────────────────


def test_onboarding_memory_success(mock_jwt, mock_ws_table, mock_agentcore_control):
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "mem-onb"}}
    resp = _invoke(_apigw("POST", "/api/onboarding"))
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["memory_id"] == "mem-onb"
    assert body["onboarding"] is True
    mock_ws_table.update_item.assert_called_once()


def test_onboarding_memory_failure_does_not_block(mock_jwt, mock_ws_table, mock_agentcore_control):
    mock_agentcore_control.create_memory.side_effect = Exception("service unavailable")
    resp = _invoke(_apigw("POST", "/api/onboarding"))
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body.get("memory_id") is None
    assert body["onboarding"] is True
    mock_ws_table.update_item.assert_not_called()


# ── _create_workspace_memory isolation ─────────────────────────


def test_create_workspace_memory_returns_id(mock_agentcore_control):
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "mem-123"}}
    from crud.workspaces import _create_workspace_memory
    result = _create_workspace_memory("ws-abcdefghijkl-rest")
    assert result == "mem-123"
    call_kw = mock_agentcore_control.create_memory.call_args.kwargs
    assert call_kw["name"] == "agentstudio-ws-ws-abcdefghi"


def test_create_workspace_memory_returns_none_on_error(mock_agentcore_control):
    mock_agentcore_control.create_memory.side_effect = RuntimeError("timeout")
    from crud.workspaces import _create_workspace_memory
    result = _create_workspace_memory("ws-xyz")
    assert result is None

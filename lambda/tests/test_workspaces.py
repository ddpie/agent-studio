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


# ── DELETE /api/workspaces/{wsId} — memory cleanup ──────────────


@pytest.fixture
def mock_membership_owner_for_delete():
    """Mock get_membership where middleware imports it so auth_check grants owner access."""
    with patch("shared.middleware.get_membership") as mock:
        yield mock


def _setup_delete_mocks(mock_ws_table, mock_membership, ws_id, user_id, memory_id=None):
    """Wire up mocks so delete_workspace can proceed past auth + query loop."""
    meta_item = {"workspaceId": ws_id, "sk": "META", "owner_id": user_id}
    if memory_id:
        meta_item["memory_id"] = memory_id
    mock_ws_table.get_item.return_value = {"Item": meta_item}
    mock_ws_table.query.return_value = {"Items": [
        {"workspaceId": ws_id, "sk": "META"},
        {"workspaceId": ws_id, "sk": f"MEMBER#{user_id}"},
    ]}
    mock_membership.return_value = {
        "workspaceId": ws_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "owner",
    }
    # batch_writer context manager
    batch = MagicMock()
    mock_ws_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch)
    mock_ws_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)


def test_delete_workspace_calls_delete_memory(
    mock_jwt, mock_ws_table, mock_agentcore_control, mock_membership_owner_for_delete
):
    """delete_workspace attempts to delete the Memory resource."""
    user_id = mock_jwt.return_value["sub"]
    _setup_delete_mocks(mock_ws_table, mock_membership_owner_for_delete, "ws-d1", user_id, "mem-d1")
    mock_agentcore_control.delete_memory.return_value = {}
    resp = _invoke(_apigw("DELETE", "/api/workspaces/ws-d1", user_id))
    assert resp["statusCode"] == 200
    mock_agentcore_control.delete_memory.assert_called_once_with(memoryId="mem-d1")


def test_delete_workspace_memory_failure_does_not_block(
    mock_jwt, mock_ws_table, mock_agentcore_control, mock_membership_owner_for_delete
):
    """When delete_memory fails, workspace deletion still proceeds."""
    user_id = mock_jwt.return_value["sub"]
    _setup_delete_mocks(mock_ws_table, mock_membership_owner_for_delete, "ws-d2", user_id, "mem-d2")
    mock_agentcore_control.delete_memory.side_effect = Exception("not found")
    resp = _invoke(_apigw("DELETE", "/api/workspaces/ws-d2", user_id))
    assert resp["statusCode"] == 200


def test_delete_workspace_skips_memory_when_no_memory_id(
    mock_jwt, mock_ws_table, mock_agentcore_control, mock_membership_owner_for_delete
):
    """No memory_id → delete_memory is not called."""
    user_id = mock_jwt.return_value["sub"]
    _setup_delete_mocks(mock_ws_table, mock_membership_owner_for_delete, "ws-d3", user_id)
    resp = _invoke(_apigw("DELETE", "/api/workspaces/ws-d3", user_id))
    assert resp["statusCode"] == 200
    mock_agentcore_control.delete_memory.assert_not_called()


# ── POST /api/workspaces/{wsId}/memory/repair ────────────────────


@pytest.fixture
def mock_membership_owner_for_repair():
    """Mock get_membership so auth_check grants owner access for repair tests."""
    with patch("shared.middleware.get_membership") as mock:
        yield mock


def _setup_repair_membership(mock_membership, ws_id, user_id):
    mock_membership.return_value = {
        "workspaceId": ws_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "owner",
    }


def test_repair_noop_when_memory_id_present(
    mock_jwt, mock_ws_table, mock_agentcore_control, mock_membership_owner_for_repair
):
    """If memory_id already set, return it without calling create_memory."""
    user_id = mock_jwt.return_value["sub"]
    _setup_repair_membership(mock_membership_owner_for_repair, "ws-r1", user_id)
    mock_ws_table.get_item.return_value = {"Item": {
        "workspaceId": "ws-r1", "sk": "META", "memory_id": "existing-mem"
    }}
    resp = _invoke(_apigw("POST", "/api/workspaces/ws-r1/memory/repair", user_id))
    body = json.loads(resp["body"])
    assert body["memory_id"] == "existing-mem"
    mock_agentcore_control.create_memory.assert_not_called()


def test_repair_creates_when_null(
    mock_jwt, mock_ws_table, mock_agentcore_control, mock_membership_owner_for_repair
):
    """If memory_id is null, create and persist."""
    user_id = mock_jwt.return_value["sub"]
    _setup_repair_membership(mock_membership_owner_for_repair, "ws-r2", user_id)
    mock_ws_table.get_item.return_value = {"Item": {
        "workspaceId": "ws-r2", "sk": "META"
    }}
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "new-mem"}}
    resp = _invoke(_apigw("POST", "/api/workspaces/ws-r2/memory/repair", user_id))
    body = json.loads(resp["body"])
    assert body["memory_id"] == "new-mem"
    mock_agentcore_control.create_memory.assert_called_once()
    mock_ws_table.update_item.assert_called_once()


def test_repair_returns_error_when_creation_fails(
    mock_jwt, mock_ws_table, mock_agentcore_control, mock_membership_owner_for_repair
):
    """If create_memory fails again, return 500."""
    user_id = mock_jwt.return_value["sub"]
    _setup_repair_membership(mock_membership_owner_for_repair, "ws-r3", user_id)
    mock_ws_table.get_item.return_value = {"Item": {
        "workspaceId": "ws-r3", "sk": "META"
    }}
    mock_agentcore_control.create_memory.side_effect = Exception("still broken")
    resp = _invoke(_apigw("POST", "/api/workspaces/ws-r3/memory/repair", user_id))
    assert resp["statusCode"] == 500


# ── GET /api/admin/workspaces ───────────────────────────────────


@pytest.fixture
def mock_admin_check():
    """Patch check_platform_admin at the workspaces module's import site."""
    with patch("crud.workspaces.check_platform_admin") as mock:
        yield mock


def test_list_all_workspaces_requires_platform_admin(mock_jwt, mock_ws_table, mock_admin_check):
    """Non-admin callers get 403."""
    mock_admin_check.return_value = ("u1", False, None)
    resp = _invoke(_apigw("GET", "/api/admin/workspaces"))
    assert resp["statusCode"] == 403
    mock_ws_table.scan.assert_not_called()


def test_list_all_workspaces_returns_every_workspace(mock_jwt, mock_ws_table, mock_admin_check):
    """Admin sees workspaces regardless of membership."""
    mock_admin_check.return_value = ("admin-uid", True, None)
    mock_ws_table.scan.return_value = {
        "Items": [
            {"workspaceId": "ws-a", "sk": "META", "name": "Alice WS", "owner_id": "uid-a", "created_at": "t1"},
            {"workspaceId": "ws-b", "sk": "META", "name": "Bob WS", "owner_id": "uid-b", "created_at": "t2"},
        ]
    }
    # Cognito hydration is best-effort — make it a no-op by pretending pool isn't set
    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp = _invoke(_apigw("GET", "/api/admin/workspaces"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    ids = sorted(w["workspaceId"] for w in body["items"])
    assert ids == ["ws-a", "ws-b"]
    # Scan must filter on META rows, not return MEMBER/INVITE junk
    scan_kwargs = mock_ws_table.scan.call_args.kwargs
    assert scan_kwargs["ExpressionAttributeValues"][":sk"] == "META"
    assert "next" not in body  # no LastEvaluatedKey


def test_list_all_workspaces_passes_pagination_token(mock_jwt, mock_ws_table, mock_admin_check):
    """LastEvaluatedKey is echoed back as an opaque `next` token and round-trips."""
    import base64
    mock_admin_check.return_value = ("admin-uid", True, None)
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "ws-1", "sk": "META", "name": "One"}],
        "LastEvaluatedKey": {"workspaceId": "ws-1", "sk": "META"},
    }
    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp = _invoke(_apigw("GET", "/api/admin/workspaces"))
    body = json.loads(resp["body"])
    assert "next" in body
    # Round-trip: decoded token matches LastEvaluatedKey
    decoded = json.loads(base64.urlsafe_b64decode(body["next"].encode()).decode())
    assert decoded == {"workspaceId": "ws-1", "sk": "META"}

    # Second page request resumes via ExclusiveStartKey
    mock_ws_table.scan.reset_mock()
    mock_ws_table.scan.return_value = {"Items": []}
    event = _apigw("GET", "/api/admin/workspaces")
    event["queryStringParameters"] = {"next": body["next"]}
    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp2 = _invoke(event)
    assert resp2["statusCode"] == 200
    assert mock_ws_table.scan.call_args.kwargs["ExclusiveStartKey"] == decoded


def test_list_all_workspaces_rejects_invalid_next_token(mock_jwt, mock_ws_table, mock_admin_check):
    mock_admin_check.return_value = ("admin-uid", True, None)
    event = _apigw("GET", "/api/admin/workspaces")
    event["queryStringParameters"] = {"next": "not-base64!!!"}
    resp = _invoke(event)
    assert resp["statusCode"] == 400
    mock_ws_table.scan.assert_not_called()

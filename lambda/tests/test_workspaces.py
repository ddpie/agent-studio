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
        # _workspace_name_exists scans with `while True` until LastEvaluatedKey
        # is missing. A bare MagicMock would loop forever (its .get() returns
        # a truthy MagicMock). Force a real dict so the loop exits.
        t.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
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
    assert call_kw["name"] == "agentstudio_ws_ws_abcdefghi"


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
    mock_ws_table.query.return_value = {
        "Items": [
            {"workspaceId": ws_id, "sk": "META"},
            {"workspaceId": ws_id, "sk": f"MEMBER#{user_id}"},
        ]
    }
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
    mock_ws_table.get_item.return_value = {
        "Item": {"workspaceId": "ws-r1", "sk": "META", "memory_id": "existing-mem"}
    }
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
    mock_ws_table.get_item.return_value = {"Item": {"workspaceId": "ws-r2", "sk": "META"}}
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
    mock_ws_table.get_item.return_value = {"Item": {"workspaceId": "ws-r3", "sk": "META"}}
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
            {
                "workspaceId": "ws-a",
                "sk": "META",
                "name": "Alice WS",
                "owner_id": "uid-a",
                "created_at": "t1",
            },
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


def test_list_all_workspaces_propagates_admin_err(mock_jwt, mock_ws_table, mock_admin_check):
    """If check_platform_admin returns an error response, propagate it."""
    from shared.response import forbidden

    mock_admin_check.return_value = (None, False, forbidden())
    resp = _invoke(_apigw("GET", "/api/admin/workspaces"))
    assert resp["statusCode"] == 403
    mock_ws_table.scan.assert_not_called()


def test_list_all_workspaces_hydrates_owner_via_cognito(mock_jwt, mock_ws_table, mock_admin_check):
    """Admin list hydrates owner_name/email from Cognito when pool configured."""
    mock_admin_check.return_value = ("admin-uid", True, None)
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "ws-h1", "sk": "META", "name": "H1", "owner_id": "uid-h"}]
    }
    fake_cogn = MagicMock()
    fake_cogn.admin_get_user.return_value = {
        "UserAttributes": [
            {"Name": "name", "Value": "Alice"},
            {"Name": "email", "Value": "alice@example.com"},
        ],
    }
    # Reset module-level cache so cognito is actually called.
    with (
        patch("crud.workspaces._identity_cache", {}),
        patch("crud.workspaces._get_cognito", return_value=fake_cogn),
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool-123"),
    ):
        resp = _invoke(_apigw("GET", "/api/admin/workspaces"))
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    item = body["items"][0]
    assert item["owner_name"] == "Alice"
    assert item["owner_email"] == "alice@example.com"


def test_list_all_workspaces_owner_hydration_handles_cognito_failure(
    mock_jwt, mock_ws_table, mock_admin_check
):
    """Cognito error per-uid → fall back to truncated user_id."""
    mock_admin_check.return_value = ("admin-uid", True, None)
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "ws-h2", "sk": "META", "name": "H2", "owner_id": "uid-failure"}]
    }
    fake_cogn = MagicMock()
    fake_cogn.admin_get_user.side_effect = RuntimeError("denied")
    with (
        patch("crud.workspaces._identity_cache", {}),
        patch("crud.workspaces._get_cognito", return_value=fake_cogn),
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool-123"),
    ):
        resp = _invoke(_apigw("GET", "/api/admin/workspaces"))
    body = json.loads(resp["body"])
    item = body["items"][0]
    # Falls back to truncated id (first 8 chars)
    assert item["owner_name"] == "uid-fail"
    assert item["owner_email"] == ""


# ── GET /api/workspaces (per-user list) ────────────────────────


def test_list_workspaces_returns_only_user_member_records(mock_jwt, mock_ws_table):
    user_id = mock_jwt.return_value["sub"]
    mock_ws_table.query.return_value = {
        "Items": [
            {"workspaceId": "ws-1", "userId": user_id, "role": "owner"},
            {"workspaceId": "ws-2", "userId": user_id, "role": "viewer"},
        ]
    }

    def _get_item(Key, **kw):
        return {
            "Item": {
                "workspaceId": Key["workspaceId"],
                "sk": "META",
                "name": f"name-{Key['workspaceId']}",
                "owner_id": user_id,
                "created_at": "t",
            }
        }

    mock_ws_table.get_item.side_effect = _get_item

    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp = _invoke(_apigw("GET", "/api/workspaces"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    ids = sorted(w["workspaceId"] for w in body["items"])
    assert ids == ["ws-1", "ws-2"]


def test_list_workspaces_skips_member_records_with_no_meta(mock_jwt, mock_ws_table):
    """If META was deleted but MEMBER row still exists, skip it (not crash)."""
    user_id = mock_jwt.return_value["sub"]
    mock_ws_table.query.return_value = {
        "Items": [{"workspaceId": "ws-orphan", "userId": user_id, "role": "viewer"}]
    }
    mock_ws_table.get_item.return_value = {}  # no Item

    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp = _invoke(_apigw("GET", "/api/workspaces"))
    body = json.loads(resp["body"])
    assert resp["statusCode"] == 200
    assert body["items"] == []


def test_list_workspaces_requires_jwt(mock_ws_table):
    """Missing/invalid JWT → 403 from auth_check."""
    # No mock_jwt fixture; verify_jwt isn't patched, so it'll fail.
    with patch("shared.middleware.verify_jwt", side_effect=Exception("bad token")):
        resp = _invoke(_apigw("GET", "/api/workspaces"))
    assert resp["statusCode"] == 403


# ── POST /api/workspaces validation ─────────────────────────────


def test_create_workspace_rejects_empty_name(mock_jwt, mock_ws_table, mock_agentcore_control):
    resp = _invoke(_apigw("POST", "/api/workspaces", body={"name": ""}))
    assert resp["statusCode"] == 400
    assert "name is required" in json.loads(resp["body"])["error"]


def test_create_workspace_rejects_long_name(mock_jwt, mock_ws_table, mock_agentcore_control):
    resp = _invoke(_apigw("POST", "/api/workspaces", body={"name": "x" * 101}))
    assert resp["statusCode"] == 400
    assert "100 characters" in json.loads(resp["body"])["error"]


def test_create_workspace_rejects_duplicate_name(mock_jwt, mock_ws_table, mock_agentcore_control):
    """Existing workspace with same (case-folded) name → 409."""
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "ws-existing", "name": "Workspace"}],
        "LastEvaluatedKey": None,
    }
    resp = _invoke(_apigw("POST", "/api/workspaces", body={"name": "WORKSPACE"}))
    assert resp["statusCode"] == 409
    body = json.loads(resp["body"])
    assert body["code"] == "WORKSPACE_NAME_DUPLICATE"


def test_create_workspace_persists_description(mock_jwt, mock_ws_table, mock_agentcore_control):
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "mem"}}
    resp = _invoke(_apigw("POST", "/api/workspaces", body={"name": "WSDesc", "description": "hello"}))
    assert resp["statusCode"] == 201
    # Inspect the FIRST put_item call (META item) for description.
    put_calls = list(mock_ws_table.put_item.call_args_list)
    meta_item = put_calls[0].kwargs["Item"]
    assert meta_item["description"] == "hello"


# ── POST /api/onboarding extra cases ─────────────────────────────


def test_onboarding_uses_email_prefix_as_default_name(mock_jwt, mock_ws_table, mock_agentcore_control):
    """When Cognito returns an email, the default name uses its prefix."""
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "mem-x"}}
    fake_cogn = MagicMock()
    fake_cogn.admin_get_user.return_value = {
        "UserAttributes": [{"Name": "email", "Value": "bob@example.com"}],
    }
    with (
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool-x"),
        patch("crud.workspaces._get_cognito", return_value=fake_cogn),
    ):
        resp = _invoke(_apigw("POST", "/api/onboarding"))
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["name"] == "bob"


def test_onboarding_dedups_with_email_domain(mock_jwt, mock_ws_table, mock_agentcore_control):
    """When the email-prefix name already exists, we add (domain) suffix."""
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "mem-y"}}
    fake_cogn = MagicMock()
    fake_cogn.admin_get_user.return_value = {
        "UserAttributes": [{"Name": "email", "Value": "bob@example.com"}],
    }
    # First scan call: claim "bob" exists. Subsequent: empty.
    scan_responses = iter(
        [
            {"Items": [{"workspaceId": "wso", "name": "bob"}], "LastEvaluatedKey": None},
            {"Items": [], "LastEvaluatedKey": None},
        ]
    )
    mock_ws_table.scan.side_effect = lambda **kw: next(scan_responses)
    with (
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool-y"),
        patch("crud.workspaces._get_cognito", return_value=fake_cogn),
    ):
        resp = _invoke(_apigw("POST", "/api/onboarding"))
    body = json.loads(resp["body"])
    assert body["name"] == "bob(example)"


def test_onboarding_handles_transaction_cancelled_with_existing_ws(
    mock_jwt, mock_ws_table, mock_agentcore_control
):
    """If the user already has a workspace, return it instead of failing."""
    user_id = mock_jwt.return_value["sub"]
    tcc = type("TransactionCanceledException", (Exception,), {})
    mock_ws_table.meta.client.exceptions.TransactionCanceledException = tcc
    mock_ws_table.meta.client.transact_write_items.side_effect = tcc()
    mock_ws_table.query.return_value = {"Items": [{"workspaceId": "ws-existing", "userId": user_id}]}
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": "ws-existing",
            "sk": "META",
            "name": "Existing",
            "description": "old",
            "created_at": "t-old",
        }
    }
    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp = _invoke(_apigw("POST", "/api/onboarding"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["workspaceId"] == "ws-existing"
    assert body["onboarding"] is False


def test_onboarding_returns_400_when_transaction_cancels_with_no_existing_ws(
    mock_jwt, mock_ws_table, mock_agentcore_control
):
    tcc = type("TransactionCanceledException", (Exception,), {})
    mock_ws_table.meta.client.exceptions.TransactionCanceledException = tcc
    mock_ws_table.meta.client.transact_write_items.side_effect = tcc()
    mock_ws_table.query.return_value = {"Items": []}
    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp = _invoke(_apigw("POST", "/api/onboarding"))
    assert resp["statusCode"] == 400


def test_onboarding_cognito_failure_falls_back_to_default_name(
    mock_jwt, mock_ws_table, mock_agentcore_control
):
    """If admin_get_user blows up, just use 'My Workspace'."""
    mock_agentcore_control.create_memory.return_value = {"memory": {"id": "mem-z"}}
    fake_cogn = MagicMock()
    fake_cogn.admin_get_user.side_effect = RuntimeError("no user")
    with (
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool-z"),
        patch("crud.workspaces._get_cognito", return_value=fake_cogn),
    ):
        resp = _invoke(_apigw("POST", "/api/onboarding"))
    body = json.loads(resp["body"])
    assert body["name"] == "My Workspace"


# ── GET /api/workspaces/{wsId} ────────────────────────────────


@pytest.fixture
def _mw_owner_membership(workspace_id, user_id):
    """Patch get_membership at middleware call-site for owner role."""
    with patch("shared.middleware.get_membership") as mock:
        mock.return_value = {
            "workspaceId": workspace_id,
            "userId": user_id,
            "role": "owner",
        }
        yield mock


def test_get_workspace_success_returns_meta_and_members(
    mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id
):
    user_id = mock_jwt.return_value["sub"]
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": "META",
            "name": "WS",
            "description": "d",
            "owner_id": user_id,
            "created_at": "t1",
            "updated_at": "t2",
            "memory_id": "m1",
        }
    }
    mock_ws_table.query.return_value = {
        "Items": [
            {
                "userId": user_id,
                "role": "owner",
                "joined_at": "t1",
                "display_name": "Bob",
                "email": "b@x.com",
            },
            {"userId": "u2", "role": "viewer", "joined_at": "t3"},
        ]
    }
    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["workspaceId"] == workspace_id
    assert body["memory_id"] == "m1"
    assert len(body["members"]) == 2


def test_get_workspace_returns_403_when_meta_missing(
    mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id
):
    """Auth passes but META no longer exists → forbidden()."""
    mock_ws_table.get_item.return_value = {}
    resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}"))
    assert resp["statusCode"] == 403


# ── PUT /api/workspaces/{wsId} ────────────────────────────────


@pytest.fixture
def _mw_admin_membership(workspace_id, user_id):
    with patch("shared.middleware.get_membership") as mock:
        mock.return_value = {
            "workspaceId": workspace_id,
            "userId": user_id,
            "role": "admin",
        }
        yield mock


def test_update_workspace_success(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    """Happy path: name + description + matching expected_updated_at → 200."""
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}",
            body={"name": "NewName", "description": "d", "expected_updated_at": "t-prev"},
        )
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["name"] == "NewName"


def test_update_workspace_rejects_empty_name(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}",
            body={"name": "  ", "expected_updated_at": "t"},
        )
    )
    assert resp["statusCode"] == 400


def test_update_workspace_rejects_long_name(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}",
            body={"name": "x" * 101, "expected_updated_at": "t"},
        )
    )
    assert resp["statusCode"] == 400


def test_update_workspace_requires_expected_updated_at(
    mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id
):
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}",
            body={"name": "X"},
        )
    )
    assert resp["statusCode"] == 400


def test_update_workspace_duplicate_name_returns_409(
    mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id
):
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "other-ws", "name": "Taken"}],
        "LastEvaluatedKey": None,
    }
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}",
            body={"name": "Taken", "expected_updated_at": "t"},
        )
    )
    assert resp["statusCode"] == 409


def test_update_workspace_self_rename_allowed(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    """Existing name on the SAME workspace doesn't count as a duplicate."""
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": workspace_id, "name": "Same"}],
        "LastEvaluatedKey": None,
    }
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}",
            body={"name": "Same", "expected_updated_at": "t"},
        )
    )
    assert resp["statusCode"] == 200


def test_update_workspace_version_conflict(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    """ConditionalCheckFailedException → 409 version conflict."""
    ccf = type("ConditionalCheckFailedException", (Exception,), {})
    mock_ws_table.meta.client.exceptions.ConditionalCheckFailedException = ccf
    mock_ws_table.update_item.side_effect = ccf()
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}",
            body={"name": "X", "expected_updated_at": "stale"},
        )
    )
    assert resp["statusCode"] == 409


# ── DELETE /api/workspaces/{wsId} (additional cases) ────────────


def test_delete_workspace_paginates_query_loop(mock_jwt, mock_ws_table, mock_agentcore_control):
    """delete_workspace pages through DDB and stops when LastEvaluatedKey is gone."""
    user_id = mock_jwt.return_value["sub"]
    with patch("shared.middleware.get_membership") as mw:
        mw.return_value = {"role": "owner", "userId": user_id}
        mock_ws_table.get_item.return_value = {
            "Item": {
                "workspaceId": "ws-pg",
                "sk": "META",
                "owner_id": user_id,
            }
        }
        # Two pages; second has no LastEvaluatedKey.
        responses = iter(
            [
                {
                    "Items": [{"workspaceId": "ws-pg", "sk": "META"}],
                    "LastEvaluatedKey": {"workspaceId": "ws-pg", "sk": "META"},
                },
                {"Items": [{"workspaceId": "ws-pg", "sk": "MEMBER#u"}], "LastEvaluatedKey": None},
            ]
        )
        mock_ws_table.query.side_effect = lambda **kw: next(responses)
        batch = MagicMock()
        mock_ws_table.batch_writer.return_value.__enter__ = MagicMock(return_value=batch)
        mock_ws_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)
        resp = _invoke(_apigw("DELETE", "/api/workspaces/ws-pg"))
    assert resp["statusCode"] == 200
    assert mock_ws_table.query.call_count == 2


# ── POST /api/workspaces/{wsId}/members (invite) ────────────────


def test_invite_member_success_as_admin(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/members",
            body={"email": "x@y.com", "role": "viewer"},
        )
    )
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["email"] == "x@y.com"
    assert body["role"] == "viewer"
    assert body["token"]


def test_invite_member_rejects_missing_email(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/members",
            body={"role": "viewer"},
        )
    )
    assert resp["statusCode"] == 400


def test_invite_member_rejects_invalid_role(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/members",
            body={"email": "x@y.com", "role": "owner"},
        )
    )
    assert resp["statusCode"] == 400


def test_invite_member_admin_cannot_invite_admin(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    """Only owners may invite admins."""
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/members",
            body={"email": "x@y.com", "role": "admin"},
        )
    )
    assert resp["statusCode"] == 403


def test_invite_member_owner_can_invite_admin(mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/members",
            body={"email": "z@y.com", "role": "admin"},
        )
    )
    assert resp["statusCode"] == 201


# ── PUT /api/workspaces/{wsId}/members/{memberId} ───────────────


def test_update_member_role_success(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    member_id = "abc123"
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": f"MEMBER#{member_id}",
            "role": "viewer",
        }
    }
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/members/{member_id}",
            body={"role": "editor"},
        )
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["role"] == "editor"


def test_update_member_role_rejects_invalid_id(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    """Member id with disallowed chars (slash) → 400 from validate_id."""
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/members/bad..id",
            body={"role": "editor"},
        )
    )
    # `.` is rejected by ID_PATTERN.
    assert resp["statusCode"] == 400


def test_update_member_role_rejects_invalid_role(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/members/u1",
            body={"role": "godmode"},
        )
    )
    assert resp["statusCode"] == 400


def test_update_member_role_target_not_found(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.get_item.return_value = {}
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/members/u1",
            body={"role": "editor"},
        )
    )
    assert resp["statusCode"] == 403


def test_update_member_role_cannot_demote_owner(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": "MEMBER#u1",
            "role": "owner",
        }
    }
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/members/u1",
            body={"role": "editor"},
        )
    )
    assert resp["statusCode"] == 403


def test_update_member_role_admin_cannot_promote_to_admin(
    mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id
):
    """Admin caller cannot promote anyone to admin (only owner can)."""
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": "MEMBER#u1",
            "role": "viewer",
        }
    }
    resp = _invoke(
        _apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/members/u1",
            body={"role": "admin"},
        )
    )
    assert resp["statusCode"] == 403


# ── DELETE /api/workspaces/{wsId}/members/{memberId} ────────────


def test_remove_member_success(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": "MEMBER#u1",
            "role": "viewer",
        }
    }
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/members/u1"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["removed"] is True


def test_remove_member_rejects_invalid_id(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/members/bad..id"))
    assert resp["statusCode"] == 400


def test_remove_member_target_missing(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.get_item.return_value = {}
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/members/u1"))
    assert resp["statusCode"] == 403


def test_remove_member_cannot_remove_owner(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": "MEMBER#u1",
            "role": "owner",
        }
    }
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/members/u1"))
    assert resp["statusCode"] == 400


# ── POST /api/workspaces/{wsId}/leave ──────────────────────────


@pytest.fixture
def _mw_viewer_membership(workspace_id, user_id):
    with patch("shared.middleware.get_membership") as mock:
        mock.return_value = {
            "workspaceId": workspace_id,
            "userId": user_id,
            "role": "viewer",
        }
        yield mock


def test_leave_workspace_success(mock_jwt, mock_ws_table, _mw_viewer_membership, workspace_id):
    resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/leave"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["left"] is True


def test_leave_workspace_owner_cannot_leave(mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id):
    resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/leave"))
    assert resp["statusCode"] == 400


# ── POST /api/workspaces/{wsId}/transfer-ownership ─────────────


def test_transfer_ownership_success(mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id):
    target_id = "newowner1"
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": f"MEMBER#{target_id}",
            "role": "editor",
        }
    }
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/transfer-ownership",
            body={"targetUserId": target_id},
        )
    )
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["newOwnerId"] == target_id
    # Should fire 3 update_item calls in order.
    assert mock_ws_table.update_item.call_count == 3


def test_transfer_ownership_missing_target(mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/transfer-ownership",
            body={},
        )
    )
    assert resp["statusCode"] == 400


def test_transfer_ownership_invalid_target_id(mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id):
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/transfer-ownership",
            body={"targetUserId": "bad..id"},
        )
    )
    assert resp["statusCode"] == 400


def test_transfer_ownership_target_not_member(mock_jwt, mock_ws_table, _mw_owner_membership, workspace_id):
    mock_ws_table.get_item.return_value = {}
    resp = _invoke(
        _apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/transfer-ownership",
            body={"targetUserId": "ghost1"},
        )
    )
    assert resp["statusCode"] == 403


# ── GET /api/workspaces/{wsId}/invitations ───────────────────


def test_list_invitations_success(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.query.return_value = {
        "Items": [
            {"token": "tok-1", "email": "a@b.com", "role": "viewer", "invited_by": "u", "created_at": "t1"},
            {"token": "tok-2", "email": "c@b.com", "role": "editor", "invited_by": "u", "created_at": "t2"},
        ]
    }
    resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/invitations"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body["items"]) == 2


# ── DELETE /api/workspaces/{wsId}/invitations/{token} ───────


def test_revoke_invitation_success(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": workspace_id,
            "sk": "INVITE#tok-x",
            "token": "tok-x",
        }
    }
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/invitations/tok-x"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["revoked"] is True


def test_revoke_invitation_not_found(mock_jwt, mock_ws_table, _mw_admin_membership, workspace_id):
    mock_ws_table.get_item.return_value = {}
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/invitations/tok-x"))
    assert resp["statusCode"] == 403


# ── GET /api/invitations/{token} (unauthenticated) ─────────


def test_verify_invitation_success(mock_ws_table):
    """No JWT required; reads any token by scanning."""
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "ws-i", "sk": "INVITE#tok", "email": "x@y.com"}],
        "LastEvaluatedKey": None,
    }
    mock_ws_table.get_item.return_value = {"Item": {"name": "Cool WS"}}
    resp = _invoke(_apigw("GET", "/api/invitations/tok"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["workspaceName"] == "Cool WS"


def test_verify_invitation_not_found(mock_ws_table):
    """No matching invite → 404."""
    mock_ws_table.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
    resp = _invoke(_apigw("GET", "/api/invitations/missing"))
    assert resp["statusCode"] == 404


def test_verify_invitation_paginates_until_found(mock_ws_table):
    """First page empty, second page has the invite."""
    responses = iter(
        [
            {"Items": [], "LastEvaluatedKey": {"workspaceId": "x", "sk": "INVITE#xxx"}},
            {"Items": [{"workspaceId": "ws-pp", "sk": "INVITE#tok"}], "LastEvaluatedKey": None},
        ]
    )
    mock_ws_table.scan.side_effect = lambda **kw: next(responses)
    mock_ws_table.get_item.return_value = {"Item": {"name": "PagedWS"}}
    resp = _invoke(_apigw("GET", "/api/invitations/tok"))
    assert resp["statusCode"] == 200


# ── POST /api/invitations/{token}/accept ───────────────────


def test_accept_invitation_success(mock_jwt, mock_ws_table):
    mock_jwt.return_value["sub"]
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "ws-acc", "sk": "INVITE#tok", "role": "editor"}],
        "LastEvaluatedKey": None,
    }
    mock_ws_table.get_item.return_value = {}  # not yet a member
    resp = _invoke(_apigw("POST", "/api/invitations/tok/accept"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["workspaceId"] == "ws-acc"
    assert body["role"] == "editor"
    # Both put_item (MEMBER row) and delete_item (INVITE row) called.
    assert mock_ws_table.put_item.called
    assert mock_ws_table.delete_item.called


def test_accept_invitation_not_found(mock_jwt, mock_ws_table):
    mock_ws_table.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
    resp = _invoke(_apigw("POST", "/api/invitations/missing/accept"))
    assert resp["statusCode"] == 404


def test_accept_invitation_already_member(mock_jwt, mock_ws_table):
    user_id = mock_jwt.return_value["sub"]
    mock_ws_table.scan.return_value = {
        "Items": [{"workspaceId": "ws-acc", "sk": "INVITE#tok", "role": "editor"}],
        "LastEvaluatedKey": None,
    }
    mock_ws_table.get_item.return_value = {
        "Item": {
            "workspaceId": "ws-acc",
            "userId": user_id,
            "role": "viewer",
        }
    }
    resp = _invoke(_apigw("POST", "/api/invitations/tok/accept"))
    assert resp["statusCode"] == 400


def test_accept_invitation_paginates(mock_jwt, mock_ws_table):
    """First scan empty with continuation, second yields the invite."""
    responses = iter(
        [
            {"Items": [], "LastEvaluatedKey": {"workspaceId": "x", "sk": "INVITE#x"}},
            {
                "Items": [{"workspaceId": "ws-pa", "sk": "INVITE#tok", "role": "viewer"}],
                "LastEvaluatedKey": None,
            },
        ]
    )
    mock_ws_table.scan.side_effect = lambda **kw: next(responses)
    mock_ws_table.get_item.return_value = {}
    resp = _invoke(_apigw("POST", "/api/invitations/tok/accept"))
    assert resp["statusCode"] == 200


# ── _create_workspace_memory recovery via list_memories ────────


def test_create_workspace_memory_recovers_on_already_exists(mock_agentcore_control):
    """If create_memory says 'already exists', look it up via list_memories."""
    err = Exception("ResourceConflictException: memory already exists")
    mock_agentcore_control.create_memory.side_effect = err
    # safe_name for ws "abc-12345678" → "agentstudio_ws_abc_12345678"
    mock_agentcore_control.list_memories.return_value = {
        "memories": [{"id": "agentstudio_ws_abc_12345678_recovered"}],
    }
    from crud.workspaces import _create_workspace_memory

    result = _create_workspace_memory("abc-12345678")
    assert result == "agentstudio_ws_abc_12345678_recovered"


def test_create_workspace_memory_falls_through_when_lookup_finds_nothing(mock_agentcore_control):
    """already-exists but list_memories returns nothing → None."""
    err = Exception("ResourceConflictException: already exists")
    mock_agentcore_control.create_memory.side_effect = err
    mock_agentcore_control.list_memories.return_value = {"memories": []}
    from crud.workspaces import _create_workspace_memory

    result = _create_workspace_memory("xyz-99999999")
    assert result is None


# ── _find_memory_by_name ────────────────────────────────────


def test_find_memory_by_name_first_page(mock_agentcore_control):
    mock_agentcore_control.list_memories.return_value = {
        "memories": [
            {"id": "other-mem"},
            {"id": "agentstudio_ws_target_match"},
        ],
    }
    from crud.workspaces import _find_memory_by_name

    assert _find_memory_by_name("agentstudio_ws_target") == "agentstudio_ws_target_match"


def test_find_memory_by_name_paginates(mock_agentcore_control):
    """No match on first page → follow nextToken."""
    responses = iter(
        [
            {"memories": [{"id": "nope"}], "nextToken": "tok"},
            {"memories": [{"id": "agentstudio_ws_x_yes"}]},
        ]
    )
    mock_agentcore_control.list_memories.side_effect = lambda **kw: next(responses)
    from crud.workspaces import _find_memory_by_name

    assert _find_memory_by_name("agentstudio_ws_x") == "agentstudio_ws_x_yes"


def test_find_memory_by_name_no_match(mock_agentcore_control):
    mock_agentcore_control.list_memories.return_value = {"memories": [{"id": "x"}]}
    from crud.workspaces import _find_memory_by_name

    assert _find_memory_by_name("foo") is None


def test_find_memory_by_name_handles_exception(mock_agentcore_control):
    mock_agentcore_control.list_memories.side_effect = RuntimeError("ListError")
    from crud.workspaces import _find_memory_by_name

    assert _find_memory_by_name("any") is None


# ── _delete_workspace_memory ────────────────────────────────


def test_delete_workspace_memory_no_id_is_noop(mock_agentcore_control):
    from crud.workspaces import _delete_workspace_memory

    _delete_workspace_memory("")
    _delete_workspace_memory(None)
    mock_agentcore_control.delete_memory.assert_not_called()


def test_delete_workspace_memory_swallows_errors(mock_agentcore_control):
    from crud.workspaces import _delete_workspace_memory

    mock_agentcore_control.delete_memory.side_effect = Exception("boom")
    _delete_workspace_memory("mem-1")  # must not raise


# ── _hydrate_member_identities ──────────────────────────────


def test_hydrate_member_identities_pool_unset_returns_input():
    from crud.workspaces import _hydrate_member_identities

    members = [{"userId": "u1"}]
    with patch("crud.workspaces.COGNITO_USER_POOL_ID", ""):
        out = _hydrate_member_identities(members)
    assert out is members


def test_hydrate_member_identities_skips_already_named():
    from crud.workspaces import _hydrate_member_identities

    fake = MagicMock()
    members = [{"userId": "u1", "display_name": "set"}]
    with (
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool"),
        patch("crud.workspaces._get_cognito", return_value=fake),
    ):
        out = _hydrate_member_identities(members)
    assert out[0]["display_name"] == "set"
    fake.admin_get_user.assert_not_called()


def test_hydrate_member_identities_skips_missing_user_id():
    from crud.workspaces import _hydrate_member_identities

    fake = MagicMock()
    with (
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool"),
        patch("crud.workspaces._get_cognito", return_value=fake),
    ):
        out = _hydrate_member_identities([{"role": "viewer"}])
    assert out == [{"role": "viewer"}]
    fake.admin_get_user.assert_not_called()


def test_hydrate_member_identities_fetches_and_caches_cognito():
    from crud.workspaces import _hydrate_member_identities

    fake = MagicMock()
    fake.admin_get_user.return_value = {
        "UserAttributes": [
            {"Name": "name", "Value": "Carol"},
            {"Name": "email", "Value": "c@x.com"},
        ]
    }
    members = [{"userId": "u-fresh"}]
    with (
        patch("crud.workspaces._identity_cache", {}),
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool"),
        patch("crud.workspaces._get_cognito", return_value=fake),
    ):
        out = _hydrate_member_identities(members)
    assert out[0]["display_name"] == "Carol"
    assert out[0]["email"] == "c@x.com"


def test_hydrate_member_identities_uses_cache():
    from crud.workspaces import _hydrate_member_identities

    fake = MagicMock()
    cache = {"u-cached": {"display_name": "Cached", "email": "ca@x.com"}}
    with (
        patch("crud.workspaces._identity_cache", cache),
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool"),
        patch("crud.workspaces._get_cognito", return_value=fake),
    ):
        out = _hydrate_member_identities([{"userId": "u-cached"}])
    assert out[0]["display_name"] == "Cached"
    fake.admin_get_user.assert_not_called()


def test_hydrate_member_identities_handles_failure_per_user():
    from crud.workspaces import _hydrate_member_identities

    fake = MagicMock()
    fake.admin_get_user.side_effect = RuntimeError("denied")
    with (
        patch("crud.workspaces._identity_cache", {}),
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool"),
        patch("crud.workspaces._get_cognito", return_value=fake),
    ):
        out = _hydrate_member_identities([{"userId": "u-bad"}])
    # On failure, member dict passes through unchanged.
    assert out == [{"userId": "u-bad"}]


# ── _hydrate_owner_identities ────────────────────────────────


def test_hydrate_owner_identities_noop_for_empty_list():
    from crud.workspaces import _hydrate_owner_identities

    workspaces = []
    _hydrate_owner_identities(workspaces)
    assert workspaces == []


def test_hydrate_owner_identities_uses_cache():
    from crud.workspaces import _hydrate_owner_identities

    cache = {"u-ow": {"display_name": "Carl", "email": "c@x.com"}}
    workspaces = [{"workspaceId": "w", "owner_id": "u-ow"}]
    with (
        patch("crud.workspaces._identity_cache", cache),
        patch("crud.workspaces.COGNITO_USER_POOL_ID", "pool"),
    ):
        _hydrate_owner_identities(workspaces)
    assert workspaces[0]["owner_name"] == "Carl"
    assert workspaces[0]["owner_email"] == "c@x.com"


# ── _workspace_name_exists pagination ────────────────────────


def test_workspace_name_exists_paginated_match():
    """Match found on the second page; loop continues then returns True."""
    from crud.workspaces import _workspace_name_exists

    table = MagicMock()
    responses = iter(
        [
            {
                "Items": [{"workspaceId": "wA", "name": "Alpha"}],
                "LastEvaluatedKey": {"workspaceId": "wA", "sk": "META"},
            },
            {"Items": [{"workspaceId": "wB", "name": "Target"}], "LastEvaluatedKey": None},
        ]
    )
    table.scan.side_effect = lambda **kw: next(responses)
    assert _workspace_name_exists(table, "target") is True


def test_workspace_name_exists_no_match():
    from crud.workspaces import _workspace_name_exists

    table = MagicMock()
    table.scan.return_value = {"Items": [{"workspaceId": "wA", "name": "Alpha"}], "LastEvaluatedKey": None}
    assert _workspace_name_exists(table, "delta") is False


def test_workspace_name_exists_excludes_self():
    from crud.workspaces import _workspace_name_exists

    table = MagicMock()
    table.scan.return_value = {
        "Items": [{"workspaceId": "ws-self", "name": "Same"}],
        "LastEvaluatedKey": None,
    }
    # exclude self → not a duplicate
    assert _workspace_name_exists(table, "Same", exclude_ws_id="ws-self") is False
    assert _workspace_name_exists(table, "Same", exclude_ws_id="ws-other") is True


# ── lazy boto3 client creation ────────────────────────────────


def test_get_table_initializes_lazily(monkeypatch):
    monkeypatch.setattr("crud.workspaces._table", None)
    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table
    with patch("crud.workspaces.boto3.resource", return_value=fake_resource) as bt:
        from crud.workspaces import _get_table

        out = _get_table()
        out2 = _get_table()
    assert out is fake_table
    assert out2 is fake_table  # cached
    assert bt.call_count == 1


def test_get_cognito_initializes_lazily(monkeypatch):
    monkeypatch.setattr("crud.workspaces._cognito", None)
    fake_client = MagicMock()
    with patch("crud.workspaces.boto3.client", return_value=fake_client) as bc:
        from crud.workspaces import _get_cognito

        c = _get_cognito()
        c2 = _get_cognito()
    assert c is fake_client
    assert c2 is fake_client
    assert bc.call_count == 1


def test_get_control_initializes_lazily(monkeypatch):
    monkeypatch.setattr("crud.workspaces._control", None)
    fake_client = MagicMock()
    with patch("crud.workspaces.boto3.client", return_value=fake_client) as bc:
        from crud.workspaces import _get_control

        c = _get_control()
        c2 = _get_control()
    assert c is fake_client
    assert c2 is fake_client
    assert bc.call_count == 1


def test_get_iam_client_initializes_lazily(monkeypatch):
    monkeypatch.setattr("crud.workspaces._iam_client", None)
    fake_client = MagicMock()
    with patch("crud.workspaces.boto3.client", return_value=fake_client) as bc:
        from crud.workspaces import _get_iam_client

        c = _get_iam_client()
        c2 = _get_iam_client()
    assert c is fake_client
    assert c2 is fake_client
    assert bc.call_count == 1

"""Tests for crud/a2a_keys.py — per-user-per-agent API key management."""
import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("A2A_KEYS_TABLE", "test-a2a-keys")
    import importlib

    import shared.config as _cfg
    importlib.reload(_cfg)


def _event(method, ws_id, agent_id, key_id=None, body=None):
    path = f"/api/workspaces/{ws_id}/agents/{agent_id}/a2a-keys"
    resource = "/api/workspaces/{wsId}/agents/{agentId}/a2a-keys"
    path_params = {"wsId": ws_id, "agentId": agent_id}
    if key_id is not None:
        path = f"{path}/{key_id}"
        resource = f"{resource}/{{keyId}}"
        path_params["keyId"] = key_id
    return {
        "httpMethod": method,
        "path": path,
        "resource": resource,
        "pathParameters": path_params,
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_create_key_returns_plaintext_once(mock_jwt, user_id, workspace_id):
    """POST creates a key, returns the plaintext apiKey ONCE. DDB stores only hash."""
    from crud.handler import app

    fake_table = MagicMock()
    fake_table.put_item.return_value = {}
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": workspace_id}
        resp = app.resolve(_event("POST", workspace_id, "agt-1"), MagicMock())

    assert resp["statusCode"] == 201
    data = json.loads(resp["body"])
    assert "apiKey" in data
    assert data["apiKey"].startswith("as_")
    assert "keyId" in data
    assert "keyPrefix" in data
    assert len(data["keyPrefix"]) == 8

    call = fake_table.put_item.call_args.kwargs["Item"]
    assert "apiKey" not in call
    assert call["apiKeyHash"] == hashlib.sha256(data["apiKey"].encode()).hexdigest()
    assert call["userId"] == user_id
    assert call["agentId"] == "agt-1"


def test_list_keys_hides_hash_and_raw(mock_jwt, user_id, workspace_id):
    """GET returns metadata only — no apiKey, no apiKeyHash in response."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [
            {
                "apiKeyHash": "h1",
                "keyId": "k1",
                "keyPrefix": "as_aaaa",
                "userId": user_id,
                "agentId": "agt-1",
                "createdAt": "2026-04-19T00:00:00Z",
                "revoked": False,
            }
        ]
    }
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": workspace_id}
        resp = app.resolve(_event("GET", workspace_id, "agt-1"), MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    keys = data["keys"]
    assert len(keys) == 1
    assert "apiKey" not in keys[0]
    assert "apiKeyHash" not in keys[0]
    assert keys[0]["keyId"] == "k1"
    assert keys[0]["keyPrefix"] == "as_aaaa"


def test_revoke_key_sets_flag(mock_jwt, user_id, workspace_id):
    """DELETE sets revoked=true (keeps row for audit), not hard delete."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": [{"apiKeyHash": "h1", "keyId": "k1", "userId": user_id, "agentId": "agt-1"}]}
    fake_table.update_item.return_value = {}

    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": workspace_id}
        resp = app.resolve(_event("DELETE", workspace_id, "agt-1", key_id="k1"), MagicMock())

    assert resp["statusCode"] == 200
    update_call = fake_table.update_item.call_args.kwargs
    assert update_call["UpdateExpression"] == "SET revoked = :r"
    assert update_call["ExpressionAttributeValues"][":r"] is True


def test_user_cannot_revoke_other_users_key(mock_jwt, user_id, workspace_id):
    """DELETE rejects if key belongs to a different user — 403 or 404."""
    from crud.handler import app
    fake_table = MagicMock()
    # GSI query scoped to userId#agentId — so other user's key won't show up.
    fake_table.query.return_value = {"Items": []}
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": workspace_id}
        resp = app.resolve(_event("DELETE", workspace_id, "agt-1", key_id="k1"), MagicMock())
    # GSI query scoped to this user's keys returns nothing → 404 is the
    # canonical response (the key doesn't exist from this user's view).
    assert resp["statusCode"] in (403, 404)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_generate_key_returns_unique_values():
    from crud.a2a_keys import _generate_key
    p1, h1, prefix1 = _generate_key()
    p2, h2, prefix2 = _generate_key()
    assert p1.startswith("as_")
    assert p2.startswith("as_")
    assert p1 != p2
    assert h1 != h2
    assert prefix1 == p1[:8]
    assert hashlib.sha256(p1.encode()).hexdigest() == h1
    # All chars from the allowed charset
    from crud.a2a_keys import _KEY_CHARSET
    for c in p1[3:]:
        assert c in _KEY_CHARSET


def test_get_table_lazy_init():
    import crud.a2a_keys as mod
    mod._table = None
    sentinel_table = MagicMock()
    sentinel_resource = MagicMock()
    sentinel_resource.Table.return_value = sentinel_table
    with patch("boto3.resource", return_value=sentinel_resource) as br:
        assert mod._get_table() is sentinel_table
        # Cached
        assert mod._get_table() is sentinel_table
        br.assert_called_once()
    mod._table = None


def test_get_agent_item_lazy_init():
    import crud.a2a_keys as mod
    mod._agents_table = None
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": {"agentId": "x"}}
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table
    with patch("boto3.resource", return_value=fake_resource):
        assert mod._get_agent_item("x") == {"agentId": "x"}
    # Second call uses cached
    with patch("boto3.resource") as br:
        mod._get_agent_item("x")
        br.assert_not_called()
    mod._agents_table = None


def test_get_agent_item_returns_none_for_missing():
    import crud.a2a_keys as mod
    mod._agents_table = None
    fake_table = MagicMock()
    fake_table.get_item.return_value = {}
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table
    with patch("boto3.resource", return_value=fake_resource):
        assert mod._get_agent_item("x") is None
    mod._agents_table = None


# ---------------------------------------------------------------------------
# Per-agent endpoints — auth/validation paths
# ---------------------------------------------------------------------------


def test_create_key_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_event("POST", workspace_id, "bad agt!"), MagicMock())
    assert resp["statusCode"] == 400


def test_create_key_agent_not_in_workspace(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": "other-ws"}
        resp = app.resolve(_event("POST", workspace_id, "agt-1"), MagicMock())
    assert resp["statusCode"] == 403


def test_create_key_agent_not_found(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = None
        resp = app.resolve(_event("POST", workspace_id, "agt-1"), MagicMock())
    assert resp["statusCode"] == 403


def test_create_key_auth_check_fails(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_event("POST", workspace_id, "agt-1"), MagicMock())
    assert resp["statusCode"] == 403


def test_list_keys_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_event("GET", workspace_id, "bad agt!"), MagicMock())
    assert resp["statusCode"] == 400


def test_list_keys_agent_not_in_workspace(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": "other-ws"}
        resp = app.resolve(_event("GET", workspace_id, "agt-1"), MagicMock())
    assert resp["statusCode"] == 403


def test_list_keys_auth_check_fails(workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_event("GET", workspace_id, "agt-1"), MagicMock())
    assert resp["statusCode"] == 403


def test_revoke_key_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_event("DELETE", workspace_id, "bad agt!", key_id="k1"), MagicMock())
    assert resp["statusCode"] == 400


def test_revoke_key_agent_not_in_workspace(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": "other-ws"}
        resp = app.resolve(_event("DELETE", workspace_id, "agt-1", key_id="k1"), MagicMock())
    assert resp["statusCode"] == 403


def test_revoke_key_auth_check_fails(workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_event("DELETE", workspace_id, "agt-1", key_id="k1"), MagicMock())
    assert resp["statusCode"] == 403


def test_revoke_key_user_id_mismatch(mock_jwt, user_id, workspace_id):
    """A row is found but userId doesn't match — should return 403.
    This shouldn't normally happen because GSI query scopes by user, but
    defensive code path handles mismatched DB entries."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [{
            "apiKeyHash": "h1",
            "keyId": "k1",
            "userId": "different-user",
            "agentId": "agt-1",
        }]
    }
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys._get_agent_item") as ga, \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-1", "workspace_id": workspace_id}
        resp = app.resolve(_event("DELETE", workspace_id, "agt-1", key_id="k1"), MagicMock())
    assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# Meta-Agent endpoints
# ---------------------------------------------------------------------------


def _meta_event(method, ws_id, key_id=None):
    path = f"/api/workspaces/{ws_id}/meta-agent/a2a-keys"
    resource = "/api/workspaces/{wsId}/meta-agent/a2a-keys"
    path_params = {"wsId": ws_id}
    if key_id is not None:
        path = f"{path}/{key_id}"
        resource = f"{resource}/{{keyId}}"
        path_params["keyId"] = key_id
    return {
        "httpMethod": method,
        "path": path,
        "resource": resource,
        "pathParameters": path_params,
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_create_meta_key_success(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.put_item.return_value = {}
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_meta_event("POST", workspace_id), MagicMock())
    assert resp["statusCode"] == 201
    data = json.loads(resp["body"])
    assert data["apiKey"].startswith("as_")
    assert "keyId" in data
    assert data["keyPrefix"] == data["apiKey"][:8]
    item = fake_table.put_item.call_args.kwargs["Item"]
    assert item["agentId"] == "meta-agent"
    assert item["userAgentKey"] == f"{user_id}#meta-agent"
    assert item["revoked"] is False


def test_create_meta_key_auth_fails(workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_meta_event("POST", workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_list_meta_keys_returns_metadata(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [
            {
                "apiKeyHash": "h1",
                "keyId": "k1",
                "keyPrefix": "as_aaa1",
                "userId": user_id,
                "agentId": "meta-agent",
                "createdAt": "2026-04-19T00:00:00Z",
                "revoked": False,
            },
            {
                "apiKeyHash": "h2",
                "keyId": "k2",
                "keyPrefix": "as_aaa2",
                "userId": user_id,
                "agentId": "meta-agent",
                "createdAt": "2026-04-20T00:00:00Z",
                "revoked": True,
            },
        ]
    }
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_meta_event("GET", workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert len(data["keys"]) == 2
    assert data["keys"][0]["keyId"] == "k1"
    assert data["keys"][1]["revoked"] is True
    assert "apiKeyHash" not in data["keys"][0]


def test_list_meta_keys_auth_fails(workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_meta_event("GET", workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_revoke_meta_key_success(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [{"apiKeyHash": "h1", "keyId": "k1", "userId": user_id}]
    }
    fake_table.update_item.return_value = {}
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_meta_event("DELETE", workspace_id, key_id="k1"), MagicMock())
    assert resp["statusCode"] == 200
    update_call = fake_table.update_item.call_args.kwargs
    assert update_call["UpdateExpression"] == "SET revoked = :r"


def test_revoke_meta_key_not_found(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": []}
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_meta_event("DELETE", workspace_id, key_id="k1"), MagicMock())
    assert resp["statusCode"] == 404


def test_revoke_meta_key_user_id_mismatch(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [{"apiKeyHash": "h1", "keyId": "k1", "userId": "other-user"}]
    }
    with patch("crud.a2a_keys._get_table", return_value=fake_table), \
         patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_meta_event("DELETE", workspace_id, key_id="k1"), MagicMock())
    assert resp["statusCode"] == 403


def test_revoke_meta_key_auth_fails(workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.a2a_keys.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_meta_event("DELETE", workspace_id, key_id="k1"), MagicMock())
    assert resp["statusCode"] == 403

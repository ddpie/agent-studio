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

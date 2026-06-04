"""Tests for crud.secrets — agent secrets via AWS Secrets Manager."""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "")


@pytest.fixture
def mock_sm():
    with patch("crud.secrets._get_sm") as g:
        s = MagicMock()
        s.exceptions.ResourceNotFoundException = type("ResourceNotFoundException", (Exception,), {})
        g.return_value = s
        yield s


@pytest.fixture
def mock_agents_table():
    with patch("crud.secrets._get_agents_table") as g:
        t = MagicMock()
        # default: agent exists in workspace
        t.get_item.return_value = {"Item": None}
        g.return_value = t
        yield t


@pytest.fixture
def _mock_admin(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "admin",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_editor(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "editor",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


def _apigw(method, path, body=None):
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
            "Content-Type": "application/json",
        },
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": {},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler

    return lambda_handler(event, MagicMock())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_secret_path_with_key(self):
        from crud.secrets import _secret_path

        assert _secret_path("ws1", "agt1", "API_KEY") == "agent-studio/ws1/agt1/API_KEY"

    def test_secret_path_without_key(self):
        from crud.secrets import _secret_path

        assert _secret_path("ws1", "agt1") == "agent-studio/ws1/agt1"

    def test_verify_agent_ownership_match(self, mock_agents_table):
        from crud.secrets import _verify_agent_ownership

        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": "ws1"}}
        assert _verify_agent_ownership("agt1", "ws1") is True

    def test_verify_agent_ownership_wrong_workspace(self, mock_agents_table):
        from crud.secrets import _verify_agent_ownership

        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": "other-ws"}}
        assert _verify_agent_ownership("agt1", "ws1") is False

    def test_verify_agent_ownership_no_item(self, mock_agents_table):
        from crud.secrets import _verify_agent_ownership

        mock_agents_table.get_item.return_value = {}
        assert _verify_agent_ownership("agt1", "ws1") is False


# ---------------------------------------------------------------------------
# Lazy init
# ---------------------------------------------------------------------------


class TestLazyInit:
    def test_get_sm_caches(self):
        import crud.secrets as mod

        mod._sm = None
        sentinel = MagicMock()
        with patch("boto3.client", return_value=sentinel) as bc:
            assert mod._get_sm() is sentinel
            assert mod._get_sm() is sentinel
            bc.assert_called_once()
        mod._sm = None

    def test_get_agents_table_caches(self):
        import crud.secrets as mod

        mod._agents_table = None
        sentinel = MagicMock()
        sentinel_table = MagicMock()
        sentinel.Table.return_value = sentinel_table
        with patch("boto3.resource", return_value=sentinel) as br:
            assert mod._get_agents_table() is sentinel_table
            assert mod._get_agents_table() is sentinel_table
            br.assert_called_once()
        mod._agents_table = None


# ---------------------------------------------------------------------------
# GET /api/workspaces/{wsId}/agents/{agentId}/secrets
# ---------------------------------------------------------------------------


class TestListSecrets:
    def test_list_empty(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        # Paginator: a single page with no secrets
        paginator = MagicMock()
        paginator.paginate.return_value = [{"SecretList": []}]
        mock_sm.get_paginator.return_value = paginator

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/secrets"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["items"] == []

    def test_list_returns_keys(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        ts = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
        prefix = f"agent-studio/{workspace_id}/agt1/"
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "SecretList": [
                    {
                        "Name": f"{prefix}API_KEY",
                        "CreatedDate": ts,
                        "LastChangedDate": ts,
                    },
                    {
                        "Name": f"{prefix}DB_PASSWORD",
                        "CreatedDate": ts,
                        "LastChangedDate": ts,
                    },
                ],
            }
        ]
        mock_sm.get_paginator.return_value = paginator

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/secrets"))
        assert resp["statusCode"] == 200
        items = json.loads(resp["body"])["items"]
        assert len(items) == 2
        keys = {it["key"] for it in items}
        assert keys == {"API_KEY", "DB_PASSWORD"}
        assert items[0]["created_at"] == ts.isoformat()

    def test_list_skips_empty_key_suffix(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        prefix = f"agent-studio/{workspace_id}/agt1/"
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {
                "SecretList": [
                    # One with empty suffix - should be skipped
                    {"Name": f"{prefix}", "CreatedDate": "", "LastChangedDate": ""},
                    {"Name": f"{prefix}KEY1", "CreatedDate": "", "LastChangedDate": ""},
                ],
            }
        ]
        mock_sm.get_paginator.return_value = paginator

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/secrets"))
        assert resp["statusCode"] == 200
        items = json.loads(resp["body"])["items"]
        assert [it["key"] for it in items] == ["KEY1"]
        # date fields default to "" when CreatedDate is not a datetime
        assert items[0]["created_at"] == ""
        assert items[0]["updated_at"] == ""

    def test_list_handles_paginator_failure(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        mock_sm.get_paginator.side_effect = Exception("AWS down")
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/secrets"))
        # Errors are logged + empty list returned, so still 200
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["items"] == []

    def test_list_invalid_agent_id(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/bad id/secrets"))
        assert resp["statusCode"] == 400

    def test_list_agent_not_in_workspace(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": "other-ws"}}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/secrets"))
        assert resp["statusCode"] == 403

    def test_list_agent_not_found(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/secrets"))
        assert resp["statusCode"] == 403

    def test_list_editor_forbidden(self, workspace_id, mock_jwt, _mock_editor, mock_sm):
        # admin required
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/secrets"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# POST /api/workspaces/{wsId}/agents/{agentId}/secrets
# ---------------------------------------------------------------------------


class TestSetSecret:
    def test_set_creates_when_not_exists(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        mock_sm.put_secret_value.side_effect = mock_sm.exceptions.ResourceNotFoundException()
        body = {"key": "API_KEY", "value": "secret-val"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/agt1/secrets", body=body))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data == {"key": "API_KEY", "set": True}
        mock_sm.create_secret.assert_called_once()

    def test_set_updates_existing(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        mock_sm.put_secret_value.return_value = {}
        body = {"key": "API_KEY", "value": "v"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/agt1/secrets", body=body))
        assert resp["statusCode"] == 201
        mock_sm.put_secret_value.assert_called_once()
        mock_sm.create_secret.assert_not_called()

    def test_set_missing_key(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        body = {"value": "v"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/agt1/secrets", body=body))
        assert resp["statusCode"] == 400
        assert "key" in json.loads(resp["body"])["error"]

    def test_set_missing_value(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        body = {"key": "API_KEY"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/agt1/secrets", body=body))
        assert resp["statusCode"] == 400
        assert "value" in json.loads(resp["body"])["error"]

    def test_set_invalid_key_chars(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        body = {"key": "BAD KEY!", "value": "v"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/agt1/secrets", body=body))
        assert resp["statusCode"] == 400

    def test_set_invalid_agent_id(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        body = {"key": "K", "value": "v"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/bad agent/secrets", body=body))
        assert resp["statusCode"] == 400

    def test_set_agent_not_in_workspace(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": "other-ws"}}
        body = {"key": "K", "value": "v"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/agt1/secrets", body=body))
        assert resp["statusCode"] == 403

    def test_set_editor_forbidden(self, workspace_id, mock_jwt, _mock_editor, mock_sm):
        body = {"key": "K", "value": "v"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents/agt1/secrets", body=body))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# DELETE /api/workspaces/{wsId}/agents/{agentId}/secrets/{secretKey}
# ---------------------------------------------------------------------------


class TestDeleteSecret:
    def test_delete_success(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        mock_sm.delete_secret.return_value = {}
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/secrets/API_KEY"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data == {"key": "API_KEY", "deleted": True}
        mock_sm.delete_secret.assert_called_once()

    def test_delete_not_found(self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": workspace_id}}
        mock_sm.delete_secret.side_effect = mock_sm.exceptions.ResourceNotFoundException()
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/secrets/API_KEY"))
        assert resp["statusCode"] == 404

    def test_delete_invalid_agent_id(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/bad agent/secrets/K"))
        assert resp["statusCode"] == 400

    def test_delete_invalid_secret_key(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        # secret key contains invalid chars
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/secrets/bad key"))
        assert resp["statusCode"] == 400

    def test_delete_agent_not_in_workspace(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {"agentId": "agt1", "workspace_id": "other-ws"}}
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/secrets/API_KEY"))
        assert resp["statusCode"] == 403

    def test_delete_editor_forbidden(self, workspace_id, mock_jwt, _mock_editor, mock_sm):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/secrets/API_KEY"))
        assert resp["statusCode"] == 403

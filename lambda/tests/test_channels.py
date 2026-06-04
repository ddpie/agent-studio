"""Tests for crud.channels module."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "test-origin")
    monkeypatch.setenv("CHANNELS_TABLE", "test-channels")
    monkeypatch.setenv("CHANNEL_HISTORY_TABLE", "test-channel-history")


@pytest.fixture
def mock_channels_table():
    with patch("crud.channels._get_channels_table") as g:
        t = MagicMock()
        t.name = "test-channels"
        t.put_item.return_value = {}
        t.get_item.return_value = {"Item": None}
        t.delete_item.return_value = {}
        t.query.return_value = {"Items": []}
        t.update_item.return_value = {"Attributes": {}}
        g.return_value = t
        yield t


@pytest.fixture
def mock_history_table():
    with patch("crud.channels._get_history_table") as g:
        t = MagicMock()
        t.name = "test-channel-history"
        t.scan.return_value = {"Items": []}
        g.return_value = t
        yield t


@pytest.fixture
def mock_agents_table():
    with patch("crud.channels._get_agents_table") as g:
        t = MagicMock()
        t.name = "test-agents"
        g.return_value = t
        yield t


@pytest.fixture
def mock_secrets():
    with patch("crud.channels._get_secrets") as g:
        s = MagicMock()
        s.create_secret.return_value = {"ARN": "arn:aws:secretsmanager:us-east-1:123:secret:test"}
        s.put_secret_value.return_value = {}
        s.delete_secret.return_value = {}
        g.return_value = s
        yield s


# --- Role-based membership fixtures ---
# Must patch at shared.middleware where get_membership is called via its
# imported reference. The conftest fixtures patch shared.auth which is the
# definition site but NOT the call site in middleware.


@pytest.fixture
def _mock_editor(workspace_id, user_id):
    """Patch get_membership at the middleware call-site to return editor role."""
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "editor",
        "joined_at": datetime.utcnow().isoformat() + "Z",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_owner(workspace_id, user_id):
    """Patch get_membership at the middleware call-site to return owner role."""
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "owner",
        "joined_at": datetime.utcnow().isoformat() + "Z",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
    """Patch get_membership at the middleware call-site to return viewer role."""
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "viewer",
        "joined_at": datetime.utcnow().isoformat() + "Z",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    """Patch get_membership at the middleware call-site to return None (no access)."""
    with patch("shared.middleware.get_membership", return_value=None):
        yield


def _apigw(method, path, body=None, query_params=None):
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
        },
        "body": json.dumps(body) if body else None,
        "queryStringParameters": query_params or {},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler

    return lambda_handler(event, MagicMock())


def _valid_create_body(agent_id="agent123"):
    return {
        "channelType": "feishu",
        "channelName": "My Channel",
        "defaultAgentId": agent_id,
        "platformConfig": {"appId": "cli_abc123"},
        "appSecret": "secret123",
        "triggerMode": "mention",
        "maxHistoryTurns": 10,
        "language": "zh",
    }


# ---------------------------------------------------------------------------
# CREATE CHANNEL
# ---------------------------------------------------------------------------


class TestCreateChannel:
    def test_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """Successful channel creation stores secret and DDB record."""
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }

        body = _valid_create_body(agent_id)
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))

        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["channelType"] == "feishu"
        assert data["channelName"] == "My Channel"
        assert data["defaultAgentId"] == agent_id
        assert data["triggerMode"] == "mention"
        assert data["status"] == "provisioning"
        assert data["channelId"].startswith("ch_")

        # Secret was created
        mock_secrets.create_secret.assert_called_once()
        call_kwargs = mock_secrets.create_secret.call_args.kwargs
        assert "appSecret" in call_kwargs["SecretString"]

        # DDB record was written
        mock_channels_table.put_item.assert_called_once()

    def test_default_trigger_mode_at_bot_rejected(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """Known bug: when triggerMode is omitted, the default 'at_bot' is not
        in VALID_TRIGGER_MODES and validation rejects it."""
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }

        body = _valid_create_body(agent_id)
        del body["triggerMode"]  # Let it fall through to default "at_bot"

        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))

        # The default "at_bot" is not in VALID_TRIGGER_MODES, so validator rejects
        assert resp["statusCode"] == 400
        data = json.loads(resp["body"])
        assert "triggerMode" in data["error"]

    def test_missing_channel_type(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        body = _valid_create_body()
        del body["channelType"]
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "channelType" in json.loads(resp["body"])["error"]

    def test_invalid_channel_type(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        body = _valid_create_body()
        body["channelType"] = "telegram"
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "channelType" in json.loads(resp["body"])["error"]

    def test_missing_channel_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        body = _valid_create_body()
        del body["channelName"]
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "channelName" in json.loads(resp["body"])["error"]

    def test_channel_name_too_long(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        body = _valid_create_body()
        body["channelName"] = "A" * 65
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "channelName" in json.loads(resp["body"])["error"]

    def test_missing_default_agent_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        body = _valid_create_body()
        del body["defaultAgentId"]
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "defaultAgentId" in json.loads(resp["body"])["error"]

    def test_agent_not_in_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """Agent exists but belongs to a different workspace."""
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agent123", "workspace_id": "other-workspace"}
        }
        body = _valid_create_body()
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "does not exist" in json.loads(resp["body"])["error"]

    def test_missing_platform_config_app_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        body = _valid_create_body(agent_id)
        body["platformConfig"] = {}  # Missing appId
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "appId" in json.loads(resp["body"])["error"]

    def test_missing_app_secret(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        body = _valid_create_body(agent_id)
        del body["appSecret"]
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "appSecret" in json.loads(resp["body"])["error"]

    def test_invalid_max_history_turns(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        body = _valid_create_body(agent_id)
        body["maxHistoryTurns"] = 100  # Exceeds MAX_HISTORY_TURNS (50)
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "maxHistoryTurns" in json.loads(resp["body"])["error"]

    def test_invalid_language(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        body = _valid_create_body(agent_id)
        body["language"] = "fr"
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "language" in json.loads(resp["body"])["error"]

    def test_viewer_cannot_create(
        self, workspace_id, mock_jwt, _mock_viewer, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """Viewers don't have editor role, so creation is forbidden."""
        body = _valid_create_body()
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 403

    def test_secret_creation_failure_returns_500(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """If Secrets Manager fails, return 500 and don't write DDB."""
        from botocore.exceptions import ClientError

        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        mock_secrets.create_secret.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError", "Message": "boom"}},
            "CreateSecret",
        )

        body = _valid_create_body(agent_id)
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 500
        mock_channels_table.put_item.assert_not_called()

    def test_ddb_failure_cleans_up_secret(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """If DDB put_item fails, the secret should be cleaned up."""
        from botocore.exceptions import ClientError

        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        mock_channels_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "PutItem",
        )

        body = _valid_create_body(agent_id)
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 500
        # Secret should have been cleaned up
        mock_secrets.delete_secret.assert_called_once()

    def test_platform_config_not_dict(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        body = _valid_create_body(agent_id)
        body["platformConfig"] = "not-a-dict"
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "platformConfig" in json.loads(resp["body"])["error"]


# ---------------------------------------------------------------------------
# UPDATE CHANNEL
# ---------------------------------------------------------------------------


class TestUpdateChannel:
    def _existing_item(self, workspace_id, channel_id="ch_abc12345678901234"):
        return {
            "workspaceId": workspace_id,
            "sk": channel_id,
            "channelType": "feishu",
            "channelName": "Old Name",
            "defaultAgentId": "agent123",
            "platformConfig": {"appId": "cli_old"},
            "triggerMode": "mention",
            "maxHistoryTurns": 10,
            "language": "zh",
            "status": "active",
            "configVersion": 1,
            "createdAt": 1700000000,
            "updatedAt": 1700000000,
            "createdBy": "user1",
        }

    def test_partial_update_channel_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        updated = dict(existing)
        updated["channelName"] = "New Name"
        updated["configVersion"] = 2
        mock_channels_table.update_item.return_value = {"Attributes": updated}

        body = {"channelName": "New Name"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["channelName"] == "New Name"
        mock_channels_table.update_item.assert_called_once()

    def test_update_trigger_mode(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        updated = dict(existing)
        updated["triggerMode"] = "all"
        mock_channels_table.update_item.return_value = {"Attributes": updated}

        body = {"triggerMode": "all"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_invalid_trigger_mode(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"triggerMode": "at_bot"}  # Not in VALID_TRIGGER_MODES
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "triggerMode" in json.loads(resp["body"])["error"]

    def test_update_channel_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {"Item": None}

        body = {"channelName": "X"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 404

    def test_update_empty_body(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body={}))
        assert resp["statusCode"] == 400
        assert (
            "body" in json.loads(resp["body"])["error"].lower()
            or "no update" in json.loads(resp["body"])["error"].lower()
        )

    def test_update_no_updateable_fields(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """Body contains keys not in the updatable set (and no appSecret)."""
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"unknownField": "value"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "no updateable fields" in json.loads(resp["body"])["error"].lower()

    def test_update_app_secret_only(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """When only appSecret is changed, secret is updated and current record returned."""
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"appSecret": "new-secret-value"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200
        mock_secrets.put_secret_value.assert_called_once()
        # DDB update_item should NOT be called when only appSecret changes
        mock_channels_table.update_item.assert_not_called()

    def test_update_app_secret_recreates_on_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """If secret was previously deleted, update recreates it."""
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        mock_secrets.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "PutSecretValue",
        )

        body = {"appSecret": "new-secret-value"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200
        mock_secrets.create_secret.assert_called_once()

    def test_update_routing_mode(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        updated = dict(existing)
        updated["routingMode"] = "per-group"
        mock_channels_table.update_item.return_value = {"Attributes": updated}

        body = {"routingMode": "per-group"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_invalid_routing_mode(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"routingMode": "invalid"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "routingMode" in json.loads(resp["body"])["error"]

    def test_update_status(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        updated = dict(existing)
        updated["status"] = "paused"
        mock_channels_table.update_item.return_value = {"Attributes": updated}

        body = {"status": "paused"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_invalid_status(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"status": "deleted"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400

    def test_update_default_agent_validates_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """Changing defaultAgentId requires agent to exist in this workspace."""
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        # Agent belongs to a different workspace
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "newAgent1", "workspace_id": "other-ws"}
        }

        body = {"defaultAgentId": "newAgent1"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "does not exist" in json.loads(resp["body"])["error"]

    def test_viewer_cannot_update(
        self, workspace_id, mock_jwt, _mock_viewer, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        body = {"channelName": "New"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 403

    def test_update_max_history_turns_out_of_range(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"maxHistoryTurns": 51}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "maxHistoryTurns" in json.loads(resp["body"])["error"]

    def test_update_channel_name_empty(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"channelName": ""}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "channelName" in json.loads(resp["body"])["error"]


# ---------------------------------------------------------------------------
# DELETE CHANNEL
# ---------------------------------------------------------------------------


class TestDeleteChannel:
    def _existing_item(self, workspace_id, channel_id="ch_abc12345678901234"):
        return {
            "workspaceId": workspace_id,
            "sk": channel_id,
            "channelType": "feishu",
            "channelName": "Test",
            "status": "active",
        }

    def test_delete_success(self, workspace_id, mock_jwt, _mock_owner, mock_channels_table, mock_secrets):
        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {"Item": self._existing_item(workspace_id, ch_id)}

        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["deleted"] == ch_id
        mock_secrets.delete_secret.assert_called_once()
        mock_channels_table.delete_item.assert_called_once()

    def test_delete_not_found(self, workspace_id, mock_jwt, _mock_owner, mock_channels_table, mock_secrets):
        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {"Item": None}

        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 404

    def test_delete_secret_already_gone(
        self, workspace_id, mock_jwt, _mock_owner, mock_channels_table, mock_secrets
    ):
        """Delete succeeds even if secret was already deleted (idempotent)."""
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {"Item": self._existing_item(workspace_id, ch_id)}
        mock_secrets.delete_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "DeleteSecret",
        )

        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 200
        # DDB record still deleted
        mock_channels_table.delete_item.assert_called_once()

    def test_delete_secret_other_error_returns_500(
        self, workspace_id, mock_jwt, _mock_owner, mock_channels_table, mock_secrets
    ):
        """Non-404 secret errors are treated as fatal."""
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {"Item": self._existing_item(workspace_id, ch_id)}
        mock_secrets.delete_secret.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError", "Message": "boom"}},
            "DeleteSecret",
        )

        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 500
        # DDB record should NOT be deleted when secret deletion fails
        mock_channels_table.delete_item.assert_not_called()

    def test_editor_cannot_delete(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """Delete requires admin role."""
        ch_id = "ch_abc12345678901234"
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 403

    def test_viewer_cannot_delete(
        self, workspace_id, mock_jwt, _mock_viewer, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# LIST CHANNELS
# ---------------------------------------------------------------------------


class TestListChannels:
    def test_list_empty(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table):
        mock_channels_table.query.return_value = {"Items": []}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["channels"] == []

    def test_list_returns_channels(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table):
        items = [
            {
                "workspaceId": workspace_id,
                "sk": "ch_aaa11111111111111111",
                "channelType": "feishu",
                "channelName": "Channel A",
                "defaultAgentId": "agent1",
                "triggerMode": "mention",
                "status": "active",
                "createdAt": 1700000000,
                "updatedAt": 1700000000,
                "createdBy": "user1",
            },
            {
                "workspaceId": workspace_id,
                "sk": "ch_bbb22222222222222222",
                "channelType": "slack",
                "channelName": "Channel B",
                "defaultAgentId": "agent2",
                "triggerMode": "all",
                "status": "provisioning",
                "createdAt": 1700001000,
                "updatedAt": 1700001000,
                "createdBy": "user2",
            },
        ]
        mock_channels_table.query.return_value = {"Items": items}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["channels"]) == 2
        assert data["channels"][0]["channelId"] == "ch_aaa11111111111111111"
        assert data["channels"][1]["channelId"] == "ch_bbb22222222222222222"

    def test_list_filters_group_metadata(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table):
        """Records with '#group#' in sk should be filtered out."""
        items = [
            {
                "workspaceId": workspace_id,
                "sk": "ch_aaa11111111111111111",
                "channelType": "feishu",
                "channelName": "Real Channel",
                "status": "active",
                "createdAt": 1700000000,
                "updatedAt": 1700000000,
                "createdBy": "user1",
            },
            {
                "workspaceId": workspace_id,
                "sk": "ch_aaa11111111111111111#group#oc_123",
                "channelType": "feishu",
                "channelName": "Group Meta",
                "status": "active",
                "createdAt": 1700000000,
                "updatedAt": 1700000000,
                "createdBy": "user1",
            },
        ]
        mock_channels_table.query.return_value = {"Items": items}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["channels"]) == 1
        assert data["channels"][0]["channelName"] == "Real Channel"

    def test_list_workspace_scoped(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table):
        """Query uses workspace_id in KeyConditionExpression."""
        mock_channels_table.query.return_value = {"Items": []}
        _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels"))

        call_kwargs = mock_channels_table.query.call_args.kwargs
        assert ":wsId" in call_kwargs["ExpressionAttributeValues"]
        assert call_kwargs["ExpressionAttributeValues"][":wsId"] == workspace_id

    def test_list_viewer_allowed(self, workspace_id, mock_jwt, _mock_viewer, mock_channels_table):
        """Viewers can list channels (min_role=viewer for list)."""
        mock_channels_table.query.return_value = {"Items": []}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels"))
        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# GET CHANNEL (via list_channel_messages channel ownership check)
# Note: There is no dedicated GET single channel endpoint in the code,
# but list_channel_messages does a channel ownership check. We test that.
# ---------------------------------------------------------------------------


class TestGetChannelMessages:
    def test_messages_channel_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_history_table
    ):
        """Messages endpoint returns 404 if channel doesn't belong to workspace."""
        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {"Item": None}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels/{ch_id}/messages"))
        assert resp["statusCode"] == 404

    def test_messages_empty(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_history_table
    ):
        ch_id = "ch_abc12345678901234"
        # Channel exists
        mock_channels_table.get_item.return_value = {
            "Item": {"workspaceId": workspace_id, "sk": ch_id, "channelId": ch_id}
        }
        mock_history_table.scan.return_value = {"Items": []}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels/{ch_id}/messages"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["messages"] == []

    def test_messages_returns_sorted(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_history_table
    ):
        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {
            "Item": {"workspaceId": workspace_id, "sk": ch_id, "channelId": ch_id}
        }
        mock_history_table.scan.return_value = {
            "Items": [
                {
                    "pk": f"{ch_id}#user1",
                    "sk": "1700000001",
                    "role": "user",
                    "content": "Hello",
                    "userName": "Alice",
                    "timestamp": 1700000001,
                },
                {
                    "pk": f"{ch_id}#user1",
                    "sk": "1700000003",
                    "role": "assistant",
                    "content": "Hi!",
                    "userName": "",
                    "timestamp": 1700000003,
                },
                {
                    "pk": f"{ch_id}#user1",
                    "sk": "1700000002",
                    "role": "user",
                    "content": "How are you?",
                    "userName": "Alice",
                    "timestamp": 1700000002,
                },
            ]
        }

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels/{ch_id}/messages"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        messages = data["messages"]
        assert len(messages) == 3
        # Should be sorted descending by sk
        assert messages[0]["content"] == "Hi!"
        assert messages[1]["content"] == "How are you?"
        assert messages[2]["content"] == "Hello"

    def test_messages_viewer_allowed(
        self, workspace_id, mock_jwt, _mock_viewer, mock_channels_table, mock_history_table
    ):
        """Viewers can read messages."""
        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {
            "Item": {"workspaceId": workspace_id, "sk": ch_id, "channelId": ch_id}
        }
        mock_history_table.scan.return_value = {"Items": []}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels/{ch_id}/messages"))
        assert resp["statusCode"] == 200

    def test_messages_cross_workspace_rejected(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_history_table
    ):
        """If the channel doesn't belong to the workspace, reject."""
        ch_id = "ch_abc12345678901234"
        # get_item with this workspace returns no item (channel belongs to another ws)
        mock_channels_table.get_item.return_value = {"Item": None}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels/{ch_id}/messages"))
        assert resp["statusCode"] == 404


# ---------------------------------------------------------------------------
# TEST CHANNEL CONNECTION
# ---------------------------------------------------------------------------


class TestTestChannel:
    def test_placeholder_response(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table):
        ch_id = "ch_abc12345678901234"
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels/{ch_id}/test"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["status"] == "ok"

    def test_viewer_cannot_test(self, workspace_id, mock_jwt, _mock_viewer, mock_channels_table):
        ch_id = "ch_abc12345678901234"
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels/{ch_id}/test"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# CHANNEL RESPONSE SHAPING
# ---------------------------------------------------------------------------


class TestChannelResponse:
    def test_channel_response_strips_internal_fields(self):
        """_channel_response should not leak secretArn or other internal fields."""
        from crud.channels import _channel_response

        item = {
            "workspaceId": "ws1",
            "sk": "ch_test123",
            "channelType": "feishu",
            "channelName": "Test",
            "defaultAgentId": "agent1",
            "platformConfig": {"appId": "app1"},
            "triggerMode": "mention",
            "maxHistoryTurns": 10,
            "language": "zh",
            "status": "active",
            "configVersion": 1,
            "createdAt": 1700000000,
            "updatedAt": 1700000000,
            "createdBy": "user1",
            # Internal fields that should NOT appear in response
            "secretArn": "arn:aws:secretsmanager:us-east-1:123:secret:channels/ch_test123",
            "internalState": {"ecs_task": "arn:..."},
        }

        resp = _channel_response(item)
        assert "secretArn" not in resp
        assert "internalState" not in resp
        assert resp["channelId"] == "ch_test123"
        assert resp["channelType"] == "feishu"

    def test_channel_response_defaults(self):
        """Missing fields get sensible defaults."""
        from crud.channels import _channel_response

        item = {"sk": "ch_x", "workspaceId": "ws1"}
        resp = _channel_response(item)
        assert resp["channelId"] == "ch_x"
        assert resp["routingMode"] == "single"
        assert resp["routingRules"] == []
        assert resp["triggerMode"] == "mention"
        assert resp["maxHistoryTurns"] == 10
        assert resp["language"] == "zh"
        assert resp["status"] == "provisioning"
        assert resp["configVersion"] == 1
        assert resp["messageCount"] == 0
        assert resp["errorCount"] == 0


# ---------------------------------------------------------------------------
# SECRET PERSISTENCE BUG
# ---------------------------------------------------------------------------


class TestSecretPersistence:
    def test_secret_arn_not_stored_in_ddb(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """Known behavior: the secretArn returned by create_secret is NOT saved
        in the DDB item. The channel relies on convention-based naming
        (agent-studio/channels/{channelId}) to find the secret later."""
        agent_id = "agent123"
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": agent_id, "workspace_id": workspace_id}
        }
        mock_secrets.create_secret.return_value = {
            "ARN": "arn:aws:secretsmanager:us-east-1:123:secret:agent-studio/channels/ch_xyz-AbCdEf",
            "Name": "agent-studio/channels/ch_xyz",
        }

        body = _valid_create_body(agent_id)
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 201

        # Verify the DDB item does NOT contain secretArn
        put_call = mock_channels_table.put_item.call_args
        item_written = put_call.kwargs.get("Item") or put_call[1].get("Item")
        assert "secretArn" not in item_written

    def test_secret_name_derivation(self):
        """The secret name is derived from channel ID via _secret_name."""
        from crud.channels import _secret_name

        assert _secret_name("ch_abc123") == "agent-studio/channels/ch_abc123"


# ---------------------------------------------------------------------------
# AUTH EDGE CASES
# ---------------------------------------------------------------------------


class TestAuthEdgeCases:
    def test_no_membership_forbidden(self, workspace_id, mock_jwt, _mock_no_membership, mock_channels_table):
        """Non-members cannot access channels."""
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels"))
        assert resp["statusCode"] == 403

    def test_invalid_channel_id_format(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """Channel ID with invalid characters is rejected by validate_id."""
        bad_id = "ch_has spaces!"
        body = {"channelName": "X"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{bad_id}", body=body))
        assert resp["statusCode"] == 400
        assert "Invalid" in json.loads(resp["body"])["error"]


# ---------------------------------------------------------------------------
# LAZY INITIALIZER COVERAGE
# ---------------------------------------------------------------------------


class TestLazyGetters:
    """Cover the `if x is None: x = boto3...; return x` paths."""

    def _reset_globals(self):
        import crud.channels as ch

        ch._channels_table = None
        ch._history_table = None
        ch._agents_table = None
        ch._secrets = None

    def test_get_channels_table(self, monkeypatch):
        self._reset_globals()
        from crud import channels as ch

        sentinel = object()

        class FakeRes:
            def Table(self, _name):
                return sentinel

        monkeypatch.setattr(ch.boto3, "resource", lambda *a, **kw: FakeRes())
        result = ch._get_channels_table()
        assert result is sentinel
        # Cached on second call
        assert ch._get_channels_table() is sentinel

    def test_get_history_table(self, monkeypatch):
        self._reset_globals()
        from crud import channels as ch

        sentinel = object()

        class FakeRes:
            def Table(self, _name):
                return sentinel

        monkeypatch.setattr(ch.boto3, "resource", lambda *a, **kw: FakeRes())
        assert ch._get_history_table() is sentinel

    def test_get_agents_table(self, monkeypatch):
        self._reset_globals()
        from crud import channels as ch

        sentinel = object()

        class FakeRes:
            def Table(self, _name):
                return sentinel

        monkeypatch.setattr(ch.boto3, "resource", lambda *a, **kw: FakeRes())
        assert ch._get_agents_table() is sentinel

    def test_get_secrets(self, monkeypatch):
        self._reset_globals()
        from crud import channels as ch

        sentinel = object()
        monkeypatch.setattr(ch.boto3, "client", lambda *a, **kw: sentinel)
        assert ch._get_secrets() is sentinel


# ---------------------------------------------------------------------------
# ERROR PATH COVERAGE
# ---------------------------------------------------------------------------


class TestCreateChannelErrorPaths:
    def test_validate_agent_clienterror_returns_false(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """When the agents-table get_item raises ClientError, treat as not-in-workspace."""
        from botocore.exceptions import ClientError

        mock_agents_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "GetItem",
        )
        body = _valid_create_body("agent123")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert "does not exist" in json.loads(resp["body"])["error"]

    def test_invalid_default_agent_id_format(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        body = _valid_create_body("bad agent id!")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 400
        assert (
            "Invalid" in json.loads(resp["body"])["error"]
            or "defaultAgentId" in json.loads(resp["body"])["error"]
        )

    def test_ddb_failure_secret_cleanup_swallows_clienterror(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        """When DDB fails and the secret-cleanup *also* fails, still 500."""
        from botocore.exceptions import ClientError

        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agent123", "workspace_id": workspace_id}
        }
        mock_channels_table.put_item.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "PutItem",
        )
        mock_secrets.delete_secret.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "cleanup boom"}},
            "DeleteSecret",
        )
        body = _valid_create_body("agent123")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/channels", body=body))
        assert resp["statusCode"] == 500


class TestListChannelsClientError:
    def test_list_channels_clienterror(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table):
        from botocore.exceptions import ClientError

        mock_channels_table.query.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "Query",
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/channels"))
        assert resp["statusCode"] == 500


class TestUpdateChannelMoreCoverage:
    def _existing_item(self, workspace_id, channel_id="ch_abc12345678901234"):
        return {
            "workspaceId": workspace_id,
            "sk": channel_id,
            "channelType": "feishu",
            "channelName": "Old Name",
            "defaultAgentId": "agent123",
            "platformConfig": {"appId": "cli_old"},
            "triggerMode": "mention",
            "maxHistoryTurns": 10,
            "language": "zh",
            "status": "active",
            "configVersion": 1,
            "createdAt": 1700000000,
            "updatedAt": 1700000000,
            "createdBy": "user1",
        }

    def test_update_get_item_clienterror(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        from botocore.exceptions import ClientError

        mock_channels_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "x"}},
            "GetItem",
        )
        ch_id = "ch_abc12345678901234"
        body = {"channelName": "X"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 500

    def test_update_default_agent_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"defaultAgentId": "bad agent!"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400

    def test_update_default_agent_empty(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"defaultAgentId": ""}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "defaultAgentId" in json.loads(resp["body"])["error"]

    def test_update_default_agent_succeeds(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_agents_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "newagent", "workspace_id": workspace_id}
        }
        mock_channels_table.update_item.return_value = {"Attributes": existing}

        body = {"defaultAgentId": "newagent"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_platform_config_not_dict(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"platformConfig": "not-a-dict"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "platformConfig" in json.loads(resp["body"])["error"]

    def test_update_platform_config_succeeds(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_channels_table.update_item.return_value = {"Attributes": existing}

        body = {"platformConfig": {"appId": "newapp"}}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_routing_rules_not_list(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"routingRules": "not-a-list"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "routingRules" in json.loads(resp["body"])["error"]

    def test_update_routing_rules_succeeds(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_channels_table.update_item.return_value = {"Attributes": existing}

        body = {"routingRules": [{"if": "x", "then": "y"}]}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_language_succeeds(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_channels_table.update_item.return_value = {"Attributes": existing}

        body = {"language": "en"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_language_invalid(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"language": "fr"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400

    def test_update_app_secret_empty(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"appSecret": ""}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400
        assert "appSecret" in json.loads(resp["body"])["error"]

    def test_update_app_secret_recreate_also_fails(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """Secret was deleted; recreate also fails → 500."""
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_secrets.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "PutSecretValue",
        )
        mock_secrets.create_secret.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "CreateSecret",
        )
        body = {"appSecret": "new"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 500

    def test_update_app_secret_other_error(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """Non-NotFound ClientError on put_secret_value → 500."""
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_secrets.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "PutSecretValue",
        )
        body = {"appSecret": "new"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 500

    def test_update_channel_name_too_long(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}

        body = {"channelName": "A" * 65}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 400

    def test_update_max_history_turns_succeeds(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_channels_table.update_item.return_value = {"Attributes": existing}

        body = {"maxHistoryTurns": 25}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 200

    def test_update_ddb_clienterror(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        existing = self._existing_item(workspace_id, ch_id)
        mock_channels_table.get_item.return_value = {"Item": existing}
        mock_channels_table.update_item.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}},
            "UpdateItem",
        )
        body = {"channelName": "New"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}", body=body))
        assert resp["statusCode"] == 500

    def test_update_invalid_channel_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        body = {"channelName": "x"}
        resp = _invoke(
            _apigw(
                "PUT",
                f"/api/workspaces/{workspace_id}/channels/bad id with spaces",
                body=body,
            )
        )
        assert resp["statusCode"] == 400

    def test_update_empty_request_body(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_secrets
    ):
        """Body is None / no JSON."""
        ch_id = "ch_abc12345678901234"
        # request body field set to None
        event = _apigw("PUT", f"/api/workspaces/{workspace_id}/channels/{ch_id}")
        # Build body=None manually because helper rejects body=None as no body
        resp = _invoke(event)
        assert resp["statusCode"] == 400


class TestDeleteChannelMoreCoverage:
    def test_delete_invalid_id(self, workspace_id, mock_jwt, _mock_owner, mock_channels_table, mock_secrets):
        resp = _invoke(
            _apigw(
                "DELETE",
                f"/api/workspaces/{workspace_id}/channels/has space!",
            )
        )
        assert resp["statusCode"] == 400

    def test_delete_get_item_clienterror(
        self, workspace_id, mock_jwt, _mock_owner, mock_channels_table, mock_secrets
    ):
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "x"}},
            "GetItem",
        )
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 500

    def test_delete_ddb_delete_clienterror(
        self, workspace_id, mock_jwt, _mock_owner, mock_channels_table, mock_secrets
    ):
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {"Item": {"workspaceId": workspace_id, "sk": ch_id}}
        mock_channels_table.delete_item.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "x"}},
            "DeleteItem",
        )
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/channels/{ch_id}"))
        assert resp["statusCode"] == 500


class TestTestChannelMoreCoverage:
    def test_invalid_channel_id(self, workspace_id, mock_jwt, _mock_editor, mock_channels_table):
        resp = _invoke(
            _apigw(
                "POST",
                f"/api/workspaces/{workspace_id}/channels/bad id!/test",
            )
        )
        assert resp["statusCode"] == 400


class TestListChannelMessagesMoreCoverage:
    def test_invalid_channel_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_history_table
    ):
        resp = _invoke(
            _apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/channels/bad id!/messages",
            )
        )
        assert resp["statusCode"] == 400

    def test_get_item_clienterror(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_history_table
    ):
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "x"}},
            "GetItem",
        )
        resp = _invoke(
            _apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/channels/{ch_id}/messages",
            )
        )
        assert resp["statusCode"] == 500

    def test_history_scan_clienterror(
        self, workspace_id, mock_jwt, _mock_editor, mock_channels_table, mock_history_table
    ):
        from botocore.exceptions import ClientError

        ch_id = "ch_abc12345678901234"
        mock_channels_table.get_item.return_value = {
            "Item": {"workspaceId": workspace_id, "sk": ch_id, "channelId": ch_id}
        }
        mock_history_table.scan.side_effect = ClientError(
            {"Error": {"Code": "InternalError", "Message": "x"}},
            "Scan",
        )
        resp = _invoke(
            _apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/channels/{ch_id}/messages",
            )
        )
        assert resp["statusCode"] == 500

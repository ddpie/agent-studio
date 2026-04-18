"""Tests for crud/runtime.py — AgentCore Control Plane passthrough."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def aws_event_factory(user_id, workspace_id):
    def _build(path, method="GET", path_params=None, body=None):
        return {
            "httpMethod": method,
            "path": path,
            "resource": path.replace(
                workspace_id, "{wsId}"
            ).replace("agt-test", "{agentId}"),
            "pathParameters": path_params or {"wsId": workspace_id, "agentId": "agt-test"},
            "headers": {"Authorization": "Bearer test-token"},
            "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
            "body": json.dumps(body) if body else None,
            "isBase64Encoded": False,
            "queryStringParameters": None,
        }
    return _build


def test_get_runtime_returns_filtered_fields(mock_jwt, user_id, workspace_id, aws_event_factory):
    """GET /agents/{id}/runtime strips sensitive fields."""
    from crud.handler import app
    with patch("crud.runtime._get_control") as mock_control_factory, \
         patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {
            "agentId": "agt-test",
            "workspace_id": workspace_id,
            "created_by": user_id,
            "status": "active",
        }
        control = MagicMock()
        control.get_agent_runtime.return_value = {
            "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/agt-test",
            "agentRuntimeId": "agt-test",
            "agentRuntimeName": "TestAgent",
            "status": "ACTIVE",
            "lastUpdatedAt": "2026-04-18T00:00:00Z",
            "description": "desc",
            "executionRoleArn": "arn:aws:iam::123:role/secret",
            "agentRuntimeArtifact": {"code": {"s3": {"bucket": "b", "prefix": "p"}}},
            "agentRuntimeVersion": "4",
        }
        mock_control_factory.return_value = control

        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["status"] == "ACTIVE"
    assert data["agentRuntimeVersion"] == "4"
    assert "agentRuntimeArn" not in data
    assert "executionRoleArn" not in data
    assert "agentRuntimeArtifact" not in data


def test_get_runtime_returns_404_when_runtime_missing(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    from botocore.exceptions import ClientError
    with patch("crud.runtime._get_control") as mock_control_factory, \
         patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {
            "agentId": "agt-test",
            "workspace_id": workspace_id,
            "status": "active",
        }
        control = MagicMock()
        control.get_agent_runtime.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "GetAgentRuntime",
        )
        mock_control_factory.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 404


def test_get_runtime_denies_cross_workspace(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {
            "agentId": "agt-test",
            "workspace_id": "OTHER_WS",
            "status": "active",
        }
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 403


def test_list_versions_returns_sorted(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_control") as mock_control_factory, \
         patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_versions.return_value = {
            "agentRuntimes": [
                {"agentRuntimeVersion": "3", "status": "ACTIVE", "lastUpdatedAt": "2026-04-18T00:00:00Z"},
                {"agentRuntimeVersion": "2", "status": "ACTIVE", "lastUpdatedAt": "2026-04-17T00:00:00Z"},
                {"agentRuntimeVersion": "1", "status": "ACTIVE", "lastUpdatedAt": "2026-04-16T00:00:00Z"},
            ]
        }
        mock_control_factory.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/versions")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    versions = data.get("versions") if isinstance(data, dict) else data
    assert [v["agentRuntimeVersion"] for v in versions] == ["3", "2", "1"]
    assert "executionRoleArn" not in versions[0]


def test_list_endpoints(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_endpoints.return_value = {
            "runtimeEndpoints": [
                {"name": "DEFAULT", "liveVersion": "4", "status": "READY",
                 "createdAt": "2026-04-01", "lastUpdatedAt": "2026-04-18"},
                {"name": "staging", "liveVersion": "3", "targetVersion": None, "status": "READY",
                 "createdAt": "2026-04-10", "lastUpdatedAt": "2026-04-10"},
            ]
        }
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    ep = data.get("endpoints", data)
    names = [e["name"] for e in ep]
    assert "DEFAULT" in names and "staging" in names


def test_create_endpoint_requires_editor(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "3"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_update_endpoint_not_found_version(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    from botocore.exceptions import ClientError
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.update_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Agent version 99 does not exist"}},
            "UpdateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={"version": "99"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404

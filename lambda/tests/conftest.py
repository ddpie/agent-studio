"""Shared pytest fixtures for CRUD Lambda tests."""
import json
import os
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

os.environ["AWS_REGION"] = "us-east-1"
os.environ["COGNITO_USER_POOL_ID"] = "us-east-1_TestPool"
os.environ["COGNITO_CLIENT_ID"] = "test-client-id"
os.environ["WORKSPACES_TABLE"] = "test-workspaces"
os.environ["AGENTS_TABLE"] = "test-agents"
os.environ["SKILLS_TABLE"] = "test-skills"
os.environ["TOOLS_TABLE"] = "test-tools"
os.environ["S3_BUCKET"] = "test-assets"
os.environ["POWERTOOLS_SERVICE_NAME"] = "crud-test"
os.environ["POWERTOOLS_TRACE_DISABLED"] = "1"


@pytest.fixture
def user_id():
    return str(uuid.uuid4())


@pytest.fixture
def workspace_id():
    return str(uuid.uuid4())


@pytest.fixture
def mock_jwt(user_id):
    with patch("shared.auth.verify_jwt") as mock:
        mock.return_value = {
            "sub": user_id,
            "email": "test@example.com",
            "token_use": "id",
        }
        yield mock


@pytest.fixture
def mock_membership_owner(workspace_id, user_id):
    with patch("shared.auth.get_membership") as mock:
        mock.return_value = {
            "workspaceId": workspace_id,
            "sk": f"MEMBER#{user_id}",
            "userId": user_id,
            "role": "owner",
            "joined_at": datetime.utcnow().isoformat() + "Z",
        }
        yield mock


@pytest.fixture
def mock_membership_editor(workspace_id, user_id):
    with patch("shared.auth.get_membership") as mock:
        mock.return_value = {
            "workspaceId": workspace_id,
            "sk": f"MEMBER#{user_id}",
            "userId": user_id,
            "role": "editor",
            "joined_at": datetime.utcnow().isoformat() + "Z",
        }
        yield mock


@pytest.fixture
def mock_membership_viewer(workspace_id, user_id):
    with patch("shared.auth.get_membership") as mock:
        mock.return_value = {
            "workspaceId": workspace_id,
            "sk": f"MEMBER#{user_id}",
            "userId": user_id,
            "role": "viewer",
            "joined_at": datetime.utcnow().isoformat() + "Z",
        }
        yield mock


@pytest.fixture
def mock_membership_none():
    with patch("shared.auth.get_membership") as mock:
        mock.return_value = None
        yield mock


def make_apigw_event(
    method: str,
    path: str,
    body: dict | None = None,
    path_params: dict | None = None,
    query_params: dict | None = None,
    headers: dict | None = None,
) -> dict:
    default_headers = {
        "Authorization": "Bearer test-token",
        "Content-Type": "application/json",
    }
    if headers:
        default_headers.update(headers)

    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
        "headers": default_headers,
        "body": json.dumps(body) if body else None,
        "requestContext": {
            "stage": "test",
            "requestId": str(uuid.uuid4()),
            "identity": {"sourceIp": "127.0.0.1"},
        },
        "isBase64Encoded": False,
    }

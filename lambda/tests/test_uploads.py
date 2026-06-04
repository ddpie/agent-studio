"""Tests for crud.uploads — presigned URLs, public listings, and clones."""
import base64
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_s3():
    with patch("crud.uploads._get_s3") as g:
        s = MagicMock()
        s.generate_presigned_post.return_value = {
            "url": "https://s3.example.com/bucket",
            "fields": {"key": "uploads/images/abc.png", "Content-Type": "image/png"},
        }
        s.generate_presigned_url.return_value = "https://s3.example.com/dl"
        # paginator for clone copies
        paginator = MagicMock()
        paginator.paginate.return_value = []
        s.get_paginator.return_value = paginator
        # NoSuchKey exception class on the client
        s.exceptions = MagicMock()
        s.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        g.return_value = s
        yield s


@pytest.fixture
def mock_agents_table():
    with patch("crud.uploads._get_agents_table") as g:
        t = MagicMock()
        t.name = "test-agents"
        t.get_item.return_value = {"Item": None}
        # query has explicit no LastEvaluatedKey — public listing loops `while last_key`
        t.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.put_item.return_value = {}
        g.return_value = t
        yield t


@pytest.fixture
def mock_skills_table():
    with patch("crud.uploads._get_skills_table") as g:
        t = MagicMock()
        t.name = "test-skills"
        t.get_item.return_value = {"Item": None}
        t.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.put_item.return_value = {}
        g.return_value = t
        yield t


@pytest.fixture
def mock_tools_table():
    with patch("crud.uploads._get_tools_table") as g:
        t = MagicMock()
        t.name = "test-tools"
        t.get_item.return_value = {"Item": None}
        t.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.put_item.return_value = {}
        g.return_value = t
        yield t


@pytest.fixture
def _mock_editor(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "editor",
    }
    # Patch BOTH the middleware call-site AND the uploads module's bound
    # reference (clone endpoints import get_membership at module load).
    with patch("shared.middleware.get_membership", return_value=member), \
         patch("crud.uploads.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "viewer",
    }
    with patch("shared.middleware.get_membership", return_value=member), \
         patch("crud.uploads.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    with patch("shared.middleware.get_membership", return_value=None), \
         patch("crud.uploads.get_membership", return_value=None):
        yield


def _apigw(method, path, body=None, query_params=None, headers=None):
    h = {"Authorization": "Bearer tok", "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "headers": h,
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query_params or {},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


# ---------------------------------------------------------------------------
# POST /api/workspaces/{ws}/uploads/images
# ---------------------------------------------------------------------------


class TestUploadImage:
    def test_success(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"content_type": "image/png", "filename": "photo.png"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/images", body=body))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["uploadUrl"] == "https://s3.example.com/bucket"
        assert data["s3Key"].startswith("uploads/images/")
        assert data["s3Key"].endswith(".png")
        assert data["expiresIn"] == 900
        mock_s3.generate_presigned_post.assert_called_once()

    def test_missing_content_type(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"filename": "photo.png"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/images", body=body))
        assert resp["statusCode"] == 400
        assert "content_type" in json.loads(resp["body"])["error"]

    def test_invalid_content_type(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"content_type": "video/mp4", "filename": "x.mp4"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/images", body=body))
        assert resp["statusCode"] == 400
        assert "content_type" in json.loads(resp["body"])["error"]

    def test_missing_filename(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"content_type": "image/png"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/images", body=body))
        assert resp["statusCode"] == 400
        assert "filename" in json.loads(resp["body"])["error"]

    def test_filename_with_traversal(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"content_type": "image/png", "filename": "../etc/passwd"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/images", body=body))
        assert resp["statusCode"] == 400
        assert "filename" in json.loads(resp["body"])["error"].lower()

    def test_filename_with_slash(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"content_type": "image/png", "filename": "a/b.png"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/images", body=body))
        assert resp["statusCode"] == 400

    def test_no_membership_forbidden(self, workspace_id, mock_jwt, _mock_no_membership, mock_s3):
        body = {"content_type": "image/png", "filename": "x.png"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/images", body=body))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# POST /api/workspaces/{ws}/uploads/attachments
# ---------------------------------------------------------------------------


class TestUploadAttachment:
    def test_success(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {
            "content_type": "application/pdf",
            "filename": "doc.pdf",
            "sessionId": "sess_abc-123",
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/attachments", body=body))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert "uploads/attachments/sess_abc-123/doc.pdf" == data["s3Key"]

    def test_invalid_content_type(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"content_type": "image/png", "filename": "x.png", "sessionId": "s1"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/attachments", body=body))
        assert resp["statusCode"] == 400

    def test_missing_session_id(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"content_type": "application/pdf", "filename": "x.pdf"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/attachments", body=body))
        assert resp["statusCode"] == 400
        assert "sessionId" in json.loads(resp["body"])["error"]

    def test_invalid_session_id(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {
            "content_type": "application/pdf",
            "filename": "x.pdf",
            "sessionId": "bad session!",
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/attachments", body=body))
        assert resp["statusCode"] == 400
        assert "sessionId" in json.loads(resp["body"])["error"]

    def test_filename_traversal(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {
            "content_type": "application/pdf",
            "filename": "../boom.pdf",
            "sessionId": "s1",
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/uploads/attachments", body=body))
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# GET /api/workspaces/{ws}/downloads
# ---------------------------------------------------------------------------


class TestGetDownloadUrl:
    def test_outputs_prefix_works(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "outputs/agt-1/result.txt"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["url"] == "https://s3.example.com/dl"
        assert data["expiresIn"] == 300

    def test_invalid_prefix(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "private/secret.txt"},
        ))
        assert resp["statusCode"] == 400
        assert "prefix" in json.loads(resp["body"])["error"].lower()

    def test_invalid_path(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "outputs/../etc"},
        ))
        assert resp["statusCode"] == 400

    def test_agent_path_in_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_s3, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": workspace_id, "status": "active"}
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "agents/agt-1/deployment.zip"},
        ))
        assert resp["statusCode"] == 200

    def test_agent_in_other_workspace_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_s3, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-x", "workspace_id": "other-ws", "status": "active"}
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "agents/agt-x/deployment.zip"},
        ))
        assert resp["statusCode"] == 403

    def test_agent_archived_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_s3, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": workspace_id, "status": "archived"}
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "agents/agt-1/deployment.zip"},
        ))
        assert resp["statusCode"] == 403

    def test_agent_not_found_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_s3, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "agents/agt-1/deployment.zip"},
        ))
        assert resp["statusCode"] == 403

    def test_skill_in_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_s3, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": {"skillId": "sk1", "workspace_id": workspace_id, "deleted": False}
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "skills/sk1/SKILL.md"},
        ))
        assert resp["statusCode"] == 200

    def test_skill_other_workspace_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_s3, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": {"skillId": "sk1", "workspace_id": "other"}
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "skills/sk1/SKILL.md"},
        ))
        assert resp["statusCode"] == 403

    def test_skill_deleted_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_s3, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": {"skillId": "sk1", "workspace_id": workspace_id, "deleted": True}
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "skills/sk1/SKILL.md"},
        ))
        assert resp["statusCode"] == 403

    def test_uploads_prefix_no_check(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/downloads",
            query_params={"key": "uploads/images/x.png"},
        ))
        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# /api/workspaces/{ws}/storage   (GET, PUT, DELETE)
# ---------------------------------------------------------------------------


class TestStorage:
    def test_get_existing(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body_obj = MagicMock()
        body_obj.read.return_value = b'{"hello":"world"}'
        mock_s3.get_object.return_value = {"Body": body_obj}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/storage",
            query_params={"key": "tool-history/abc.json"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["data"] == {"hello": "world"}

    def test_get_missing_returns_null(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        # Simulate NoSuchKey
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey()
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/storage",
            query_params={"key": "tool-history/missing.json"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["data"] is None

    def test_get_invalid_prefix(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/storage",
            query_params={"key": "outputs/x.json"},
        ))
        assert resp["statusCode"] == 400
        assert "prefix" in json.loads(resp["body"])["error"].lower()

    def test_get_staging_injects_user_id(self, workspace_id, user_id, mock_jwt, _mock_editor, mock_s3):
        body_obj = MagicMock()
        body_obj.read.return_value = b'null'
        mock_s3.get_object.return_value = {"Body": body_obj}
        _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/storage",
            query_params={"key": "staging/foo.json"},
        ))
        # Verify the s3 key includes the user_id
        called_key = mock_s3.get_object.call_args.kwargs["Key"]
        assert user_id in called_key

    def test_put_success(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"key": "tool-history/abc.json", "data": {"k": "v"}}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/storage",
            body=body,
        ))
        assert resp["statusCode"] == 200
        mock_s3.put_object.assert_called_once()

    def test_put_too_large(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        big = "x" * 2_000_000
        body = {"key": "tool-history/big.json", "data": big}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/storage",
            body=body,
        ))
        assert resp["statusCode"] == 400
        assert "large" in json.loads(resp["body"])["error"].lower()

    def test_put_invalid_prefix(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        body = {"key": "outputs/x.json", "data": 1}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/storage",
            body=body,
        ))
        assert resp["statusCode"] == 400

    def test_put_viewer_forbidden(self, workspace_id, mock_jwt, _mock_viewer, mock_s3):
        body = {"key": "tool-history/x.json", "data": 1}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/storage",
            body=body,
        ))
        assert resp["statusCode"] == 403

    def test_delete_success(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/storage",
            query_params={"key": "tool-history/x.json"},
        ))
        assert resp["statusCode"] == 200
        mock_s3.delete_object.assert_called_once()

    def test_delete_invalid_prefix(self, workspace_id, mock_jwt, _mock_editor, mock_s3):
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/storage",
            query_params={"key": "outputs/x.json"},
        ))
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# Public listings  (GET /api/public/agents, /api/public/skills, /api/public/tools)
# ---------------------------------------------------------------------------


class TestPublicAgents:
    def test_unauthorized_no_bearer(self, mock_agents_table):
        # No mock_jwt fixture used; verify_jwt patched at module-level when fixture not active.
        # Use empty Authorization to trigger forbidden().
        event = _apigw("GET", "/api/public/agents", headers={"Authorization": ""})
        resp = _invoke(event)
        assert resp["statusCode"] == 403

    def test_returns_items(self, mock_jwt, mock_agents_table):
        mock_agents_table.query.return_value = {
            "Items": [
                {
                    "agentId": "a1",
                    "name": "Agent1",
                    "description": "d",
                    "model_id": "m1",
                    "supports_images": True,
                    "welcome_message": "hi",
                    "created_at": "2026-01-01",
                },
            ],
            "LastEvaluatedKey": None,
        }
        resp = _invoke(_apigw("GET", "/api/public/agents"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 1
        assert data["items"][0]["agentId"] == "a1"
        # Verify GSI used
        call_kwargs = mock_agents_table.query.call_args.kwargs
        assert call_kwargs["IndexName"] == "public-index"

    def test_invalid_cursor(self, mock_jwt, mock_agents_table):
        resp = _invoke(_apigw(
            "GET",
            "/api/public/agents",
            query_params={"cursor": "not-base64!!!"},
        ))
        assert resp["statusCode"] == 400


class TestPublicSkills:
    def test_returns_items(self, mock_jwt, mock_skills_table):
        mock_skills_table.scan.return_value = {
            "Items": [
                {
                    "skillId": "sk1",
                    "name": "Skill1",
                    "description": "d",
                    "type": "prompt",
                    "tags": ["a", "b"],
                    "created_at": "2026-01-01",
                },
            ],
            "LastEvaluatedKey": None,
        }
        resp = _invoke(_apigw("GET", "/api/public/skills"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 1
        assert data["items"][0]["skillId"] == "sk1"

    def test_invalid_cursor(self, mock_jwt, mock_skills_table):
        resp = _invoke(_apigw(
            "GET",
            "/api/public/skills",
            query_params={"cursor": "###bad###"},
        ))
        assert resp["statusCode"] == 400

    def test_unauthorized(self, mock_skills_table):
        event = _apigw("GET", "/api/public/skills", headers={"Authorization": ""})
        resp = _invoke(event)
        assert resp["statusCode"] == 403


class TestPublicTools:
    def test_returns_items(self, mock_jwt, mock_tools_table):
        mock_tools_table.scan.return_value = {
            "Items": [
                {
                    "toolId": "t1",
                    "name": "tool1",
                    "description": "d",
                    "category": "general",
                    "created_at": "2026-01-01",
                    # source code should NOT be returned
                    "code": "def secret():\n    return 'super-private'",
                },
            ],
            "LastEvaluatedKey": None,
        }
        resp = _invoke(_apigw("GET", "/api/public/tools"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 1
        assert data["items"][0]["toolId"] == "t1"
        # source code is stripped from public listing
        assert "code" not in data["items"][0]

    def test_invalid_cursor(self, mock_jwt, mock_tools_table):
        resp = _invoke(_apigw(
            "GET",
            "/api/public/tools",
            query_params={"cursor": "%%%"},
        ))
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# Public clones
# ---------------------------------------------------------------------------


class TestCloneAgent:
    def test_clones_when_public(
        self, workspace_id, user_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-pub",
                "name": "Pub Agent",
                "visibility": "public",
                "status": "active",
                "model_id": "claude-sonnet-4",
                "skills": [{"id": "sk1", "name": "skill1"}],
            }
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/agt-pub/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["workspace_id"] == workspace_id
        # New agent id is alphanumeric (after "".join(c for c in name if c.isalnum()))
        mock_agents_table.put_item.assert_called_once()

    def test_not_public_returns_404(
        self, workspace_id, user_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "x", "visibility": "private"}
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/x/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 404

    def test_archived_returns_404(
        self, workspace_id, user_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "x", "visibility": "public", "status": "archived"}
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/x/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 404

    def test_not_found_returns_404(
        self, workspace_id, user_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/x/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 404

    def test_invalid_id(
        self, workspace_id, user_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        # Path with invalid id triggers validate_id
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/has spaces!/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        # API Gateway resolver may 404 or 400 depending on routing. Either is fine
        # because the endpoint should not succeed.
        assert resp["statusCode"] in (400, 404)

    def test_no_workspace_header(
        self, mock_jwt, _mock_editor, mock_agents_table, mock_s3
    ):
        resp = _invoke(_apigw("POST", "/api/public/agents/agt-pub/clone"))
        assert resp["statusCode"] == 400
        assert "x-workspace-id" in json.loads(resp["body"])["error"]

    def test_no_membership_in_target_workspace(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_agents_table, mock_s3
    ):
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/agt-pub/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 403

    def test_unauthorized(self, workspace_id, mock_agents_table, mock_s3):
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/agt-pub/clone",
            headers={"Authorization": "", "x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 403

    def test_override_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "a", "name": "src", "visibility": "public"}
        }
        body = {"name": "MyOverride!@#"}
        resp = _invoke(_apigw(
            "POST",
            "/api/public/agents/a/clone",
            body=body,
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        # non-alphanumeric stripped
        assert data["name"] == "MyOverride"


class TestCloneSkill:
    def test_clones_public(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_s3
    ):
        mock_skills_table.get_item.return_value = {
            "Item": {
                "skillId": "sk1",
                "name": "skill1",
                "visibility": "public",
                "type": "prompt",
                "deleted": False,
            }
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/skills/sk1/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 201
        mock_skills_table.put_item.assert_called_once()

    def test_script_skill_needs_approval(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_s3
    ):
        """Script skills come back with approved=False on clone."""
        mock_skills_table.get_item.return_value = {
            "Item": {
                "skillId": "sk1",
                "name": "skill1",
                "visibility": "public",
                "type": "script",
                "deleted": False,
            }
        }
        _invoke(_apigw(
            "POST",
            "/api/public/skills/sk1/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        item = mock_skills_table.put_item.call_args.kwargs["Item"]
        assert item["approved"] is False
        assert item["type"] == "script"

    def test_private_skill_returns_404(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_s3
    ):
        mock_skills_table.get_item.return_value = {
            "Item": {"skillId": "sk1", "visibility": "private"}
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/skills/sk1/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 404

    def test_deleted_skill_returns_404(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_s3
    ):
        mock_skills_table.get_item.return_value = {
            "Item": {"skillId": "sk1", "visibility": "public", "deleted": True}
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/skills/sk1/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 404


class TestCloneTool:
    def test_clones_public(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {
            "Item": {
                "toolId": "t1",
                "name": "tool1",
                "visibility": "public",
                "code": "def x(): pass",
                "deleted": False,
            }
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/tools/t1/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 201
        item = mock_tools_table.put_item.call_args.kwargs["Item"]
        # source code IS exposed post-clone (you own it now)
        assert item["code"] == "def x(): pass"
        assert item["visibility"] == "private"
        assert item["builtin"] is False

    def test_private_tool_returns_404(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {
            "Item": {"toolId": "t1", "visibility": "private"}
        }
        resp = _invoke(_apigw(
            "POST",
            "/api/public/tools/t1/clone",
            headers={"x-workspace-id": workspace_id},
        ))
        assert resp["statusCode"] == 404

"""Tests for crud.skills module."""
import base64
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


def _make_ccf_exception_class():
    """Build a real exception class to plant on table.meta.client.exceptions."""
    return type("ConditionalCheckFailedException", (Exception,), {})


@pytest.fixture
def mock_skills_table():
    with patch("crud.skills._get_table") as g:
        t = MagicMock()
        t.name = "test-skills"
        t.put_item.return_value = {}
        # Default: empty get_item, empty list query, empty update.
        t.get_item.return_value = {"Item": None}
        t.delete_item.return_value = {}
        # IMPORTANT: list_skills loops while LastEvaluatedKey — must return None.
        t.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.update_item.return_value = {"Attributes": {}}
        # Plant a real exception class so `except ...` catches it.
        t.meta.client.exceptions.ConditionalCheckFailedException = _make_ccf_exception_class()
        g.return_value = t
        yield t


@pytest.fixture
def mock_skills_s3():
    with patch("crud.skills._get_s3") as g:
        s = MagicMock()

        # get_object returns a body that .read().decode() works on.
        body = MagicMock()
        body.read.return_value = b"# SKILL.md content"
        s.get_object.return_value = {"Body": body}
        s.put_object.return_value = {}
        s.delete_object.return_value = {}
        # list_objects_v2: paginated loop in get_skill_file/delete_skill (purge).
        # MUST set IsTruncated to a falsy value to break the while-True loop.
        s.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
        s.delete_objects.return_value = {}

        # NoSuchKey exception must be a class derived from Exception so
        # `except s3.exceptions.NoSuchKey` catches it.
        class NoSuchKey(Exception):
            pass

        s.exceptions.NoSuchKey = NoSuchKey
        g.return_value = s
        yield s


# Role-based membership fixtures (must patch at the call-site shared.middleware).


@pytest.fixture
def _mock_owner(workspace_id, user_id):
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
def _mock_admin(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "admin",
        "joined_at": datetime.utcnow().isoformat() + "Z",
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
        "joined_at": datetime.utcnow().isoformat() + "Z",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
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
            "requestId": "req-skill",
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _existing_skill(workspace_id, skill_id="skill_abc123", deleted=False, skill_type="prompt", approved=None):
    if approved is None:
        approved = skill_type != "script"
    return {
        "skillId": skill_id,
        "workspace_id": workspace_id,
        "name": "Existing Skill",
        "description": "desc",
        "type": skill_type,
        "approved": approved,
        "visibility": "private",
        "tags": [],
        "deleted": deleted,
        "created_by": "u1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# LIST SKILLS
# ---------------------------------------------------------------------------


class TestListSkills:
    def test_list_empty(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["items"] == []

    def test_list_returns_skills(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        items = [
            _existing_skill(workspace_id, "skill_a"),
            _existing_skill(workspace_id, "skill_b"),
        ]
        mock_skills_table.query.return_value = {"Items": items, "LastEvaluatedKey": None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 2
        assert data["items"][0]["skillId"] == "skill_a"

    def test_list_filters_deleted_by_default(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        mock_skills_table.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills"))
        call_kwargs = mock_skills_table.query.call_args.kwargs
        assert "deleted" in call_kwargs["FilterExpression"]
        assert call_kwargs["ExpressionAttributeValues"][":f"] is False

    def test_list_show_deleted(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        mock_skills_table.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills",
                       query_params={"deleted": "true"}))
        call_kwargs = mock_skills_table.query.call_args.kwargs
        assert call_kwargs["ExpressionAttributeValues"][":t"] is True

    def test_list_invalid_cursor(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills",
                              query_params={"cursor": "not-base64-or-json"}))
        # Decoder may succeed but JSON parse will fail → bad request
        # OR base64 decode might succeed → catches all in except.
        assert resp["statusCode"] == 400

    def test_list_with_pagination(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        # First call returns one item + a continuation key, second call empty.
        first_key = {"skillId": "skill_x", "workspace_id": workspace_id}
        cursor = base64.b64encode(json.dumps(first_key).encode()).decode()

        mock_skills_table.query.return_value = {
            "Items": [_existing_skill(workspace_id, "skill_y")],
            "LastEvaluatedKey": None,
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills",
                              query_params={"cursor": cursor, "limit": "5"}))
        assert resp["statusCode"] == 200
        # ExclusiveStartKey should have been used
        call_kwargs = mock_skills_table.query.call_args.kwargs
        assert call_kwargs["ExclusiveStartKey"] == first_key

    def test_list_paginated_continues_until_limit(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table
    ):
        """When the first query returns a LastEvaluatedKey and items < limit,
        it should keep querying. Verify we eventually exit (no hang)."""
        # First response: one item + LastEvaluatedKey, second: empty + None.
        responses = [
            {"Items": [_existing_skill(workspace_id, "skill_aaa")],
             "LastEvaluatedKey": {"skillId": "skill_aaa"}},
            {"Items": [], "LastEvaluatedKey": None},
        ]
        mock_skills_table.query.side_effect = responses
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 1

    def test_list_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_skills_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# GET SKILL
# ---------------------------------------------------------------------------


class TestGetSkill:
    def test_get_success(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3):
        skill_id = "skill_abc123"
        mock_skills_table.get_item.return_value = {"Item": _existing_skill(workspace_id, skill_id)}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["skillId"] == skill_id
        # SKILL.md content fetched from S3
        assert data["content"] == "# SKILL.md content"

    def test_get_skill_with_s3_failure_returns_empty_content(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc123"
        mock_skills_table.get_item.return_value = {"Item": _existing_skill(workspace_id, skill_id)}
        mock_skills_s3.get_object.side_effect = Exception("boom")
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["content"] == ""

    def test_get_skill_invalid_id(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        bad_id = "bad id with spaces"
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{bad_id}"))
        assert resp["statusCode"] == 400

    def test_get_skill_not_found(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        mock_skills_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/skill_abc123"))
        assert resp["statusCode"] == 403  # forbidden() — not 404

    def test_get_skill_other_workspace(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        item = _existing_skill("other-ws", "skill_abc123")
        mock_skills_table.get_item.return_value = {"Item": item}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/skill_abc123"))
        assert resp["statusCode"] == 403

    def test_get_skill_deleted(self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table):
        item = _existing_skill(workspace_id, "skill_abc123", deleted=True)
        mock_skills_table.get_item.return_value = {"Item": item}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/skill_abc123"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# CREATE SKILL
# ---------------------------------------------------------------------------


class TestCreateSkill:
    def test_create_success_prompt(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        body = {"name": "MySkill", "description": "test", "type": "prompt", "content": "Hi"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills", body=body))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["name"] == "MySkill"
        assert data["type"] == "prompt"
        assert data["approved"] is True  # prompt skills auto-approved
        mock_skills_table.put_item.assert_called_once()
        # Content was uploaded to S3
        mock_skills_s3.put_object.assert_called_once()

    def test_create_success_script(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        body = {"name": "MyScript", "type": "script"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills", body=body))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["type"] == "script"
        assert data["approved"] is False

    def test_create_no_content_skips_s3(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        body = {"name": "MySkill"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills", body=body))
        assert resp["statusCode"] == 201
        mock_skills_s3.put_object.assert_not_called()

    def test_create_missing_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills", body={}))
        assert resp["statusCode"] == 400
        assert "name" in json.loads(resp["body"])["error"]

    def test_create_blank_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills",
                              body={"name": "   "}))
        assert resp["statusCode"] == 400

    def test_create_name_too_long(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills",
                              body={"name": "a" * 201}))
        assert resp["statusCode"] == 400
        assert "200" in json.loads(resp["body"])["error"]

    def test_create_invalid_type(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills",
                              body={"name": "X", "type": "weird"}))
        assert resp["statusCode"] == 400
        assert "type" in json.loads(resp["body"])["error"]

    def test_viewer_cannot_create(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills",
                              body={"name": "X"}))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# UPDATE SKILL
# ---------------------------------------------------------------------------


class TestUpdateSkill:
    def test_update_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc"
        existing = _existing_skill(workspace_id, skill_id)
        mock_skills_table.get_item.return_value = {"Item": existing}
        updated = dict(existing, name="NewName")
        mock_skills_table.update_item.return_value = {"Attributes": updated}

        body = {
            "name": "NewName",
            "description": "Updated",
            "expected_updated_at": existing["updated_at"],
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              body=body))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["name"] == "NewName"
        mock_skills_table.update_item.assert_called_once()

    def test_update_with_content_uploads_to_s3(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc"
        existing = _existing_skill(workspace_id, skill_id)
        mock_skills_table.get_item.return_value = {"Item": existing}
        mock_skills_table.update_item.return_value = {"Attributes": existing}

        body = {
            "content": "# Updated content",
            "name": "Same",
            "expected_updated_at": existing["updated_at"],
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              body=body))
        assert resp["statusCode"] == 200
        mock_skills_s3.put_object.assert_called_once()

    def test_update_script_with_content_resets_approval(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc"
        existing = _existing_skill(workspace_id, skill_id, skill_type="script", approved=True)
        mock_skills_table.get_item.return_value = {"Item": existing}
        mock_skills_table.update_item.return_value = {"Attributes": existing}

        body = {
            "content": "new code",
            "expected_updated_at": existing["updated_at"],
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              body=body))
        assert resp["statusCode"] == 200
        # Inspect the update expression — should include approved = :false
        call_kwargs = mock_skills_table.update_item.call_args.kwargs
        assert ":false" in call_kwargs["ExpressionAttributeValues"]
        assert call_kwargs["ExpressionAttributeValues"][":false"] is False

    def test_update_invalid_id(self, workspace_id, mock_jwt, _mock_editor, mock_skills_table):
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/has space",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 400

    def test_update_missing_expected_updated_at(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {"Item": _existing_skill(workspace_id, skill_id)}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              body={"name": "x"}))
        assert resp["statusCode"] == 400
        assert "expected_updated_at" in json.loads(resp["body"])["error"]

    def test_update_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", skill_id),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403

    def test_update_deleted(self, workspace_id, mock_jwt, _mock_editor, mock_skills_table):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403

    def test_update_not_found(self, workspace_id, mock_jwt, _mock_editor, mock_skills_table):
        mock_skills_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/skill_abc",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403

    def test_update_version_conflict(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        existing = _existing_skill(workspace_id, skill_id)
        mock_skills_table.get_item.return_value = {"Item": existing}
        ccf = mock_skills_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_skills_table.update_item.side_effect = ccf("conflict")

        body = {"name": "x", "expected_updated_at": "stale"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              body=body))
        assert resp["statusCode"] == 409

    def test_viewer_cannot_update(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table
    ):
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/skill_x",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# DELETE SKILL
# ---------------------------------------------------------------------------


class TestDeleteSkill:
    def test_soft_delete_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["deleted"] is True
        mock_skills_table.update_item.assert_called_once()

    def test_soft_delete_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/bad id"))
        assert resp["statusCode"] == 400

    def test_soft_delete_conditional_check_fails(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        ccf = mock_skills_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_skills_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/skill_abc"))
        assert resp["statusCode"] == 403

    def test_purge_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        # First page has 2 objects, second page empty.
        mock_skills_s3.list_objects_v2.side_effect = [
            {
                "Contents": [
                    {"Key": f"skills/{skill_id}/SKILL.md"},
                    {"Key": f"skills/{skill_id}/scripts/run.sh"},
                ],
                "IsTruncated": False,
            },
        ]

        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              query_params={"purge": "true"}))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["purged"] is True
        mock_skills_s3.delete_objects.assert_called_once()
        mock_skills_table.delete_item.assert_called_once()

    def test_purge_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", "skill_abc"),
        }
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/skill_abc",
                              query_params={"purge": "true"}))
        assert resp["statusCode"] == 403

    def test_purge_no_objects(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        mock_skills_s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              query_params={"purge": "true"}))
        assert resp["statusCode"] == 200
        mock_skills_s3.delete_objects.assert_not_called()

    def test_purge_s3_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        mock_skills_s3.list_objects_v2.side_effect = Exception("S3 boom")
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              query_params={"purge": "true"}))
        assert resp["statusCode"] == 500

    def test_purge_ddb_conditional_check_fails(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        ccf = mock_skills_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_skills_table.delete_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}",
                              query_params={"purge": "true"}))
        assert resp["statusCode"] == 403

    def test_viewer_cannot_delete(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table
    ):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/skill_x"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# IMPORT SKILL
# ---------------------------------------------------------------------------


class TestImportSkill:
    def test_import_basic(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        body = {
            "name": "imported",
            "content": "# Hello",
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import", body=body))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["name"] == "imported"
        # SKILL.md uploaded
        assert mock_skills_s3.put_object.call_count == 1

    def test_import_with_frontmatter(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        body = {
            "content": "---\nname: parsed-name\ndescription: from-fm\n---\n\nbody",
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import", body=body))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["name"] == "parsed-name"
        assert data["description"] == "from-fm"

    def test_import_missing_content(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import",
                              body={"name": "x"}))
        assert resp["statusCode"] == 400
        assert "content" in json.loads(resp["body"])["error"]

    def test_import_missing_name_no_fm(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import",
                              body={"content": "no fm"}))
        assert resp["statusCode"] == 400
        assert "name" in json.loads(resp["body"])["error"]

    def test_import_invalid_type(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import",
                              body={"name": "x", "content": "y", "type": "weird"}))
        assert resp["statusCode"] == 400

    def test_import_with_scripts_and_files(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        body = {
            "name": "imp",
            "content": "# H",
            "scripts": {"run.sh": "echo hi"},
            "files": {"docs/usage.md": "usage info"},
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import", body=body))
        assert resp["statusCode"] == 201
        # SKILL.md + script + file = 3 put_objects
        assert mock_skills_s3.put_object.call_count == 3

    def test_import_invalid_script_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        body = {
            "name": "imp",
            "content": "# H",
            "scripts": {"bad name with space": "echo"},
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import", body=body))
        assert resp["statusCode"] == 400
        assert "script name" in json.loads(resp["body"])["error"].lower()

    def test_import_invalid_file_path(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        body = {
            "name": "imp",
            "content": "# H",
            "files": {"../escape.txt": "evil"},
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import", body=body))
        assert resp["statusCode"] == 400

    def test_viewer_cannot_import(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table
    ):
        body = {"name": "x", "content": "y"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/import", body=body))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# PUBLISH / UNPUBLISH
# ---------------------------------------------------------------------------


class TestPublishSkill:
    def test_publish_success(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/publish"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["visibility"] == "public"

    def test_publish_unapproved_script_rejected(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, skill_type="script", approved=False),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/publish"))
        assert resp["statusCode"] == 400
        assert "approved" in json.loads(resp["body"])["error"].lower()

    def test_publish_other_workspace(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", skill_id),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/publish"))
        assert resp["statusCode"] == 403

    def test_publish_deleted(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/publish"))
        assert resp["statusCode"] == 403

    def test_publish_invalid_id(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/has space/publish"))
        assert resp["statusCode"] == 400

    def test_publish_conditional_failure(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        ccf = mock_skills_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_skills_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/publish"))
        assert resp["statusCode"] == 403

    def test_editor_cannot_publish(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/publish"))
        assert resp["statusCode"] == 403


class TestUnpublishSkill:
    def test_unpublish_success(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/unpublish"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["visibility"] == "private"

    def test_unpublish_invalid_id(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/has space/unpublish"))
        assert resp["statusCode"] == 400

    def test_unpublish_conditional_failure(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        ccf = mock_skills_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_skills_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/unpublish"))
        assert resp["statusCode"] == 403

    def test_editor_cannot_unpublish(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/unpublish"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# APPROVE
# ---------------------------------------------------------------------------


class TestApproveSkill:
    def test_approve_success(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, skill_type="script", approved=False),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/approve"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["approved"] is True

    def test_approve_already_approved(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, skill_type="script", approved=True),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/approve"))
        assert resp["statusCode"] == 400
        assert "already" in json.loads(resp["body"])["error"].lower()

    def test_approve_non_script(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, skill_type="prompt"),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/approve"))
        assert resp["statusCode"] == 400

    def test_approve_other_workspace(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", "skill_x", skill_type="script", approved=False),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/approve"))
        assert resp["statusCode"] == 403

    def test_approve_deleted(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, "skill_x", skill_type="script", deleted=True),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/approve"))
        assert resp["statusCode"] == 403

    def test_approve_invalid_id(
        self, workspace_id, mock_jwt, _mock_admin, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/has space/approve"))
        assert resp["statusCode"] == 400

    def test_editor_cannot_approve(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/approve"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# FILES
# ---------------------------------------------------------------------------


class TestSkillFiles:
    def test_list_files(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        prefix = f"skills/{skill_id}/"
        mock_skills_s3.list_objects_v2.return_value = {
            "Contents": [
                {"Key": f"{prefix}SKILL.md"},
                {"Key": f"{prefix}scripts/run.sh"},
                {"Key": f"{prefix}.hidden"},  # filtered (starts with .)
            ],
            "IsTruncated": False,
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert "SKILL.md" in data["files"]
        assert "scripts/run.sh" in data["files"]
        assert ".hidden" not in data["files"]

    def test_list_files_paginated(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        prefix = f"skills/{skill_id}/"
        mock_skills_s3.list_objects_v2.side_effect = [
            {
                "Contents": [{"Key": f"{prefix}a.txt"}],
                "IsTruncated": True,
                "NextContinuationToken": "tok",
            },
            {
                "Contents": [{"Key": f"{prefix}b.txt"}],
                "IsTruncated": False,
            },
        ]
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["files"] == ["a.txt", "b.txt"]

    def test_list_files_s3_failure(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        mock_skills_s3.list_objects_v2.side_effect = Exception("boom")
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files"))
        assert resp["statusCode"] == 500

    def test_read_file(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        body = MagicMock()
        body.read.return_value = b"file contents"
        mock_skills_s3.get_object.return_value = {"Body": body}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "scripts/run.sh"}))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["content"] == "file contents"
        assert data["path"] == "scripts/run.sh"

    def test_read_file_not_found(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        mock_skills_s3.get_object.side_effect = mock_skills_s3.exceptions.NoSuchKey("nope")
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "missing.txt"}))
        assert resp["statusCode"] == 404

    def test_read_file_s3_other_error(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        mock_skills_s3.get_object.side_effect = Exception("network")
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "x.txt"}))
        assert resp["statusCode"] == 500

    def test_read_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        # ".." segment is rejected by validate_path
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "../etc/passwd"}))
        assert resp["statusCode"] == 400

    def test_get_file_other_workspace(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table, mock_skills_s3
    ):
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", "skill_x"),
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/skills/skill_x/files"))
        assert resp["statusCode"] == 403

    def test_put_file_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        body = {"content": "new contents"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              body=body, query_params={"path": "scripts/run.sh"}))
        assert resp["statusCode"] == 200
        mock_skills_s3.put_object.assert_called_once()
        call_kwargs = mock_skills_s3.put_object.call_args.kwargs
        assert call_kwargs["Key"] == f"skills/{skill_id}/scripts/run.sh"

    def test_put_file_skill_md_with_frontmatter_syncs_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        content = "---\nname: SyncedName\ndescription: Synced desc\n---\n\nbody"
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              body={"content": content},
                              query_params={"path": "SKILL.md"}))
        assert resp["statusCode"] == 200
        # update_item should have been called with the synced name + description
        update_call = mock_skills_table.update_item.call_args.kwargs
        assert ":fname" in update_call["ExpressionAttributeValues"]
        assert update_call["ExpressionAttributeValues"][":fname"] == "SyncedName"

    def test_put_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              body={"content": "x"},
                              query_params={"path": "../bad"}))
        assert resp["statusCode"] == 400

    def test_put_file_to_deleted_skill(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              body={"content": "x"},
                              query_params={"path": "f.txt"}))
        assert resp["statusCode"] == 400
        assert "deleted" in json.loads(resp["body"])["error"].lower()

    def test_put_file_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", "skill_x"),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/skill_x/files",
                              body={"content": "x"},
                              query_params={"path": "f.txt"}))
        assert resp["statusCode"] == 403

    def test_put_file_s3_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        mock_skills_s3.put_object.side_effect = Exception("boom")
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              body={"content": "x"},
                              query_params={"path": "f.txt"}))
        assert resp["statusCode"] == 500

    def test_put_file_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table
    ):
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/skills/skill_x/files",
                              body={"content": "x"},
                              query_params={"path": "f.txt"}))
        assert resp["statusCode"] == 403

    def test_delete_file_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "scripts/run.sh"}))
        assert resp["statusCode"] == 200
        mock_skills_s3.delete_object.assert_called_once()

    def test_delete_file_skill_md_blocked(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_x"
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "SKILL.md"}))
        assert resp["statusCode"] == 400
        assert "SKILL.md" in json.loads(resp["body"])["error"]

    def test_delete_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_x"
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "../bad"}))
        assert resp["statusCode"] == 400

    def test_delete_file_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", "skill_x"),
        }
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/skill_x/files",
                              query_params={"path": "f.txt"}))
        assert resp["statusCode"] == 403

    def test_delete_file_deleted_skill(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "f.txt"}))
        assert resp["statusCode"] == 403

    def test_delete_file_s3_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table, mock_skills_s3
    ):
        skill_id = "skill_x"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id),
        }
        mock_skills_s3.delete_object.side_effect = Exception("boom")
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/skills/{skill_id}/files",
                              query_params={"path": "f.txt"}))
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# RESTORE
# ---------------------------------------------------------------------------


class TestRestoreSkill:
    def test_restore_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/restore"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["restored"] is True

    def test_restore_not_deleted(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=False),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/restore"))
        assert resp["statusCode"] == 400

    def test_restore_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill("other-ws", "skill_abc", deleted=True),
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_abc/restore"))
        assert resp["statusCode"] == 403

    def test_restore_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/has space/restore"))
        assert resp["statusCode"] == 400

    def test_restore_conditional_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        ccf = mock_skills_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_skills_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/restore"))
        assert resp["statusCode"] == 403

    def test_restore_other_exception(
        self, workspace_id, mock_jwt, _mock_editor, mock_skills_table
    ):
        skill_id = "skill_abc"
        mock_skills_table.get_item.return_value = {
            "Item": _existing_skill(workspace_id, skill_id, deleted=True),
        }
        mock_skills_table.update_item.side_effect = Exception("boom")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/{skill_id}/restore"))
        assert resp["statusCode"] == 500

    def test_restore_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_skills_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/skills/skill_x/restore"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# RESPONSE HELPER
# ---------------------------------------------------------------------------


class TestSkillResponseHelper:
    def test_skill_response_defaults(self):
        from crud.skills import _skill_response
        r = _skill_response({"skillId": "x", "workspace_id": "w"})
        assert r["skillId"] == "x"
        assert r["type"] == "prompt"
        assert r["approved"] is False
        assert r["visibility"] == "private"
        assert r["tags"] == []
        assert r["deleted"] is False

    def test_skill_response_strips_unknown(self):
        from crud.skills import _skill_response
        r = _skill_response({"skillId": "x", "secret_field": "leak"})
        assert "secret_field" not in r

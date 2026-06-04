"""Tests for crud.tools module."""
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
    return type("ConditionalCheckFailedException", (Exception,), {})


@pytest.fixture
def mock_tools_table():
    with patch("crud.tools._get_table") as g:
        t = MagicMock()
        t.name = "test-tools"
        t.put_item.return_value = {}
        t.get_item.return_value = {"Item": None}
        t.delete_item.return_value = {}
        # list_tools uses query (paginated) AND scan (paginated for builtins).
        # Both must terminate.
        t.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.update_item.return_value = {"Attributes": {}}
        t.meta.client.exceptions.ConditionalCheckFailedException = _make_ccf_exception_class()
        g.return_value = t
        yield t


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
            "requestId": "req-tool",
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


def _existing_tool(workspace_id, tool_id="tool_abc", deleted=False, builtin=False):
    return {
        "toolId": tool_id,
        "workspace_id": workspace_id,
        "name": "Existing Tool",
        "description": "desc",
        "category": "data",
        "code": "def hello(): return 'hi'",
        "builtin": builtin,
        "visibility": "private",
        "deleted": deleted,
        "created_by": "u1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# LIST TOOLS
# ---------------------------------------------------------------------------


class TestListTools:
    def test_list_empty(self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["items"] == []

    def test_list_returns_workspace_tools(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        items = [_existing_tool(workspace_id, "tool_a"), _existing_tool(workspace_id, "tool_b")]
        mock_tools_table.query.return_value = {"Items": items, "LastEvaluatedKey": None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 2

    def test_list_merges_builtin_tools(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        ws_items = [_existing_tool(workspace_id, "tool_user")]
        builtin_items = [_existing_tool("global", "tool_builtin", builtin=True)]
        mock_tools_table.query.return_value = {"Items": ws_items, "LastEvaluatedKey": None}
        mock_tools_table.scan.return_value = {"Items": builtin_items, "LastEvaluatedKey": None}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        ids = [i["toolId"] for i in data["items"]]
        assert "tool_user" in ids
        assert "tool_builtin" in ids
        # builtins are inserted at the front
        assert data["items"][0]["toolId"] == "tool_builtin"

    def test_list_dedups_builtin_in_workspace(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        """If a builtin tool somehow appears in workspace results, it shouldn't duplicate."""
        ws_items = [_existing_tool(workspace_id, "tool_dup")]
        builtin_items = [_existing_tool("global", "tool_dup", builtin=True)]
        mock_tools_table.query.return_value = {"Items": ws_items, "LastEvaluatedKey": None}
        mock_tools_table.scan.return_value = {"Items": builtin_items, "LastEvaluatedKey": None}

        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        ids = [i["toolId"] for i in data["items"]]
        assert ids.count("tool_dup") == 1

    def test_list_invalid_cursor(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools",
                              query_params={"cursor": "not-base64"}))
        assert resp["statusCode"] == 400

    def test_list_with_valid_cursor(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        key = {"toolId": "tool_x", "workspace_id": workspace_id}
        cursor = base64.b64encode(json.dumps(key).encode()).decode()
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools",
                              query_params={"cursor": cursor}))
        assert resp["statusCode"] == 200
        call_kwargs = mock_tools_table.query.call_args.kwargs
        assert call_kwargs["ExclusiveStartKey"] == key

    def test_list_paginated_under_limit(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        """If first query returns < limit items + LastEvaluatedKey, keep querying."""
        responses = [
            {"Items": [_existing_tool(workspace_id, "tool_a")],
             "LastEvaluatedKey": {"toolId": "tool_a"}},
            {"Items": [_existing_tool(workspace_id, "tool_b")],
             "LastEvaluatedKey": None},
        ]
        mock_tools_table.query.side_effect = responses
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 2

    def test_list_builtin_paginated(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        """Multiple scan calls for builtin tools must terminate."""
        scans = [
            {"Items": [_existing_tool("global", "bt_a", builtin=True)],
             "LastEvaluatedKey": {"toolId": "bt_a"}},
            {"Items": [_existing_tool("global", "bt_b", builtin=True)],
             "LastEvaluatedKey": None},
        ]
        mock_tools_table.scan.side_effect = scans
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        ids = [i["toolId"] for i in data["items"]]
        assert "bt_a" in ids and "bt_b" in ids

    def test_list_no_membership(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_tools_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# LIST DELETED TOOLS
# ---------------------------------------------------------------------------


class TestListDeletedTools:
    def test_list_deleted_empty(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/deleted"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data == []

    def test_list_deleted_returns(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        items = [_existing_tool(workspace_id, "tool_a", deleted=True)]
        mock_tools_table.query.return_value = {"Items": items, "LastEvaluatedKey": None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/deleted"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data) == 1
        assert data[0]["toolId"] == "tool_a"

    def test_list_deleted_paginated(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        """Multi-page DDB query must terminate."""
        responses = [
            {"Items": [_existing_tool(workspace_id, "tool_a", deleted=True)],
             "LastEvaluatedKey": {"toolId": "tool_a"}},
            {"Items": [_existing_tool(workspace_id, "tool_b", deleted=True)],
             "LastEvaluatedKey": None},
        ]
        mock_tools_table.query.side_effect = responses
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/deleted"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data) == 2

    def test_list_deleted_uses_filter(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/deleted"))
        call_kwargs = mock_tools_table.query.call_args.kwargs
        assert call_kwargs["FilterExpression"] == "deleted = :t"
        assert call_kwargs["ExpressionAttributeValues"] == {":t": True}


# ---------------------------------------------------------------------------
# GET TOOL
# ---------------------------------------------------------------------------


class TestGetTool:
    def test_get_success(self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table):
        tool_id = "tool_abc"
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool(workspace_id, tool_id),
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/{tool_id}"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["toolId"] == tool_id

    def test_get_invalid_id(self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/has space"))
        assert resp["statusCode"] == 400

    def test_get_not_found(self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table):
        mock_tools_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/tool_x"))
        assert resp["statusCode"] == 403  # forbidden() per impl

    def test_get_other_workspace(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool("other-ws", "tool_x"),
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/tool_x"))
        assert resp["statusCode"] == 403

    def test_get_builtin_cross_workspace_allowed(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        """Builtin tools should be accessible regardless of workspace_id."""
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool("global", "tool_x", builtin=True),
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/tool_x"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["builtin"] is True

    def test_get_deleted_tool(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool(workspace_id, "tool_x", deleted=True),
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/tools/tool_x"))
        assert resp["statusCode"] == 404


# ---------------------------------------------------------------------------
# CREATE TOOL
# ---------------------------------------------------------------------------


class TestCreateTool:
    def test_create_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        body = {
            "name": "MyTool",
            "description": "desc",
            "category": "data",
            "code": "def x(): pass",
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools", body=body))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["name"] == "MyTool"
        assert data["builtin"] is False
        assert data["visibility"] == "private"
        mock_tools_table.put_item.assert_called_once()

    def test_create_missing_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools", body={}))
        assert resp["statusCode"] == 400
        assert "name" in json.loads(resp["body"])["error"]

    def test_create_blank_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools",
                              body={"name": "   "}))
        assert resp["statusCode"] == 400

    def test_create_name_too_long(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools",
                              body={"name": "a" * 201}))
        assert resp["statusCode"] == 400

    def test_create_code_too_large(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        big_code = "a" * (351 * 1024)
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools",
                              body={"name": "x", "code": big_code}))
        assert resp["statusCode"] == 400
        assert "350KB" in json.loads(resp["body"])["error"]

    def test_create_code_unicode_size_check(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        """Size check is on UTF-8 bytes, not chars."""
        # 175k chinese chars = ~525KB UTF-8 → over limit
        big_code = "中" * (175 * 1024)
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools",
                              body={"name": "x", "code": big_code}))
        assert resp["statusCode"] == 400

    def test_viewer_cannot_create(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools",
                              body={"name": "x"}))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# UPDATE TOOL
# ---------------------------------------------------------------------------


class TestUpdateTool:
    def test_update_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        tool_id = "tool_abc"
        existing = _existing_tool(workspace_id, tool_id)
        mock_tools_table.get_item.return_value = {"Item": existing}
        mock_tools_table.update_item.return_value = {
            "Attributes": dict(existing, name="NewName"),
        }
        body = {
            "name": "NewName",
            "description": "Updated",
            "category": "infra",
            "code": "def y(): return 1",
            "expected_updated_at": existing["updated_at"],
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/{tool_id}",
                              body=body))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["name"] == "NewName"

    def test_update_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/has space",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 400

    def test_update_missing_expected_updated_at(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        tool_id = "tool_abc"
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool(workspace_id, tool_id),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/{tool_id}",
                              body={"name": "x"}))
        assert resp["statusCode"] == 400
        assert "expected_updated_at" in json.loads(resp["body"])["error"]

    def test_update_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool("other-ws", "tool_abc"),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/tool_abc",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403

    def test_update_builtin_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool(workspace_id, "tool_abc", builtin=True),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/tool_abc",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403

    def test_update_deleted(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {
            "Item": _existing_tool(workspace_id, "tool_abc", deleted=True),
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/tool_abc",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 404

    def test_update_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        mock_tools_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/tool_abc",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403

    def test_update_code_too_large(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        tool_id = "tool_abc"
        existing = _existing_tool(workspace_id, tool_id)
        mock_tools_table.get_item.return_value = {"Item": existing}
        big_code = "a" * (351 * 1024)
        body = {
            "code": big_code,
            "expected_updated_at": existing["updated_at"],
        }
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/{tool_id}",
                              body=body))
        assert resp["statusCode"] == 400
        assert "350KB" in json.loads(resp["body"])["error"]

    def test_update_version_conflict(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        tool_id = "tool_abc"
        existing = _existing_tool(workspace_id, tool_id)
        mock_tools_table.get_item.return_value = {"Item": existing}
        ccf = mock_tools_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_tools_table.update_item.side_effect = ccf("conflict")
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/{tool_id}",
                              body={"name": "x", "expected_updated_at": "stale"}))
        assert resp["statusCode"] == 409

    def test_viewer_cannot_update(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/tools/tool_x",
                              body={"name": "x", "expected_updated_at": "y"}))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# DELETE TOOL (soft-delete)
# ---------------------------------------------------------------------------


class TestDeleteTool:
    def test_delete_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/tools/tool_abc"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["deleted"] is True
        mock_tools_table.update_item.assert_called_once()

    def test_delete_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/tools/bad id"))
        assert resp["statusCode"] == 400

    def test_delete_conditional_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        ccf = mock_tools_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_tools_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/tools/tool_abc"))
        assert resp["statusCode"] == 403

    def test_viewer_cannot_delete(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/tools/tool_x"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# PUBLISH / UNPUBLISH
# ---------------------------------------------------------------------------


class TestPublishTool:
    def test_publish_success(
        self, workspace_id, mock_jwt, _mock_admin, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/publish"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["visibility"] == "public"

    def test_publish_invalid_id(
        self, workspace_id, mock_jwt, _mock_admin, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/has space/publish"))
        assert resp["statusCode"] == 400

    def test_publish_conditional_failure(
        self, workspace_id, mock_jwt, _mock_admin, mock_tools_table
    ):
        ccf = mock_tools_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_tools_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/publish"))
        assert resp["statusCode"] == 403

    def test_editor_cannot_publish(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/publish"))
        assert resp["statusCode"] == 403


class TestUnpublishTool:
    def test_unpublish_success(
        self, workspace_id, mock_jwt, _mock_admin, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/unpublish"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["visibility"] == "private"

    def test_unpublish_invalid_id(
        self, workspace_id, mock_jwt, _mock_admin, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/has space/unpublish"))
        assert resp["statusCode"] == 400

    def test_unpublish_conditional_failure(
        self, workspace_id, mock_jwt, _mock_admin, mock_tools_table
    ):
        ccf = mock_tools_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_tools_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/unpublish"))
        assert resp["statusCode"] == 403

    def test_editor_cannot_unpublish(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/unpublish"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# RESTORE
# ---------------------------------------------------------------------------


class TestRestoreTool:
    def test_restore_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/restore"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["restored"] is True

    def test_restore_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/has space/restore"))
        assert resp["statusCode"] == 400

    def test_restore_conditional_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        ccf = mock_tools_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_tools_table.update_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/restore"))
        assert resp["statusCode"] == 403

    def test_restore_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/tools/tool_x/restore"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# PERMANENT DELETE
# ---------------------------------------------------------------------------


class TestPermanentDeleteTool:
    def test_permanent_delete_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("DELETE",
                              f"/api/workspaces/{workspace_id}/tools/tool_x/permanent"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["permanent"] is True
        mock_tools_table.delete_item.assert_called_once()

    def test_permanent_delete_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        resp = _invoke(_apigw("DELETE",
                              f"/api/workspaces/{workspace_id}/tools/has space/permanent"))
        assert resp["statusCode"] == 400

    def test_permanent_delete_conditional_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_tools_table
    ):
        """Conditional check fails when tool isn't soft-deleted yet (or is builtin)."""
        ccf = mock_tools_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_tools_table.delete_item.side_effect = ccf("nope")
        resp = _invoke(_apigw("DELETE",
                              f"/api/workspaces/{workspace_id}/tools/tool_x/permanent"))
        assert resp["statusCode"] == 403

    def test_permanent_delete_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_tools_table
    ):
        resp = _invoke(_apigw("DELETE",
                              f"/api/workspaces/{workspace_id}/tools/tool_x/permanent"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# RESPONSE HELPER
# ---------------------------------------------------------------------------


class TestToolResponseHelper:
    def test_tool_response_defaults(self):
        from crud.tools import _tool_response
        r = _tool_response({"toolId": "t"})
        assert r["toolId"] == "t"
        assert r["builtin"] is False
        assert r["visibility"] == "private"
        assert r["deleted"] is False
        assert r["category"] == ""

    def test_tool_response_strips_unknown(self):
        from crud.tools import _tool_response
        r = _tool_response({"toolId": "t", "secret": "leak"})
        assert "secret" not in r

    def test_tool_response_owner_alias(self):
        """`owner` field aliases `created_by` for compat."""
        from crud.tools import _tool_response
        r = _tool_response({"toolId": "t", "created_by": "u1"})
        assert r["owner"] == "u1"
        assert r["created_by"] == "u1"

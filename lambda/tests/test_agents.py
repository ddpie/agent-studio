"""Tests for crud.agents module."""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from crud.agents import _agent_response, _build_agent_item

# ---------------------------------------------------------------------------
# Pure helper tests (no fixtures needed)
# ---------------------------------------------------------------------------


def test_enable_memory_without_workspace_memory_fails():
    from crud.agents import _validate_memory_enable
    with pytest.raises(ValueError, match="memory resource missing"):
        _validate_memory_enable(memory_enabled=True, workspace_memory_id=None)


def test_enable_memory_without_workspace_memory_empty_string_fails():
    from crud.agents import _validate_memory_enable
    with pytest.raises(ValueError, match="memory resource missing"):
        _validate_memory_enable(memory_enabled=True, workspace_memory_id="")


def test_enable_memory_with_workspace_memory_ok():
    from crud.agents import _validate_memory_enable
    _validate_memory_enable(memory_enabled=True, workspace_memory_id="agentstudio-ws-abc")


def test_disable_memory_ok_regardless():
    from crud.agents import _validate_memory_enable
    _validate_memory_enable(memory_enabled=False, workspace_memory_id=None)
    _validate_memory_enable(memory_enabled=False, workspace_memory_id="x")


def test_build_agent_item_defaults_runtime_type_to_zip():
    item = _build_agent_item(
        body={"name": "x"}, ws_id="ws1", agent_id="a1", user_id="u1",
        now="2026-01-01T00:00:00Z",
    )
    assert item["runtime_type"] == "zip"


def test_build_agent_item_accepts_harness_runtime():
    item = _build_agent_item(
        body={"name": "x", "runtime_type": "harness"},
        ws_id="ws1", agent_id="a1", user_id="u1", now="2026-01-01T00:00:00Z",
    )
    assert item["runtime_type"] == "harness"


def test_build_agent_item_coerces_bogus_runtime_to_zip():
    # Unknown runtime_type silently coerced to "zip" — defense in depth
    item = _build_agent_item(
        body={"name": "x", "runtime_type": "bogus"},
        ws_id="ws1", agent_id="a1", user_id="u1", now="2026-01-01T00:00:00Z",
    )
    assert item["runtime_type"] == "zip"


def test_build_agent_item_includes_harness_arn_when_provided():
    item = _build_agent_item(
        body={"name": "x", "runtime_type": "harness", "harness_arn": "arn:x"},
        ws_id="ws1", agent_id="a1", user_id="u1", now="2026-01-01T00:00:00Z",
    )
    assert item["harness_arn"] == "arn:x"


def test_build_agent_item_omits_harness_arn_when_falsy():
    item = _build_agent_item(
        body={"name": "x"},
        ws_id="ws1", agent_id="a1", user_id="u1", now="2026-01-01T00:00:00Z",
    )
    assert "harness_arn" not in item


def test_build_agent_item_default_status_visibility():
    item = _build_agent_item(
        body={"name": "x"},
        ws_id="ws1", agent_id="a1", user_id="u1", now="2026-01-01T00:00:00Z",
    )
    assert item["status"] == "active"
    assert item["visibility"] == "private"
    assert item["created_by"] == "u1"
    assert item["created_at"] == "2026-01-01T00:00:00Z"
    assert item["updated_at"] == "2026-01-01T00:00:00Z"


def test_agent_response_includes_runtime_type():
    item = {
        "agentId": "a1", "workspace_id": "ws1", "name": "x",
        "runtime_type": "harness",
        "harness_arn": "arn:aws:bedrock-agentcore:us-east-1:123:harness/abc",
    }
    resp = _agent_response(item)
    assert resp["runtime_type"] == "harness"
    assert resp["harness_arn"] == "arn:aws:bedrock-agentcore:us-east-1:123:harness/abc"


def test_agent_response_defaults_runtime_type_for_legacy_records():
    # Existing agents without runtime_type field must default to "zip" on read
    item = {"agentId": "a1", "workspace_id": "ws1", "name": "x"}
    resp = _agent_response(item)
    assert resp["runtime_type"] == "zip"
    assert resp.get("harness_arn", "") == ""


def test_agent_response_includes_linked_agents_and_kbs():
    item = {
        "agentId": "a1", "workspace_id": "ws1",
        "linked_agents": [{"agent_id": "x"}],
        "knowledge_bases": ["kb-1", "kb-2"],
    }
    resp = _agent_response(item)
    assert resp["linked_agents"] == [{"agent_id": "x"}]
    assert resp["knowledge_bases"] == ["kb-1", "kb-2"]


def test_agent_response_handles_none_knowledge_bases():
    item = {"agentId": "a1", "workspace_id": "ws1", "knowledge_bases": None}
    resp = _agent_response(item)
    assert resp["knowledge_bases"] == []


def test_update_agent_silently_drops_runtime_type_change(workspace_id, user_id, monkeypatch):
    """runtime_type is immutable after create. PUT body attempts to change it
    must be silently dropped (not error) — the ALLOWED_AGENT_FIELDS allowlist
    omits runtime_type precisely so downstream UpdateExpression never touches it.
    """
    from crud import agents

    existing = {
        "agentId": "a1",
        "workspace_id": workspace_id,
        "name": "old-name",
        "runtime_type": "zip",
        "status": "active",
        "created_by": user_id,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }

    class _FakeTable:
        def __init__(self, stored):
            self.stored = stored
            self.updated_kwargs = None
        def get_item(self, **_):
            return {"Item": self.stored}
        def update_item(self, **kwargs):
            self.updated_kwargs = kwargs
            merged = dict(self.stored)
            merged.update({"display_name": "new display", "updated_at": "2026-02-01T00:00:00Z"})
            return {"Attributes": merged}
        @property
        def meta(self):
            class _M:
                class client:
                    class exceptions:
                        class ConditionalCheckFailedException(Exception):
                            pass
            return _M

    fake = _FakeTable(existing)
    monkeypatch.setattr(agents, "_get_table", lambda: fake)

    # Assert the allowlist is the guard, not some ad-hoc check
    assert "runtime_type" not in agents.ALLOWED_AGENT_FIELDS
    assert "harness_arn" not in agents.ALLOWED_AGENT_FIELDS

    # Simulate a malicious PUT body trying to change runtime_type.
    # The update_agent handler is a Powertools route and expects
    # router.current_event — we exercise the allowlist logic directly
    # by inspecting which fields flow into UpdateExpression.
    body = {
        "runtime_type": "harness",    # should be dropped
        "harness_arn": "arn:fake",     # should be dropped
        "display_name": "new display", # legit update
    }
    # Build the UpdateExpression the same way update_agent does (lines 265-280):
    update_parts = []
    for field in agents.ALLOWED_AGENT_FIELDS:
        if field in body:
            update_parts.append(field)
    assert "runtime_type" not in update_parts
    assert "harness_arn" not in update_parts
    assert "display_name" in update_parts


# ---------------------------------------------------------------------------
# Handler tests — fixtures + apigw event helper
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "test-origin")


@pytest.fixture
def mock_agents_table():
    with patch("crud.agents._get_table") as g:
        t = MagicMock()
        t.name = "test-agents"
        t.get_item.return_value = {"Item": None}
        t.put_item.return_value = {}
        # query / update / delete return real dicts so any
        # `while LastEvaluatedKey:` style loop terminates.
        t.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.update_item.return_value = {"Attributes": {}}
        t.delete_item.return_value = {}

        # ConditionalCheckFailedException as a real Exception subclass so
        # the handler's `except table.meta.client.exceptions.ConditionalCheckFailedException`
        # can be programmatically raised by test-side mocks.
        class _CCFE(Exception):
            pass
        t.meta.client.exceptions.ConditionalCheckFailedException = _CCFE
        g.return_value = t
        yield t


@pytest.fixture
def mock_ws_table_for_agents():
    """Workspace table — used by create/update_agent for memory validation."""
    with patch("crud.agents._get_ws_table") as g:
        t = MagicMock()
        t.get_item.return_value = {"Item": {}}
        g.return_value = t
        yield t


@pytest.fixture
def mock_s3():
    with patch("crud.agents._get_s3") as g:
        s = MagicMock()
        # Return real responses to keep S3 path exercises clean
        s.get_object.return_value = {
            "Body": MagicMock(read=lambda: b"hello world")
        }
        s.put_object.return_value = {}
        s.delete_object.return_value = {}
        s.copy_object.return_value = {}
        # NoSuchKey exception class
        class _NoSuchKey(Exception):
            pass
        s.exceptions.NoSuchKey = _NoSuchKey

        # Paginator returns one empty page by default (no objects)
        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"Contents": []}])
        s.get_paginator.return_value = paginator
        g.return_value = s
        yield s


# Role membership patches — patched at the call site (shared.middleware)
# because that's where auth_check invokes get_membership.


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


# ---------------------------------------------------------------------------
# LIST AGENTS
# ---------------------------------------------------------------------------


class TestListAgents:
    def test_list_empty(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        mock_agents_table.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["items"] == []

    def test_list_returns_agents(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        mock_agents_table.query.return_value = {
            "Items": [
                {"agentId": "a1", "workspace_id": workspace_id, "name": "Alpha"},
                {"agentId": "a2", "workspace_id": workspace_id, "name": "Beta"},
            ],
            "LastEvaluatedKey": None,
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 2
        assert {a["agentId"] for a in data["items"]} == {"a1", "a2"}

    def test_list_uses_workspace_index(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        mock_agents_table.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents"))
        kwargs = mock_agents_table.query.call_args.kwargs
        assert kwargs["IndexName"] == "workspace-index"

    def test_list_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_agents_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents"))
        assert resp["statusCode"] == 403

    def test_list_invalid_cursor(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        # Pass an obviously invalid cursor — handler should reject with 400
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents",
            query_params={"cursor": "###not-base64"},
        ))
        assert resp["statusCode"] == 400
        assert "cursor" in json.loads(resp["body"])["error"].lower()

    def test_list_paginated_continues_until_limit(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        """When LastEvaluatedKey returned, continue scanning until limit is hit
        or no more pages. With LastEvaluatedKey=None on second call the loop ends."""
        first = {
            "Items": [{"agentId": "a1", "workspace_id": workspace_id, "name": "A"}],
            "LastEvaluatedKey": {"agentId": "a1"},
        }
        second = {
            "Items": [{"agentId": "a2", "workspace_id": workspace_id, "name": "B"}],
            "LastEvaluatedKey": None,
        }
        mock_agents_table.query.side_effect = [first, second]

        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents",
            query_params={"limit": "20"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["items"]) == 2

    def test_list_returns_next_cursor(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        """If the loop exits with LastEvaluatedKey still set (limit reached),
        the response includes a base64-encoded nextCursor."""
        # limit=1; first page already reaches limit and returns LastEvaluatedKey.
        mock_agents_table.query.return_value = {
            "Items": [{"agentId": "a1", "workspace_id": workspace_id, "name": "A"}],
            "LastEvaluatedKey": {"agentId": "a1"},
        }
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents",
            query_params={"limit": "1"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data.get("nextCursor")


# ---------------------------------------------------------------------------
# GET SINGLE AGENT
# ---------------------------------------------------------------------------


class TestGetAgent:
    def test_get_success(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {
            "agentId": "agent1", "workspace_id": workspace_id, "name": "X",
            "status": "active",
        }}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agent1"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["agentId"] == "agent1"

    def test_get_invalid_agent_id(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/has spaces"
        ))
        assert resp["statusCode"] == 400

    def test_get_not_found_returns_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        """Non-existent → 403 (no leak whether the agent exists)."""
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agent1"))
        assert resp["statusCode"] == 403

    def test_get_other_workspace_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": {
            "agentId": "agent1", "workspace_id": "other-ws", "name": "X",
        }}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agent1"))
        assert resp["statusCode"] == 403

    def test_get_archived_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        """Archived agents are hidden behind 403 from normal GET."""
        mock_agents_table.get_item.return_value = {"Item": {
            "agentId": "agent1", "workspace_id": workspace_id,
            "name": "X", "status": "archived",
        }}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agent1"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# CREATE AGENT
# ---------------------------------------------------------------------------


class TestCreateAgent:
    def test_create_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        body = {"name": "MyAgent", "description": "test"}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents", body=body))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["name"] == "MyAgent"
        assert data["runtime_type"] == "zip"
        assert data["status"] == "active"
        mock_agents_table.put_item.assert_called_once()

    def test_create_missing_name(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents", body={}))
        assert resp["statusCode"] == 400
        assert "name" in json.loads(resp["body"])["error"]

    def test_create_blank_name(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents", body={"name": "   "}
        ))
        assert resp["statusCode"] == 400

    def test_create_name_too_long(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents",
            body={"name": "a" * 201},
        ))
        assert resp["statusCode"] == 400
        assert "200" in json.loads(resp["body"])["error"]

    def test_create_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_ws_table_for_agents
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents",
            body={"name": "X"},
        ))
        assert resp["statusCode"] == 403

    def test_create_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents",
            body={"name": "X"},
        ))
        assert resp["statusCode"] == 403

    def test_create_with_memory_enabled_no_workspace_memory_fails(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        """memory.enabled=True but workspace has no memory_id → 400."""
        mock_ws_table_for_agents.get_item.return_value = {"Item": {}}
        body = {"name": "X", "memory": {"enabled": True}}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents", body=body))
        assert resp["statusCode"] == 400
        assert "memory" in json.loads(resp["body"])["error"]

    def test_create_with_memory_enabled_with_workspace_memory_succeeds(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        mock_ws_table_for_agents.get_item.return_value = {
            "Item": {"workspaceId": workspace_id, "memory_id": "mem-x"}
        }
        body = {"name": "X", "memory": {"enabled": True}}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents", body=body))
        assert resp["statusCode"] == 201

    def test_create_with_memory_disabled_no_validation(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        # memory.enabled=False → workspace memory_id not required
        mock_ws_table_for_agents.get_item.return_value = {"Item": {}}
        body = {"name": "X", "memory": {"enabled": False}}
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents", body=body))
        assert resp["statusCode"] == 201

    def test_create_persists_user_provided_fields(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        body = {
            "name": "X",
            "description": "desc",
            "model_id": "model1",
            "supports_images": True,
            "tool_names": ["t1", "t2"],
            "skill_ids": ["s1"],
        }
        resp = _invoke(_apigw("POST", f"/api/workspaces/{workspace_id}/agents", body=body))
        assert resp["statusCode"] == 201
        item = mock_agents_table.put_item.call_args.kwargs["Item"]
        assert item["description"] == "desc"
        assert item["model_id"] == "model1"
        assert item["supports_images"] is True
        assert item["tool_names"] == ["t1", "t2"]
        assert item["skill_ids"] == ["s1"]


# ---------------------------------------------------------------------------
# UPDATE AGENT
# ---------------------------------------------------------------------------


class TestUpdateAgent:
    def _existing(self, workspace_id):
        return {
            "agentId": "agent1",
            "workspace_id": workspace_id,
            "name": "old",
            "status": "active",
            "runtime_type": "zip",
        }

    def test_update_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        updated = dict(self._existing(workspace_id), display_name="new")
        mock_agents_table.update_item.return_value = {"Attributes": updated}

        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1",
            body={"display_name": "new"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["display_name"] == "new"

    def test_update_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/has spaces",
            body={"display_name": "x"},
        ))
        assert resp["statusCode"] == 400

    def test_update_not_found(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1",
            body={"display_name": "x"},
        ))
        assert resp["statusCode"] == 403

    def test_update_archived_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        existing = self._existing(workspace_id)
        existing["status"] = "archived"
        mock_agents_table.get_item.return_value = {"Item": existing}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1",
            body={"display_name": "x"},
        ))
        assert resp["statusCode"] == 403

    def test_update_other_workspace_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        existing = self._existing(workspace_id)
        existing["workspace_id"] = "other-ws"
        mock_agents_table.get_item.return_value = {"Item": existing}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1",
            body={"display_name": "x"},
        ))
        assert resp["statusCode"] == 403

    def test_update_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_ws_table_for_agents
    ):
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1",
            body={"display_name": "x"},
        ))
        assert resp["statusCode"] == 403

    def test_update_version_conflict(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        # Have update_item raise the ConditionalCheckFailedException class
        # we registered on the mocked exceptions namespace.
        ccfe = mock_agents_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_agents_table.update_item.side_effect = ccfe("conflict")

        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1",
            body={"display_name": "x", "expected_updated_at": "2020-01-01"},
        ))
        assert resp["statusCode"] == 409

    def test_update_with_memory_enabled_no_workspace_memory_fails(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_ws_table_for_agents.get_item.return_value = {"Item": {}}
        body = {"memory": {"enabled": True}}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1", body=body,
        ))
        assert resp["statusCode"] == 400
        assert "memory" in json.loads(resp["body"])["error"]

    def test_update_drops_unknown_fields(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_ws_table_for_agents
    ):
        """Unknown fields are dropped silently (not in ALLOWED_AGENT_FIELDS)."""
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_agents_table.update_item.return_value = {"Attributes": self._existing(workspace_id)}

        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1",
            body={"runtime_type": "harness", "display_name": "X"},
        ))
        assert resp["statusCode"] == 200
        kw = mock_agents_table.update_item.call_args.kwargs
        # runtime_type must not appear as an attribute name
        names = kw.get("ExpressionAttributeNames", {})
        assert all(v != "runtime_type" for v in names.values())


# ---------------------------------------------------------------------------
# DELETE AGENT (archive)
# ---------------------------------------------------------------------------


class TestDeleteAgent:
    def test_delete_archives(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1"
        ))
        assert resp["statusCode"] == 200
        # Confirm UpdateItem set status=archived
        kw = mock_agents_table.update_item.call_args.kwargs
        values = kw["ExpressionAttributeValues"]
        assert values[":archived"] == "archived"

    def test_delete_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/has spaces"
        ))
        assert resp["statusCode"] == 400

    def test_delete_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1"
        ))
        assert resp["statusCode"] == 403

    def test_delete_conditional_check_failure_returns_403(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        ccfe = mock_agents_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_agents_table.update_item.side_effect = ccfe("nope")
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1"
        ))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# DEPLOY
# ---------------------------------------------------------------------------


class TestDeploy:
    def _existing(self, workspace_id, **kwargs):
        return {
            "agentId": "agent1",
            "workspace_id": workspace_id,
            "name": "X",
            "status": "active",
            "skill_ids": [],
            **kwargs,
        }

    def test_deploy_no_skills_succeeds(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/deploy"
        ))
        assert resp["statusCode"] == 202
        data = json.loads(resp["body"])
        assert data["status"] == "deploying"

    def test_deploy_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/has spaces/deploy"
        ))
        assert resp["statusCode"] == 400

    def test_deploy_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/deploy"
        ))
        assert resp["statusCode"] == 403

    def test_deploy_with_unapproved_skills_blocked(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {
            "Item": self._existing(workspace_id, skill_ids=["sk1"])
        }
        # Make the skills_table return an unapproved skill in the same workspace
        skills_table = MagicMock()
        skills_table.get_item.return_value = {
            "Item": {"skillId": "sk1", "workspace_id": workspace_id, "approved": False}
        }
        with patch("boto3.resource") as br:
            # boto3.resource is called once inside deploy_agent for the skills table.
            # Return an object whose .Table(...) yields our skills_table mock.
            res = MagicMock()
            res.Table.return_value = skills_table
            br.return_value = res
            resp = _invoke(_apigw(
                "POST", f"/api/workspaces/{workspace_id}/agents/agent1/deploy"
            ))
        assert resp["statusCode"] == 400
        assert "approved" in json.loads(resp["body"])["error"].lower()

    def test_deploy_with_approved_skills_succeeds(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {
            "Item": self._existing(workspace_id, skill_ids=["sk1"])
        }
        skills_table = MagicMock()
        skills_table.get_item.return_value = {
            "Item": {"skillId": "sk1", "workspace_id": workspace_id, "approved": True}
        }
        with patch("boto3.resource") as br:
            res = MagicMock()
            res.Table.return_value = skills_table
            br.return_value = res
            resp = _invoke(_apigw(
                "POST", f"/api/workspaces/{workspace_id}/agents/agent1/deploy"
            ))
        assert resp["statusCode"] == 202


# ---------------------------------------------------------------------------
# PUBLISH / UNPUBLISH
# ---------------------------------------------------------------------------


class TestPublishUnpublish:
    def test_publish_admin_success(
        self, workspace_id, mock_jwt, _mock_admin, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/publish"
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["visibility"] == "public"

    def test_publish_editor_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        """Publish requires admin role."""
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/publish"
        ))
        assert resp["statusCode"] == 403

    def test_publish_invalid_id(
        self, workspace_id, mock_jwt, _mock_admin, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/has spaces/publish"
        ))
        assert resp["statusCode"] == 400

    def test_publish_not_found_403(
        self, workspace_id, mock_jwt, _mock_admin, mock_agents_table
    ):
        ccfe = mock_agents_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_agents_table.update_item.side_effect = ccfe("missing")
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/publish"
        ))
        assert resp["statusCode"] == 403

    def test_unpublish_admin_success(
        self, workspace_id, mock_jwt, _mock_admin, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/unpublish"
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["visibility"] == "private"

    def test_unpublish_editor_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/unpublish"
        ))
        assert resp["statusCode"] == 403

    def test_unpublish_invalid_id(
        self, workspace_id, mock_jwt, _mock_admin, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/has spaces/unpublish"
        ))
        assert resp["statusCode"] == 400

    def test_unpublish_not_found_403(
        self, workspace_id, mock_jwt, _mock_admin, mock_agents_table
    ):
        ccfe = mock_agents_table.meta.client.exceptions.ConditionalCheckFailedException
        mock_agents_table.update_item.side_effect = ccfe("missing")
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/unpublish"
        ))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# GET / PUT FILE
# ---------------------------------------------------------------------------


class TestAgentFiles:
    def _existing(self, workspace_id):
        return {
            "agentId": "agent1",
            "workspace_id": workspace_id,
            "status": "active",
            "name": "X",
        }

    def test_get_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            query_params={"path": "../etc/passwd"},
        ))
        assert resp["statusCode"] == 400

    def test_get_file_invalid_agent_id(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/has spaces/files",
            query_params={"path": "system_prompt.txt"},
        ))
        assert resp["statusCode"] == 400

    def test_get_file_agent_not_found_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            query_params={"path": "system_prompt.txt"},
        ))
        assert resp["statusCode"] == 403

    def test_get_file_success(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        body = b"the prompt"
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: body)
        }
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            query_params={"path": "system_prompt.txt"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["path"] == "system_prompt.txt"
        assert data["content"] == "the prompt"

    def test_get_file_no_such_key_returns_404(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_s3.get_object.side_effect = mock_s3.exceptions.NoSuchKey("missing")
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            query_params={"path": "draft.json"},
        ))
        assert resp["statusCode"] == 404

    def test_get_file_other_s3_error_returns_404(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_s3.get_object.side_effect = RuntimeError("boom")
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            query_params={"path": "draft.json"},
        ))
        assert resp["statusCode"] == 404

    def test_put_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            body={"content": "x"},
            query_params={"path": "../bad"},
        ))
        assert resp["statusCode"] == 400

    def test_put_file_no_content(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            body={},
            query_params={"path": "system_prompt.txt"},
        ))
        assert resp["statusCode"] == 400
        assert "content" in json.loads(resp["body"])["error"]

    def test_put_file_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            body={"content": "hello"},
            query_params={"path": "system_prompt.txt"},
        ))
        assert resp["statusCode"] == 200
        mock_s3.put_object.assert_called_once()
        kw = mock_s3.put_object.call_args.kwargs
        assert kw["Key"] == "agents/agent1/system_prompt.txt"
        assert kw["ContentType"] == "text/plain"

    def test_put_file_python_content_type(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            body={"content": "code"},
            query_params={"path": "tool_definitions.py"},
        ))
        assert resp["statusCode"] == 200
        kw = mock_s3.put_object.call_args.kwargs
        assert kw["ContentType"] == "text/x-python"

    def test_put_file_json_content_type(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            body={"content": "{}"},
            query_params={"path": "draft.json"},
        ))
        assert resp["statusCode"] == 200
        kw = mock_s3.put_object.call_args.kwargs
        assert kw["ContentType"] == "application/json"

    def test_put_file_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/files",
            body={"content": "x"},
            query_params={"path": "system_prompt.txt"},
        ))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# AGENT SKILL FILES
# ---------------------------------------------------------------------------


class TestAgentSkillFiles:
    def _existing(self, workspace_id, **kw):
        return {
            "agentId": "agent1",
            "workspace_id": workspace_id,
            "status": "active",
            **kw,
        }

    def test_list_files_empty(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        # paginator returns empty page
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["files"] == []

    def test_list_files_with_contents(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        prefix = "agents/agent1/skills/sk1/"
        paginator = MagicMock()
        paginator.paginate.return_value = iter([
            {"Contents": [
                {"Key": prefix + "SKILL.md"},
                {"Key": prefix + "scripts/foo.py"},
                {"Key": prefix + ".hidden"},  # filtered
                {"Key": prefix},  # empty rel filtered
            ]}
        ])
        mock_s3.get_paginator.return_value = paginator

        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
        ))
        assert resp["statusCode"] == 200
        files = json.loads(resp["body"])["files"]
        assert "SKILL.md" in files
        assert "scripts/foo.py" in files
        assert ".hidden" not in files

    def test_list_files_bulk_returns_contents(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        prefix = "agents/agent1/skills/sk1/"
        paginator = MagicMock()
        paginator.paginate.return_value = iter([
            {"Contents": [{"Key": prefix + "SKILL.md"}]}
        ])
        mock_s3.get_paginator.return_value = paginator
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"# hi")}

        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"bulk": "true"},
        ))
        assert resp["statusCode"] == 200
        files = json.loads(resp["body"])["files"]
        assert files == {"SKILL.md": "# hi"}

    def test_get_skill_file_by_path(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"content")}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"path": "SKILL.md"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["content"] == "content"

    def test_get_skill_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"path": "../escape"},
        ))
        assert resp["statusCode"] == 400

    def test_get_skill_file_s3_error_404(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_s3.get_object.side_effect = RuntimeError("boom")
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"path": "SKILL.md"},
        ))
        assert resp["statusCode"] == 404

    def test_get_skill_file_archived_agent_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        existing = self._existing(workspace_id, status="archived")
        mock_agents_table.get_item.return_value = {"Item": existing}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"path": "SKILL.md"},
        ))
        assert resp["statusCode"] == 403

    def test_get_skill_file_invalid_skill_id(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table, mock_s3
    ):
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/skills/has spaces/files",
        ))
        assert resp["statusCode"] == 400

    def test_put_skill_file_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            body={"content": "## hi"},
            query_params={"path": "SKILL.md"},
        ))
        assert resp["statusCode"] == 200
        kw = mock_s3.put_object.call_args.kwargs
        assert kw["ContentType"] == "text/markdown"
        assert kw["Key"] == "agents/agent1/skills/sk1/SKILL.md"

    def test_put_skill_file_python_ct(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            body={"content": "code"},
            query_params={"path": "scripts/run.py"},
        ))
        assert resp["statusCode"] == 200
        kw = mock_s3.put_object.call_args.kwargs
        assert kw["ContentType"] == "text/x-python"

    def test_put_skill_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            body={"content": "x"},
            query_params={"path": "../escape"},
        ))
        assert resp["statusCode"] == 400

    def test_put_skill_file_s3_failure_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_s3.put_object.side_effect = RuntimeError("boom")
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            body={"content": "x"},
            query_params={"path": "SKILL.md"},
        ))
        assert resp["statusCode"] == 500

    def test_delete_skill_file_specific(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"path": "SKILL.md"},
        ))
        assert resp["statusCode"] == 200
        mock_s3.delete_object.assert_called_once()

    def test_delete_skill_file_invalid_path(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"path": "../bad"},
        ))
        assert resp["statusCode"] == 400

    def test_delete_skill_file_all_for_skill(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        prefix = "agents/agent1/skills/sk1/"
        paginator = MagicMock()
        paginator.paginate.return_value = iter([
            {"Contents": [{"Key": prefix + "SKILL.md"}, {"Key": prefix + "scripts/x.py"}]}
        ])
        mock_s3.get_paginator.return_value = paginator
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
        ))
        assert resp["statusCode"] == 200
        # Two delete_object calls
        assert mock_s3.delete_object.call_count == 2

    def test_delete_skill_file_specific_failure_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        mock_s3.delete_object.side_effect = RuntimeError("boom")
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
            query_params={"path": "SKILL.md"},
        ))
        assert resp["statusCode"] == 500

    def test_delete_skill_file_bulk_failure_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {"Item": self._existing(workspace_id)}
        # paginator throws
        paginator = MagicMock()
        paginator.paginate.side_effect = RuntimeError("boom")
        mock_s3.get_paginator.return_value = paginator
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agent1/skills/sk1/files",
        ))
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# COPY SKILL FILES FROM (cross-agent / cross-workspace)
# ---------------------------------------------------------------------------


class TestCopySkillFilesFrom:
    def test_missing_source_fields(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "active"}
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
            body={},
        ))
        assert resp["statusCode"] == 400

    def test_invalid_source_id(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
            body={"sourceAgentId": "has spaces", "sourceSkillId": "sk0"},
        ))
        assert resp["statusCode"] == 400

    def test_dst_agent_archived_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "archived"}
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
            body={"sourceAgentId": "src", "sourceSkillId": "sk0"},
        ))
        assert resp["statusCode"] == 403

    def test_source_agent_not_found_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        # First call returns dst agent (active, in-workspace). Second call
        # (source agent) returns nothing → forbidden.
        mock_agents_table.get_item.side_effect = [
            {"Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "active"}},
            {"Item": None},
        ]
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
            body={"sourceAgentId": "src", "sourceSkillId": "sk0"},
        ))
        assert resp["statusCode"] == 403

    def test_source_same_workspace_copies_files(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.side_effect = [
            {"Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "active"}},
            {"Item": {"agentId": "src", "workspace_id": workspace_id, "status": "active"}},
        ]
        src_prefix = "agents/src/skills/sk0/"
        paginator = MagicMock()
        paginator.paginate.return_value = iter([
            {"Contents": [
                {"Key": src_prefix + "SKILL.md"},
                {"Key": src_prefix + "scripts/run.py"},
                {"Key": src_prefix},  # empty rel skipped
            ]}
        ])
        mock_s3.get_paginator.return_value = paginator
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
            body={"sourceAgentId": "src", "sourceSkillId": "sk0"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["copied"] == 2
        assert mock_s3.copy_object.call_count == 2

    def test_source_other_workspace_requires_membership(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        """When source agent belongs to a different workspace, caller must have
        viewer+ access there. We patch get_membership at the agents module
        import site to return None (no access) and assert 403."""
        mock_agents_table.get_item.side_effect = [
            {"Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "active"}},
            {"Item": {"agentId": "src", "workspace_id": "other-ws", "status": "active"}},
        ]
        with patch("crud.agents.get_membership", return_value=None):
            resp = _invoke(_apigw(
                "POST",
                f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
                body={"sourceAgentId": "src", "sourceSkillId": "sk0"},
            ))
        assert resp["statusCode"] == 403

    def test_source_other_workspace_with_membership_succeeds(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.side_effect = [
            {"Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "active"}},
            {"Item": {"agentId": "src", "workspace_id": "other-ws", "status": "active"}},
        ]
        # paginator empty so no copy invocations
        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"Contents": []}])
        mock_s3.get_paginator.return_value = paginator
        with patch("crud.agents.get_membership", return_value={"role": "viewer"}):
            resp = _invoke(_apigw(
                "POST",
                f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
                body={"sourceAgentId": "src", "sourceSkillId": "sk0"},
            ))
        assert resp["statusCode"] == 200

    def test_source_other_workspace_no_workspace_id_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        """Source agent record has no workspace_id field at all."""
        mock_agents_table.get_item.side_effect = [
            {"Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "active"}},
            {"Item": {"agentId": "src"}},  # missing workspace_id
        ]
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
            body={"sourceAgentId": "src", "sourceSkillId": "sk0"},
        ))
        assert resp["statusCode"] == 403

    def test_copy_s3_failure_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table, mock_s3
    ):
        mock_agents_table.get_item.side_effect = [
            {"Item": {"agentId": "dst", "workspace_id": workspace_id, "status": "active"}},
            {"Item": {"agentId": "src", "workspace_id": workspace_id, "status": "active"}},
        ]
        paginator = MagicMock()
        paginator.paginate.side_effect = RuntimeError("boom")
        mock_s3.get_paginator.return_value = paginator
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/dst/skills/sk1/copy-from",
            body={"sourceAgentId": "src", "sourceSkillId": "sk0"},
        ))
        assert resp["statusCode"] == 500

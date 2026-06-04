"""Tests for lambda/crud/memories.py — GET /my-memories."""
import json
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def mock_agentcore_data():
    with patch("crud.memories._get_data") as g:
        m = MagicMock()
        g.return_value = m
        yield m


@pytest.fixture
def mock_ws_table():
    with patch("crud.memories._get_workspaces_table") as g:
        t = MagicMock()
        t.get_item.return_value = {
            "Item": {"workspaceId": "ws1", "sk": "META", "memory_id": "mem-abc"}
        }
        g.return_value = t
        yield t


def test_list_initial_load_four_sections(mock_agentcore_data, mock_ws_table):
    from crud.memories import _list_my_memories_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"memoryRecordId": "r1", "content": {"text": "prefers Chinese"},
             "createdAt": 1714000000, "namespace": "/users/a_u/preferences/"}
        ],
        "nextToken": None,
    }
    result = _list_my_memories_impl(
        workspace_id="ws1", agent_id="a", caller_id="u",
        strategy=None, next_token=None)
    assert set(result.keys()) == {"preferences", "facts", "summaries", "episodes"}
    for section in result.values():
        assert "records" in section
        assert "nextToken" in section
    assert mock_agentcore_data.list_memory_records.call_count == 4


def test_list_uses_actor_id(mock_agentcore_data, mock_ws_table):
    from crud.memories import _list_my_memories_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [], "nextToken": None}
    _list_my_memories_impl(
        workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
        strategy="preferences", next_token=None)
    call = mock_agentcore_data.list_memory_records.call_args
    assert call.kwargs["namespace"] == "/users/agent-X_user-Y/preferences/"


def test_list_single_strategy_pagination(mock_agentcore_data, mock_ws_table):
    from crud.memories import _list_my_memories_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"memoryRecordId": f"r{i}", "content": {"text": f"f{i}"},
             "createdAt": 1714000000 + i, "namespace": "/users/a_u/facts/"}
            for i in range(50)
        ],
        "nextToken": "page-2-token",
    }
    result = _list_my_memories_impl(
        workspace_id="ws1", agent_id="a", caller_id="u",
        strategy="facts", next_token=None, max_results=50)
    assert len(result["records"]) == 50
    assert result["nextToken"] == "page-2-token"


def test_list_forwards_next_token(mock_agentcore_data, mock_ws_table):
    from crud.memories import _list_my_memories_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [], "nextToken": None}
    _list_my_memories_impl(
        workspace_id="ws1", agent_id="a", caller_id="u",
        strategy="facts", next_token="my-cursor", max_results=50)
    call = mock_agentcore_data.list_memory_records.call_args
    assert call.kwargs["nextToken"] == "my-cursor"


def test_list_missing_memory_id_returns_empty(mock_agentcore_data, mock_ws_table):
    from crud.memories import _list_my_memories_impl
    mock_ws_table.get_item.return_value = {
        "Item": {"workspaceId": "ws1", "sk": "META"}}
    result = _list_my_memories_impl(
        workspace_id="ws1", agent_id="a", caller_id="u",
        strategy=None, next_token=None)
    assert result == {}
    mock_agentcore_data.list_memory_records.assert_not_called()


def test_list_unknown_strategy_raises(mock_agentcore_data, mock_ws_table):
    from crud.memories import _list_my_memories_impl
    with pytest.raises(ValueError):
        _list_my_memories_impl(
            workspace_id="ws1", agent_id="a", caller_id="u",
            strategy="bogus", next_token=None)


def test_delete_record_success(mock_agentcore_data, mock_ws_table):
    """Caller-owned record found via list_memory_records → delete fires."""
    from crud.memories import _delete_record_impl
    # First strategy queried returns the record; outer loop exits after found.
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [{"memoryRecordId": "mem-123"}],
        "nextToken": None,
    }
    mock_agentcore_data.delete_memory_record.return_value = {"memoryRecordId": "mem-123"}
    _delete_record_impl(
        workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
        record_id="mem-123")
    mock_agentcore_data.delete_memory_record.assert_called_once_with(
        memoryId="mem-abc", memoryRecordId="mem-123")


def test_delete_record_cross_user_silent_noop(mock_agentcore_data, mock_ws_table):
    """Record not in caller's namespace → silent success (idempotent design).

    The implementation deliberately doesn't raise on cross-user attempts;
    list_memory_records scoped to the caller's namespace simply doesn't
    return the foreign record, so 'found' stays False and we no-op. The
    caller can't tell whether the record never existed or belonged to
    someone else — by design.
    """
    from crud.memories import _delete_record_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [],
        "nextToken": None,
    }
    _delete_record_impl(
        workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
        record_id="mem-OTHER")
    mock_agentcore_data.delete_memory_record.assert_not_called()


def test_delete_record_cross_agent_silent_noop(mock_agentcore_data, mock_ws_table):
    """Record under a different agent's namespace → silent no-op (same as cross-user)."""
    from crud.memories import _delete_record_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [],
        "nextToken": None,
    }
    _delete_record_impl(
        workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
        record_id="mem-1")
    mock_agentcore_data.delete_memory_record.assert_not_called()


def test_delete_record_already_deleted_idempotent(mock_agentcore_data, mock_ws_table):
    """ResourceNotFoundException from delete_memory_record is swallowed (idempotent)."""
    from crud.memories import _delete_record_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [{"memoryRecordId": "mem-x"}],
        "nextToken": None,
    }

    class _RNF(Exception):
        pass

    mock_agentcore_data.exceptions.ResourceNotFoundException = _RNF
    mock_agentcore_data.delete_memory_record.side_effect = _RNF()
    # Must not raise.
    _delete_record_impl(
        workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
        record_id="mem-x")


# ---------------------------------------------------------------------------
# forget-all (DELETE /my-memories)
# ---------------------------------------------------------------------------


def test_forget_all_under_limit(mock_agentcore_data, mock_ws_table):
    from crud.memories import _forget_all_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"memoryRecordId": f"r{i}", "namespace": "/users/a_u/preferences/"}
            for i in range(5)
        ],
        "nextToken": None,
    }
    mock_agentcore_data.delete_memory_record.return_value = {}
    result = _forget_all_impl(workspace_id="ws1", agent_id="a", caller_id="u")
    # 4 namespaces × 5 records = 20
    assert result["deleted"] == 20
    assert result["partial"] is False


def test_forget_all_over_limit(mock_agentcore_data, mock_ws_table):
    from crud.memories import _forget_all_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"memoryRecordId": f"r{i}", "namespace": "/users/a_u/preferences/"}
            for i in range(300)
        ],
        "nextToken": None,
    }
    mock_agentcore_data.delete_memory_record.return_value = {}
    result = _forget_all_impl(workspace_id="ws1", agent_id="a", caller_id="u")
    assert result["deleted"] == 1000
    assert result["partial"] is True


def test_forget_all_no_memory_id(mock_agentcore_data, mock_ws_table):
    from crud.memories import _forget_all_impl
    mock_ws_table.get_item.return_value = {
        "Item": {"workspaceId": "ws1", "sk": "META"}}
    result = _forget_all_impl(workspace_id="ws1", agent_id="a", caller_id="u")
    assert result == {"deleted": 0, "partial": False}
    mock_agentcore_data.list_memory_records.assert_not_called()


# ---------------------------------------------------------------------------
# Pure helpers + lazy init
# ---------------------------------------------------------------------------


def test_get_data_lazy_init():
    import crud.memories as mod
    mod._data = None
    sentinel = MagicMock()
    with patch("boto3.client", return_value=sentinel) as bc:
        assert mod._get_data() is sentinel
        # Cached
        assert mod._get_data() is sentinel
        bc.assert_called_once()
    mod._data = None


def test_get_workspaces_table_lazy_init():
    import crud.memories as mod
    mod._workspaces_table = None
    sentinel_table = MagicMock()
    sentinel_resource = MagicMock()
    sentinel_resource.Table.return_value = sentinel_table
    with patch("boto3.resource", return_value=sentinel_resource) as br:
        assert mod._get_workspaces_table() is sentinel_table
        assert mod._get_workspaces_table() is sentinel_table
        br.assert_called_once()
    mod._workspaces_table = None


def test_get_workspace_memory_id_no_item(mock_ws_table):
    from crud.memories import _get_workspace_memory_id
    mock_ws_table.get_item.return_value = {}
    assert _get_workspace_memory_id("ws1") is None


def test_namespace_for_unknown_strategy_raises():
    from crud.memories import _namespace_for
    with pytest.raises(ValueError, match="unknown strategy"):
        _namespace_for("not-a-strategy", "actor_x")


def test_list_one_section_swallows_exception(mock_agentcore_data):
    from crud.memories import _list_one_section
    mock_agentcore_data.list_memory_records.side_effect = Exception("boom")
    result = _list_one_section("mem-1", "/users/a_u/preferences/", 20, None)
    assert result == {"records": [], "nextToken": None}


def test_list_one_section_with_next_token(mock_agentcore_data):
    from crud.memories import _list_one_section
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [], "nextToken": None,
    }
    _list_one_section("mem-1", "/users/a_u/facts/", 50, "page-token")
    call = mock_agentcore_data.list_memory_records.call_args
    assert call.kwargs["nextToken"] == "page-token"


def test_extract_actor_id_from_namespace_match():
    from crud.memories import _extract_actor_id_from_namespace
    assert _extract_actor_id_from_namespace("/users/agent_user/preferences/") == "agent_user"


def test_extract_actor_id_from_namespace_unparseable():
    from crud.memories import _extract_actor_id_from_namespace
    with pytest.raises(ValueError, match="unparseable"):
        _extract_actor_id_from_namespace("/teams/foo/")


def test_delete_record_no_memory_id(mock_agentcore_data, mock_ws_table):
    """No memory_id configured for workspace → silent return."""
    from crud.memories import _delete_record_impl
    mock_ws_table.get_item.return_value = {
        "Item": {"workspaceId": "ws1", "sk": "META"}}
    _delete_record_impl(workspace_id="ws1", agent_id="a", caller_id="u",
                        record_id="r1")
    mock_agentcore_data.list_memory_records.assert_not_called()
    mock_agentcore_data.delete_memory_record.assert_not_called()


def test_delete_record_pagination_until_found(mock_agentcore_data, mock_ws_table):
    """First page doesn't contain the record; next page does."""
    from crud.memories import _delete_record_impl
    mock_agentcore_data.list_memory_records.side_effect = [
        {"memoryRecordSummaries": [{"memoryRecordId": "other"}],
         "nextToken": "page-2"},
        {"memoryRecordSummaries": [{"memoryRecordId": "r1"}],
         "nextToken": None},
    ]

    class _RNF(Exception):
        pass

    mock_agentcore_data.exceptions.ResourceNotFoundException = _RNF
    mock_agentcore_data.delete_memory_record.return_value = {}
    _delete_record_impl(workspace_id="ws1", agent_id="a", caller_id="u",
                        record_id="r1")
    mock_agentcore_data.delete_memory_record.assert_called_once_with(
        memoryId="mem-abc", memoryRecordId="r1")


def test_delete_record_list_exception_breaks_inner(mock_agentcore_data, mock_ws_table):
    """list_memory_records raising mid-pagination breaks out and tries next strategy."""
    from crud.memories import _delete_record_impl
    # First call (preferences) raises; rest return empty
    call_count = {"n": 0}

    def list_side(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("transient")
        return {"memoryRecordSummaries": [], "nextToken": None}

    mock_agentcore_data.list_memory_records.side_effect = list_side
    _delete_record_impl(workspace_id="ws1", agent_id="a", caller_id="u",
                        record_id="r1")
    # Iterates through 4 strategies; first raises, rest each list once
    assert call_count["n"] == 4
    mock_agentcore_data.delete_memory_record.assert_not_called()


def test_delete_record_with_strategy_filter(mock_agentcore_data, mock_ws_table):
    """Strategy filter limits search to a single namespace."""
    from crud.memories import _delete_record_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [{"memoryRecordId": "r1"}],
        "nextToken": None,
    }

    class _RNF(Exception):
        pass

    mock_agentcore_data.exceptions.ResourceNotFoundException = _RNF
    mock_agentcore_data.delete_memory_record.return_value = {}
    _delete_record_impl(workspace_id="ws1", agent_id="a", caller_id="u",
                        record_id="r1", strategy="facts")
    # Only 1 strategy queried
    assert mock_agentcore_data.list_memory_records.call_count == 1
    call = mock_agentcore_data.list_memory_records.call_args
    assert call.kwargs["namespace"] == "/users/a_u/facts/"


def test_forget_all_handles_list_exception(mock_agentcore_data, mock_ws_table):
    """list_memory_records raising for one strategy → continues to next."""
    from crud.memories import _forget_all_impl
    call_count = {"n": 0}

    def list_side(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("transient list")
        return {"memoryRecordSummaries": [], "nextToken": None}

    mock_agentcore_data.list_memory_records.side_effect = list_side
    result = _forget_all_impl(workspace_id="ws1", agent_id="a", caller_id="u")
    assert result == {"deleted": 0, "partial": False}


def test_forget_all_handles_delete_exception(mock_agentcore_data, mock_ws_table):
    """One delete fails — others still proceed; deletion count is exact."""
    from crud.memories import _forget_all_impl
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"memoryRecordId": "r1"},
            {"memoryRecordId": "r2"},
        ],
        "nextToken": None,
    }

    def delete_side(memoryId, memoryRecordId):
        if memoryRecordId == "r1":
            raise Exception("delete failed")
        return {}

    mock_agentcore_data.delete_memory_record.side_effect = delete_side
    result = _forget_all_impl(workspace_id="ws1", agent_id="a", caller_id="u")
    # 4 strategies × (2 records — 1 r1 fail) = 4 successful deletes
    assert result["deleted"] == 4
    assert result["partial"] is False


def test_forget_all_pagination(mock_agentcore_data, mock_ws_table):
    """When list returns nextToken, loop continues until it's None."""
    from crud.memories import _forget_all_impl

    pages = [
        # First page: 2 records, has nextToken
        {"memoryRecordSummaries": [
            {"memoryRecordId": "r1"}, {"memoryRecordId": "r2"},
        ], "nextToken": "tok1"},
        # Second page: 1 record, no nextToken
        {"memoryRecordSummaries": [{"memoryRecordId": "r3"}],
         "nextToken": None},
        # All subsequent strategies return empty
    ]
    extra_empty = [{"memoryRecordSummaries": [], "nextToken": None}] * 6

    mock_agentcore_data.list_memory_records.side_effect = pages + extra_empty
    mock_agentcore_data.delete_memory_record.return_value = {}
    result = _forget_all_impl(workspace_id="ws1", agent_id="a", caller_id="u")
    # First strategy: 3 records deleted; others: 0
    assert result["deleted"] == 3
    assert result["partial"] is False


# ---------------------------------------------------------------------------
# Route-level tests (list_my_memories / delete_my_memory / forget_all)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_origin(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "")


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
    member = {"workspaceId": workspace_id, "sk": f"MEMBER#{user_id}",
              "userId": user_id, "role": "viewer"}
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    with patch("shared.middleware.get_membership", return_value=None):
        yield


def _apigw(method, path, query_params=None):
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
        "body": None,
        "queryStringParameters": query_params or {},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


def test_route_list_returns_404_when_no_memory_id(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    mock_ws_table.get_item.return_value = {"Item": {"workspaceId": workspace_id, "sk": "META"}}
    resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories"))
    assert resp["statusCode"] == 404


def test_route_list_returns_200_with_data(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [], "nextToken": None,
    }
    resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories"))
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert set(data.keys()) == {"preferences", "facts", "summaries", "episodes"}


def test_route_list_unknown_strategy_400(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    resp = _invoke(_apigw(
        "GET", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories",
        query_params={"strategy": "unknown"}))
    assert resp["statusCode"] == 400


def test_route_list_strategy_max_results_invalid(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    resp = _invoke(_apigw(
        "GET", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories",
        query_params={"strategy": "facts", "maxResults": "not-int"}))
    assert resp["statusCode"] == 400
    assert "integer" in json.loads(resp["body"])["error"]


def test_route_list_strategy_max_results_capped(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    """maxResults too large should be capped at MAX_PAGE_SIZE (100)."""
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [], "nextToken": None,
    }
    resp = _invoke(_apigw(
        "GET", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories",
        query_params={"strategy": "facts", "maxResults": "999"}))
    assert resp["statusCode"] == 200
    call = mock_agentcore_data.list_memory_records.call_args
    assert call.kwargs["maxResults"] == 100


def test_route_list_no_membership_403(
    workspace_id, mock_jwt, _mock_no_membership
):
    resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories"))
    assert resp["statusCode"] == 403


def test_route_delete_record_success(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [{"memoryRecordId": "rec-1"}], "nextToken": None,
    }

    class _RNF(Exception):
        pass

    mock_agentcore_data.exceptions.ResourceNotFoundException = _RNF
    mock_agentcore_data.delete_memory_record.return_value = {}
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories/rec-1"))
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["deleted"] == "rec-1"


def test_route_delete_record_unknown_strategy_400(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    resp = _invoke(_apigw(
        "DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories/rec-1",
        query_params={"strategy": "bogus"}))
    assert resp["statusCode"] == 400


def test_route_delete_record_handles_value_error(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    """When the impl raises a generic ValueError from a strategy resolution
    e.g. _namespace_for of a known plural still works, so we trigger it via
    a non-strategy path. Instead, simulate via patch."""
    with patch("crud.memories._delete_record_impl", side_effect=ValueError("bad")):
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories/rec-1"))
    assert resp["statusCode"] == 400


def test_route_delete_record_handles_unexpected_error(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    with patch("crud.memories._delete_record_impl", side_effect=RuntimeError("boom")):
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories/rec-1"))
    assert resp["statusCode"] == 500


def test_route_delete_record_forbidden(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    """MemoryForbidden raised by impl maps to 403."""
    from crud.memories import MemoryForbidden
    with patch("crud.memories._delete_record_impl",
               side_effect=MemoryForbidden("nope")):
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories/rec-1"))
    assert resp["statusCode"] == 403


def test_route_delete_record_no_membership_403(
    workspace_id, mock_jwt, _mock_no_membership
):
    resp = _invoke(_apigw(
        "DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories/rec-1"))
    assert resp["statusCode"] == 403


def test_route_forget_all_success(
    workspace_id, mock_jwt, _mock_viewer, mock_agentcore_data, mock_ws_table
):
    mock_agentcore_data.list_memory_records.return_value = {
        "memoryRecordSummaries": [], "nextToken": None,
    }
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories"))
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["deleted"] == 0
    assert data["partial"] is False


def test_route_forget_all_handles_unexpected_error(
    workspace_id, mock_jwt, _mock_viewer
):
    with patch("crud.memories._forget_all_impl", side_effect=RuntimeError("boom")):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories"))
    assert resp["statusCode"] == 500


def test_route_forget_all_no_membership_403(
    workspace_id, mock_jwt, _mock_no_membership
):
    resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories"))
    assert resp["statusCode"] == 403


def test_route_list_handles_value_error_from_impl(
    workspace_id, mock_jwt, _mock_viewer
):
    """_list_my_memories_impl raising ValueError should map to 400."""
    with patch("crud.memories._list_my_memories_impl",
               side_effect=ValueError("bad strategy from impl")):
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/my-memories"))
    assert resp["statusCode"] == 400
    assert "bad strategy" in json.loads(resp["body"])["error"]


def test_forget_all_strategy_loop_break_over_cap(mock_agentcore_data, mock_ws_table):
    """Outer strategy loop hits cap check at top → break with partial=True.

    To trigger the OUTER (line 263-265) check we need to enter a strategy
    iteration with deleted already >= cap. Use list_memory_records that
    yields exactly _FORGET_ALL_HARD_CAP records on first strategy, no
    nextToken; the second strategy iteration's `if deleted >= cap:` fires.
    """
    from crud.memories import _forget_all_impl, _FORGET_ALL_HARD_CAP
    # Enough records on first strategy to exactly reach cap
    records = [{"memoryRecordId": f"r{i}"} for i in range(_FORGET_ALL_HARD_CAP)]
    pages = [{"memoryRecordSummaries": records, "nextToken": None}]
    extra_empty = [{"memoryRecordSummaries": [], "nextToken": None}] * 4
    mock_agentcore_data.list_memory_records.side_effect = pages + extra_empty
    mock_agentcore_data.delete_memory_record.return_value = {}
    result = _forget_all_impl(workspace_id="ws1", agent_id="a", caller_id="u")
    # deleted reached cap during first strategy; partial set inside inner
    # loop. Outer break also marks partial.
    assert result["deleted"] == _FORGET_ALL_HARD_CAP
    assert result["partial"] is True

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
    from crud.memories import _delete_record_impl
    mock_agentcore_data.get_memory_record.return_value = {
        "memoryRecord": {
            "memoryRecordId": "mem-123",
            "namespace": "/users/agent-X_user-Y/facts/",
        }
    }
    mock_agentcore_data.delete_memory_record.return_value = {"memoryRecordId": "mem-123"}
    _delete_record_impl(
        workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
        record_id="mem-123")
    mock_agentcore_data.delete_memory_record.assert_called_once_with(
        memoryId="mem-abc", memoryRecordId="mem-123")


def test_delete_record_cross_user_denied(mock_agentcore_data, mock_ws_table):
    from crud.memories import _delete_record_impl, MemoryForbidden
    mock_agentcore_data.get_memory_record.return_value = {
        "memoryRecord": {
            "memoryRecordId": "mem-OTHER",
            "namespace": "/users/agent-X_user-OTHER/facts/",
        }
    }
    with pytest.raises(MemoryForbidden):
        _delete_record_impl(
            workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
            record_id="mem-OTHER")
    mock_agentcore_data.delete_memory_record.assert_not_called()


def test_delete_record_cross_agent_denied(mock_agentcore_data, mock_ws_table):
    from crud.memories import _delete_record_impl, MemoryForbidden
    mock_agentcore_data.get_memory_record.return_value = {
        "memoryRecord": {
            "memoryRecordId": "mem-1",
            "namespace": "/users/agent-OTHER_user-Y/facts/",
        }
    }
    with pytest.raises(MemoryForbidden):
        _delete_record_impl(
            workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
            record_id="mem-1")


def test_delete_record_unparseable_namespace(mock_agentcore_data, mock_ws_table):
    from crud.memories import _delete_record_impl
    mock_agentcore_data.get_memory_record.return_value = {
        "memoryRecord": {
            "memoryRecordId": "mem-bad",
            "namespace": "/weird/path/",
        }
    }
    with pytest.raises(ValueError, match="unparseable"):
        _delete_record_impl(
            workspace_id="ws1", agent_id="agent-X", caller_id="user-Y",
            record_id="mem-bad")


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

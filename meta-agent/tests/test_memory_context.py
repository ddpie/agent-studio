"""Unit tests for MemoryContext (templates/_memory_context_src.py)."""

import asyncio
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# If strands not installed, mock the tool decorator
try:
    from strands import tool  # noqa: F401
except ImportError:
    mock_strands = MagicMock()
    mock_strands.tool = lambda f: f
    sys.modules["strands"] = mock_strands


@pytest.fixture
def mock_client():
    with patch("templates._memory_context_src._get_memory_client") as g:
        m = MagicMock()
        g.return_value = m
        yield m


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_list_preferences_correct_namespace(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.list_memory_records.return_value = {
        "memoryRecordSummaries": [
            {"memoryRecordId": "r1", "content": {"text": "prefers concise"}, "createdAt": 1}
        ]
    }
    ctx = MemoryContext("mem-X", "A_U", "s1", ["userPreference"])
    result = _run(ctx.list_preferences())
    call = mock_client.list_memory_records.call_args
    assert call.kwargs["memoryId"] == "mem-X"
    assert call.kwargs["namespace"] == "/users/A_U/preferences/"
    assert len(result) == 1


def test_retrieve_summaries_correct_namespace(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [{"memoryRecordId": "r1", "content": {"text": "debugged pipeline"}}]
    }
    ctx = MemoryContext("mem-X", "A_U", "s1", ["summary"])
    _run(ctx.retrieve_summaries("what did we discuss"))
    call = mock_client.retrieve_memory_records.call_args
    assert call.kwargs["namespace"] == "/users/A_U/summaries/"
    assert call.kwargs["searchCriteria"]["searchQuery"] == "what did we discuss"
    assert call.kwargs["searchCriteria"]["topK"] == 3


def test_record_user_turn_writes_event(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.create_event.return_value = {"event": {"eventId": "e1"}}
    ctx = MemoryContext("mem-X", "A_U", "s1", ["userPreference"])
    _run(ctx.record_user_turn("hello agent"))
    call = mock_client.create_event.call_args
    assert call.kwargs["memoryId"] == "mem-X"
    assert call.kwargs["actorId"] == "A_U"
    assert call.kwargs["sessionId"] == "s1"
    assert call.kwargs["payload"][0]["conversational"]["role"] == "USER"
    assert call.kwargs["payload"][0]["conversational"]["content"]["text"] == "hello agent"


def test_record_assistant_turn_writes_event(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.create_event.return_value = {"event": {"eventId": "e2"}}
    ctx = MemoryContext("mem-X", "A_U", "s1", [])
    _run(ctx.record_assistant_turn("here is the answer"))
    call = mock_client.create_event.call_args
    assert call.kwargs["payload"][0]["conversational"]["role"] == "ASSISTANT"


def test_list_preferences_swallows_exceptions(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.list_memory_records.side_effect = Exception("down")
    ctx = MemoryContext("mem-X", "A_U", "s1", ["userPreference"])
    result = _run(ctx.list_preferences())
    assert result == []


def test_retrieve_summaries_swallows_exceptions(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.retrieve_memory_records.side_effect = Exception("timeout")
    ctx = MemoryContext("mem-X", "A_U", "s1", ["summary"])
    result = _run(ctx.retrieve_summaries("query"))
    assert result == []


def test_record_user_turn_swallows_exceptions(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.create_event.side_effect = Exception("throttled")
    ctx = MemoryContext("mem-X", "A_U", "s1", [])
    _run(ctx.record_user_turn("text"))  # should not raise


def test_recall_facts_tool_hits_facts_namespace(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [{"content": {"text": "db is atlas"}}]
    }
    ctx = MemoryContext("mem-X", "A_U", "s1", ["semantic"])
    tool = ctx.make_recall_facts_tool()
    out = tool("what database")
    parsed = json.loads(out)
    assert parsed == [{"text": "db is atlas"}]
    call = mock_client.retrieve_memory_records.call_args
    assert call.kwargs["namespace"] == "/users/A_U/facts/"
    assert call.kwargs["searchCriteria"]["topK"] == 5


def test_recall_episodes_tool_hits_episodes_namespace(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.retrieve_memory_records.return_value = {
        "memoryRecordSummaries": [{"content": {"text": "past scenario"}}]
    }
    ctx = MemoryContext("mem-X", "A_U", "s1", ["episodic"])
    tool = ctx.make_recall_episodes_tool()
    out = tool("past tasks")
    parsed = json.loads(out)
    assert parsed == [{"text": "past scenario"}]
    call = mock_client.retrieve_memory_records.call_args
    assert call.kwargs["namespace"] == "/users/A_U/episodes/"
    assert call.kwargs["searchCriteria"]["topK"] == 3


def test_recall_facts_tool_swallows_exceptions(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.retrieve_memory_records.side_effect = Exception("err")
    ctx = MemoryContext("mem-X", "A_U", "s1", ["semantic"])
    tool = ctx.make_recall_facts_tool()
    out = tool("anything")
    assert out == "[]"


def test_recall_episodes_tool_swallows_exceptions(mock_client):
    from templates._memory_context_src import MemoryContext

    mock_client.retrieve_memory_records.side_effect = Exception("err")
    ctx = MemoryContext("mem-X", "A_U", "s1", ["episodic"])
    tool = ctx.make_recall_episodes_tool()
    out = tool("anything")
    assert out == "[]"

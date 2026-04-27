"""Tests for crud.agents module."""
import pytest

from crud.agents import _build_agent_item, _agent_response


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

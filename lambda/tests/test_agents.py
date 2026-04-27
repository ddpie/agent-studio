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

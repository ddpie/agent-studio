"""Tests for crud.agents module."""
import pytest


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

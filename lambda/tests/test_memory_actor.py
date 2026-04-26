"""Tests for the memory actor-id helper."""
import pytest

from shared.memory_actor import build_actor_id, parse_actor_id


def test_build_actor_id_joins_agent_and_user():
    assert build_actor_id("agent-abc", "user-xyz") == "agent-abc_user-xyz"


def test_build_actor_id_rejects_empty_agent():
    with pytest.raises(ValueError):
        build_actor_id("", "user-xyz")


def test_build_actor_id_rejects_empty_user():
    with pytest.raises(ValueError):
        build_actor_id("agent-abc", "")


def test_parse_actor_id_extracts_user_when_agent_matches():
    user = parse_actor_id("agent-abc_user-xyz", expected_agent_id="agent-abc")
    assert user == "user-xyz"


def test_parse_actor_id_rejects_wrong_agent():
    with pytest.raises(ValueError):
        parse_actor_id("agent-abc_user-xyz", expected_agent_id="agent-other")


def test_parse_actor_id_handles_user_with_underscore():
    # Cognito sub is a UUID with hyphens, but don't assume.
    # The helper must split on the FIRST underscore.
    user = parse_actor_id("agent-abc_user_with_under", expected_agent_id="agent-abc")
    assert user == "user_with_under"


def test_parse_actor_id_rejects_empty_expected_agent():
    with pytest.raises(ValueError):
        parse_actor_id("foo_bar", expected_agent_id="")

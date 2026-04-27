"""Tests for the shared Memory strategies config."""
from shared.memory_strategies import DEFAULT_MEMORY_STRATEGIES, STRATEGY_NAMES


def test_has_three_strategies():
    assert len(DEFAULT_MEMORY_STRATEGIES) == 3


def test_strategy_names_match_spec():
    assert sorted(STRATEGY_NAMES) == sorted([
        "userPreference", "semantic", "summary"
    ])


def test_user_preference_namespace_uses_actor_only():
    # Must NOT include {sessionId} — preferences are session-agnostic.
    entry = next(e for e in DEFAULT_MEMORY_STRATEGIES if "userPreferenceMemoryStrategy" in e)
    templates = entry["userPreferenceMemoryStrategy"]["namespaceTemplates"]
    assert templates == ["/users/{actorId}/preferences/"]


def test_semantic_namespace_uses_actor_only():
    entry = next(e for e in DEFAULT_MEMORY_STRATEGIES if "semanticMemoryStrategy" in e)
    templates = entry["semanticMemoryStrategy"]["namespaceTemplates"]
    assert templates == ["/users/{actorId}/facts/"]


def test_summary_namespace_uses_actor_and_session():
    entry = next(e for e in DEFAULT_MEMORY_STRATEGIES if "summaryMemoryStrategy" in e)
    templates = entry["summaryMemoryStrategy"]["namespaceTemplates"]
    assert templates == ["/users/{actorId}/summaries/{sessionId}/"]


def test_namespace_prefix_keys_match_strategy_names():
    """Drift guard: the prefix map must have exactly one entry per strategy."""
    from shared.memory_strategies import STRATEGY_NAMES, STRATEGY_NAMESPACE_PREFIX
    assert set(STRATEGY_NAMESPACE_PREFIX.keys()) == set(STRATEGY_NAMES)


def test_namespace_prefix_drops_session_placeholder():
    """Session-scoped strategies (summary) truncate {sessionId} so
    list_memory_records with this prefix enumerates records across all sessions
    for the given actor."""
    from shared.memory_strategies import STRATEGY_NAMESPACE_PREFIX
    for prefix in STRATEGY_NAMESPACE_PREFIX.values():
        assert "{sessionId}" not in prefix
        assert "{session_id}" not in prefix


def test_namespace_prefix_uses_python_actor_placeholder():
    """Prefixes use Python `.replace`-style {actor_id} (snake_case), not
    AgentCore's server-side {actorId} (camelCase). This is intentional — see
    module docstring."""
    from shared.memory_strategies import STRATEGY_NAMESPACE_PREFIX
    for prefix in STRATEGY_NAMESPACE_PREFIX.values():
        assert "{actor_id}" in prefix
        assert "{actorId}" not in prefix

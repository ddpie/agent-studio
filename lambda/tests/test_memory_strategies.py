"""Tests for the shared Memory strategies config."""
from shared.memory_strategies import DEFAULT_MEMORY_STRATEGIES, STRATEGY_NAMES


def test_has_four_strategies():
    assert len(DEFAULT_MEMORY_STRATEGIES) == 4


def test_strategy_names_match_spec():
    assert sorted(STRATEGY_NAMES) == sorted([
        "userPreference", "semantic", "summary", "episodic"
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


def test_episodic_namespace_uses_actor_and_session():
    entry = next(e for e in DEFAULT_MEMORY_STRATEGIES if "episodicMemoryStrategy" in e)
    templates = entry["episodicMemoryStrategy"]["namespaceTemplates"]
    assert templates == ["/users/{actorId}/episodes/{sessionId}/"]

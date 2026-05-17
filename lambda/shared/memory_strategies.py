"""Shared AgentCore Memory strategy configuration.

All workspaces use the same 4-strategy config. Keep in lockstep with
the spec at .claude/specs/2026-04-26-agentcore-memory-design.md §3.3.
"""

DEFAULT_MEMORY_STRATEGIES = [
    {"userPreferenceMemoryStrategy": {
        "name": "UserPreferences",
        "namespaceTemplates": ["/users/{actorId}/preferences/"],
    }},
    {"semanticMemoryStrategy": {
        "name": "Semantic",
        "namespaceTemplates": ["/users/{actorId}/facts/"],
    }},
    {"summaryMemoryStrategy": {
        "name": "Summary",
        "namespaceTemplates": ["/users/{actorId}/summaries/{sessionId}/"],
    }},
]

STRATEGY_NAMES = ["userPreference", "semantic", "summary"]

STRATEGY_NAMESPACE_PREFIX = {
    "userPreference": "/users/{actor_id}/preferences/",
    "semantic":       "/users/{actor_id}/facts/",
    "summary":        "/users/{actor_id}/summaries/",
    "episodic":       "/users/{actor_id}/episodes/",
}

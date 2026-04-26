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
    {"episodicMemoryStrategy": {
        "name": "Episodic",
        "namespaceTemplates": ["/users/{actorId}/episodes/{sessionId}/"],
    }},
]

# Short-name keys the rest of the codebase uses (Builder UI, agent config, etc.)
STRATEGY_NAMES = ["userPreference", "semantic", "summary", "episodic"]

# Short-name → namespace-path-prefix (session-scoped strategies get the `{actor_id}` portion
# filled but leave `{sessionId}` as-is for list-records queries that enumerate all sessions).
STRATEGY_NAMESPACE_PREFIX = {
    "userPreference": "/users/{actor_id}/preferences/",
    "semantic":       "/users/{actor_id}/facts/",
    "summary":        "/users/{actor_id}/summaries/",
    "episodic":       "/users/{actor_id}/episodes/",
}

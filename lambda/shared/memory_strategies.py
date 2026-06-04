"""Shared AgentCore Memory strategy configuration.

The spec at .claude/specs/2026-04-26-agentcore-memory-design.md §3.3 calls
for 4 strategies (userPreference, semantic, summary, episodic). The
episodic strategy is read-only on the platform side: agents emit episodic
events to AgentCore which materialises them into records; we list and
return them via /my-memories without provisioning a custom write
strategy. So DEFAULT_MEMORY_STRATEGIES has 3 entries (the writable ones)
while STRATEGY_NAMES + STRATEGY_NAMESPACE_PREFIX include all 4.
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

# All strategy names visible to the read path (/my-memories). Includes
# episodic so the namespace-prefix drift guard passes.
STRATEGY_NAMES = ["userPreference", "semantic", "summary", "episodic"]

STRATEGY_NAMESPACE_PREFIX = {
    "userPreference": "/users/{actor_id}/preferences/",
    "semantic":       "/users/{actor_id}/facts/",
    "summary":        "/users/{actor_id}/summaries/",
    "episodic":       "/users/{actor_id}/episodes/",
}

"""Compose and parse the actor_id used for AgentCore Memory scoping.

Per the spec, actor_id is `{agentId}_{callerId}`. This is the single
source of truth for that convention — `memories.py` (server-side
ownership checks) and the Agent runtime (writes) must both import from
here so they stay in lockstep.
"""


def build_actor_id(agent_id: str, caller_id: str) -> str:
    if not agent_id:
        raise ValueError("agent_id required to build actor_id")
    if not caller_id:
        raise ValueError("caller_id required to build actor_id")
    return f"{agent_id}_{caller_id}"


def parse_actor_id(actor_id: str, *, expected_agent_id: str) -> str:
    """Return the caller_id portion, validating the agent_id prefix.

    Splits on the FIRST underscore: agent ids in this project are
    alphanumeric only (CLAUDE.md: "alphanumeric only, max 36 chars"),
    but caller_ids are Cognito subs which may legally contain any
    pattern. Being conservative.
    """
    if not expected_agent_id:
        raise ValueError("expected_agent_id required to parse actor_id")
    if not actor_id.startswith(expected_agent_id + "_"):
        raise ValueError(
            f"actor_id prefix does not match expected agent: "
            f"actor={actor_id!r} expected_agent={expected_agent_id!r}"
        )
    return actor_id[len(expected_agent_id) + 1 :]

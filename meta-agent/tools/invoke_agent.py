"""invoke_agent — Invoke a deployed agent for testing."""

import json

from strands import tool

from deploy import invoke_runtime
from tools._scope import ensure_agent_in_workspace, ROLE_EDITOR


@tool
def invoke_agent(agent_id: str, prompt: str) -> str:
    """Invoke a deployed agent with a prompt. Used for testing.

    Args:
        agent_id: The ID of the agent to invoke.
        prompt: The message to send to the agent.

    Returns:
        The agent's text response.
    """
    _record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)
    return invoke_runtime(agent_id, prompt)

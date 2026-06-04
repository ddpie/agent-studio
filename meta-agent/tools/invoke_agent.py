"""invoke_agent — Invoke a deployed agent for testing."""

import json

from deploy import invoke_runtime
from strands import tool

from tools._scope import ROLE_EDITOR, ensure_agent_in_workspace


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

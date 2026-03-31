"""invoke_agent — Invoke a deployed agent for testing."""

import json

from strands import tool

from deploy import invoke_runtime


@tool
def invoke_agent(agent_id: str, prompt: str) -> str:
    """Invoke a deployed agent with a prompt. Used for testing.

    Args:
        agent_id: The ID of the agent to invoke.
        prompt: The message to send to the agent.

    Returns:
        The agent's text response.
    """
    return invoke_runtime(agent_id, prompt)

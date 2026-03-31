"""analyze_trace — Analyze agent execution trace and suggest distilling a Skill."""

import json

from strands import tool


@tool
def analyze_trace(
    agent_name: str,
    task_description: str,
    tools_used: str,
    steps_taken: str,
) -> str:
    """Analyze an agent's execution trace and suggest distilling it into a reusable Skill.

    This is called after an agent completes a task. It analyzes what was done
    and proposes a Skill that captures the pattern for future reuse.

    Args:
        agent_name: Name of the agent that executed the task.
        task_description: What the user originally asked the agent to do.
        tools_used: Comma-separated list of tools the agent used.
        steps_taken: Description of the steps the agent took.

    Returns:
        JSON with suggested skill_name, description, type, and instructions.
    """
    suggestion = {
        "suggested_skill": {
            "name": f"{agent_name}-workflow",
            "description": f"Automated workflow: {task_description}",
            "type": "prompt",
            "instructions": steps_taken,
            "tools_required": tools_used,
        },
        "recommendation": (
            "This execution pattern can be distilled into a reusable Skill. "
            "If confirmed, I will create a SKILL.md file with these instructions "
            "that other agents can use."
        ),
        "action_needed": "User confirmation to save as Skill",
    }

    return json.dumps(suggestion, indent=2)

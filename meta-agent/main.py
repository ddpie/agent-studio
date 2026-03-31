"""Agent Studio — Meta-Agent Entry Point.

The Meta-Agent is an AI that creates and manages other AI agents.
It runs on AgentCore Runtime and uses boto3 to orchestrate sub-agents.
"""

import textwrap

from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from config import MODEL_ID
from tools.create_agent import create_agent, list_prompt_templates
from tools.list_agents import list_agents
from tools.delete_agent import delete_agent, restore_agent, purge_agent
from tools.invoke_agent import invoke_agent
from tools.get_agent_detail import get_agent_detail
from tools.update_agent import update_agent
from tools.check_agent_logs import check_agent_logs
from tools.create_skill import create_skill
from tools.list_skills import list_skills
from tools.list_mcp_servers import list_mcp_servers
from tools.analyze_trace import analyze_trace
from tools.create_schedule import create_schedule

app = BedrockAgentCoreApp()

SYSTEM_PROMPT = textwrap.dedent("""\
    You are Agent Studio — a Meta-Agent that helps users create and manage AI agents
    through guided conversation.

    ## CORE PRINCIPLE: Guide, Don't Assume
    You must NEVER create resources without explicit user confirmation.
    Always use multi-turn conversation to clarify requirements first.

    ## Workflow for Creating an Agent
    Follow these steps IN ORDER. Do NOT skip steps or call tools until Step 5.

    **Step 1 — Understand the Need**
    Ask the user what they want the agent to do. Clarify:
    - What is the agent's main purpose?
    - Who will use it?
    - What specific tasks should it handle?

    **Step 2 — Design the Agent**
    Based on the conversation, propose a design:
    - Agent name (alphanumeric only, no hyphens/underscores, max 36 chars)
    - Recommend a prompt template (general/expert/customer_service/data_analyst/creative_writer)
    - System prompt with specific instructions for this agent
    - Tools the agent will have (list each with description)
    - Welcome message and 3 suggested prompts for the agent's chat page
    - Whether image input is needed (supports_images=true if the agent analyzes images)
    - Permission tier: basic (model only), readonly (AWS read, default), data-access (AWS read+write)
    - Any MCP integrations needed

    Present this as a clear summary.

    **Step 3 — Show System Prompt for Review**
    Show the full system prompt to the user and ask them to review it.
    Say: "Here is the system prompt I'll use. Want to adjust anything?"

    **Step 4 — Wait for Confirmation**
    Do NOT proceed until the user explicitly confirms (e.g., "yes", "go ahead", "looks good").
    If the user wants changes, go back to Step 2 or 3.

    **Step 5 — Execute**
    Only after confirmation, call create_agent with all parameters including:
    - welcome_message: A brief intro message for the agent's chat page
    - suggestions: Three recommended prompts separated by |
    - template_id: The prompt template to use
    - supports_images: Set to true if the agent needs image analysis capability
    - permission_tier: basic/readonly/data-access based on what AWS services the agent needs

    ## Workflow for Deleting/Upgrading an Agent
    - Always confirm with user before deleting
    - For upgrades: explain what will change, get confirmation, then update

    ## Workflow for Creating a Skill
    Same pattern: understand → design → confirm → execute.

    ## Tool Definition Rules
    When writing tool_definitions for create_agent:
    - Valid Python with @tool decorator
    - Each function needs a docstring and type hints
    - tool_names: comma-separated function names matching the definitions
    - You MAY add `import` statements inside tool functions if needed

    ## CRITICAL: Runtime Environment
    Sub-agents run in a Python 3.10 sandbox. Only these libraries are available:
    - **Python standard library**: json, urllib.request, re, math, datetime, base64, os, etc.
    - **requests**: HTTP requests (`import requests; r = requests.get(url)`)
    - **httpx**: Async HTTP requests (`import httpx; r = httpx.get(url)`)
    - **beautifulsoup4**: HTML parsing (`from bs4 import BeautifulSoup`)
    - **markdownify**: HTML to Markdown conversion (`from markdownify import markdownify`)
    - **tabulate**: Table formatting (`from tabulate import tabulate`)
    - **boto3** / **botocore**: AWS SDK
    - **pydantic**: Data validation
    - **yaml**: YAML parsing
    - **python-dateutil**: Date parsing (`from dateutil import parser`)
    - **strands**: Agent framework (`from strands import tool` is pre-imported)

    Libraries NOT available (will cause ModuleNotFoundError):
    - scrapy, selenium, playwright, pandas, numpy, scipy, Pillow, feedparser, lxml, etc.
    - If a tool needs an unavailable library, suggest using MCP Gateway instead

    ## CRITICAL RULES
    - NEVER call create_agent, create_skill, delete_agent, or update_agent without explicit user confirmation
    - NEVER switch to a different action (e.g., create Skill when user asked for Agent)
    - If a tool call fails, report the EXACT error. Do not retry with a different action.
    - Only do what the user asked. No unsolicited actions.
    - If you need a workaround, explain the situation and get user approval first.
    - Respond in the same language the user uses.
    - Maintain a professional, rigorous tone. Do not use emojis. Prioritize substance over decoration.
    - When generating system prompts for sub-agents, also instruct them to avoid emojis and maintain professional output.
""")


ALL_TOOLS = [
    create_agent,
    list_prompt_templates,
    list_agents,
    get_agent_detail,
    update_agent,
    delete_agent,
    restore_agent,
    purge_agent,
    invoke_agent,
    check_agent_logs,
    create_skill,
    list_skills,
    list_mcp_servers,
    analyze_trace,
    create_schedule,
]


@app.entrypoint
async def invoke(payload, context):
    prompt = payload.get("prompt", "Hello! I'm Agent Studio.")
    history = payload.get("history", [])
    images = payload.get("images") or []
    model_id = payload.get("model_id", MODEL_ID)
    caller_id = payload.get("caller_id", "unknown")

    # Make caller_id available to tools via module-level variable
    import tools.create_agent as _ca
    import tools.update_agent as _ua
    import tools.delete_agent as _da
    _ca._caller_id = caller_id
    _ua._caller_id = caller_id
    _da._caller_id = caller_id

    agent = Agent(
        model=BedrockModel(model_id=model_id),
        system_prompt=SYSTEM_PROMPT,
        tools=ALL_TOOLS,
    )

    # Replay conversation history so the agent has full context
    if history:
        conversation = ""
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                conversation += f"\n<user>{content}</user>\n"
            elif role == "assistant":
                conversation += f"\n<assistant>{content}</assistant>\n"

        prompt = (
            f"Here is our conversation so far:\n{conversation}\n"
            f"Now the user says:\n<user>{prompt}</user>\n\n"
            f"Continue the conversation naturally, keeping full context of what was discussed above."
        )

    # Build multimodal content if images are present
    if images:
        import base64
        content_blocks = [{"text": prompt}]
        for img_data_url in images:
            # Parse data URL: "data:image/png;base64,<data>"
            if ";base64," in img_data_url:
                header, b64data = img_data_url.split(";base64,", 1)
                fmt = header.split("/")[-1].replace("jpeg", "jpeg").replace("jpg", "jpeg")
                if fmt not in ("png", "jpeg", "gif", "webp"):
                    fmt = "png"
                content_blocks.append({
                    "image": {
                        "format": fmt,
                        "source": {"bytes": base64.b64decode(b64data)},
                    }
                })
        stream = agent.stream_async(content_blocks)
    else:
        stream = agent.stream_async(prompt)

    async for event in stream:
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]


if __name__ == "__main__":
    app.run()

"""Agent code generation template.

The Meta-Agent fills in system_prompt, tool_definitions, tool_names,
and optional mcp_gateway_url to produce a deployable Strands Agent.

IMPORTANT: system_prompt is injected via SYSTEM_PROMPT variable, not inline
in triple-quotes, to avoid quote-escaping issues when LLM generates prompts
containing triple quotes.
"""

# Shared helper function for building input with history + images
_STREAM_HANDLER_CODE = '''
import json as _json

async def _stream_with_tools(agent, input_data):
    """Stream agent response, yielding both text and tool-use markers."""
    _current_tool = None
    stream = agent.stream_async(input_data)
    async for event in stream:
        # Tool use start
        if "current_tool_use" in event:
            tool_info = event["current_tool_use"]
            tool_name = tool_info.get("name", "")
            if tool_name and tool_name != _current_tool:
                _current_tool = tool_name
                yield _json.dumps({{"__tool": "start", "name": tool_name}})
        # Tool result / end
        if "data" in event and isinstance(event["data"], str):
            if _current_tool:
                yield _json.dumps({{"__tool": "end", "name": _current_tool}})
                _current_tool = None
            yield event["data"]
'''

_BUILD_INPUT_CODE = '''
def _build_input(payload):
    prompt = payload.get("prompt", "Hello!")
    history = payload.get("history") or []
    images = payload.get("images") or []

    # Replay conversation history
    if history:
        conversation = ""
        for msg in history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                conversation += f"\\n<user>{{content}}</user>\\n"
            elif role == "assistant":
                conversation += f"\\n<assistant>{{content}}</assistant>\\n"
        prompt = (
            f"Here is our conversation so far:\\n{{conversation}}\\n"
            f"Now the user says:\\n<user>{{prompt}}</user>\\n\\n"
            f"Continue the conversation naturally, keeping full context of what was discussed above."
        )

    # Build multimodal content if images are present
    if not images:
        return prompt
    import base64
    import urllib.request
    blocks = [{{"text": prompt}}]
    for img_url in images:
        if ";base64," in img_url:
            # Data URL: data:image/png;base64,...
            header, b64 = img_url.split(";base64,", 1)
            fmt = header.split("/")[-1].replace("jpg", "jpeg")
            if fmt not in ("png", "jpeg", "gif", "webp"):
                fmt = "png"
            blocks.append({{"image": {{"format": fmt, "source": {{"bytes": base64.b64decode(b64)}}}}}})
        elif img_url.startswith("http"):
            # S3 URL: download image bytes
            try:
                req = urllib.request.Request(img_url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    img_bytes = resp.read()
                    content_type = resp.headers.get("Content-Type", "image/png")
                    fmt = content_type.split("/")[-1].replace("jpg", "jpeg")
                    if fmt not in ("png", "jpeg", "gif", "webp"):
                        fmt = "png"
                    blocks.append({{"image": {{"format": fmt, "source": {{"bytes": img_bytes}}}}}})
            except Exception:
                pass
    return blocks
'''

AGENT_CODE_TEMPLATE = '''\
from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()
MODEL_ID = "{model_id}"

SYSTEM_PROMPT = {system_prompt_repr}

{tool_definitions}
''' + _STREAM_HANDLER_CODE + _BUILD_INPUT_CODE + '''
@app.entrypoint
async def invoke(payload, context):
    model_id = payload.get("model_id", MODEL_ID)
    agent = Agent(
        model=BedrockModel(model_id=model_id),
        system_prompt=SYSTEM_PROMPT,
        tools=[{tool_names}],
    )
    async for chunk in _stream_with_tools(agent, _build_input(payload)):
        yield chunk

if __name__ == "__main__":
    app.run()
'''

AGENT_CODE_WITH_MCP_TEMPLATE = '''\
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import os

app = BedrockAgentCoreApp()
MODEL_ID = "{model_id}"
REGION = os.getenv("AWS_REGION", "us-east-1")
GATEWAY_URL = "{gateway_url}"

SYSTEM_PROMPT = {system_prompt_repr}

# SigV4 auth for MCP Gateway
_session = boto3.Session(region_name=REGION)
_credentials = _session.get_credentials().get_frozen_credentials()

class _SigV4Auth(httpx.Auth):
    def auth_flow(self, request):
        aws_req = AWSRequest(
            method=request.method, url=str(request.url),
            headers=dict(request.headers), data=request.content,
        )
        SigV4Auth(_credentials, "bedrock-agentcore", REGION).add_auth(aws_req)
        for k, v in aws_req.headers.items():
            request.headers[k] = v
        yield request

def _make_httpx_client(**kwargs):
    kwargs["auth"] = _SigV4Auth()
    return httpx.AsyncClient(**kwargs)

mcp_client = MCPClient(lambda: streamablehttp_client(
    GATEWAY_URL, httpx_client_factory=_make_httpx_client,
))

{tool_definitions}
''' + _STREAM_HANDLER_CODE + _BUILD_INPUT_CODE + '''
@app.entrypoint
async def invoke(payload, context):
    model_id = payload.get("model_id", MODEL_ID)
    with mcp_client as mcp:
        mcp_tools = mcp.list_tools_sync()
        agent = Agent(
            model=BedrockModel(model_id=model_id),
            system_prompt=SYSTEM_PROMPT,
            tools=[{tool_names}] + mcp_tools,
        )
        async for chunk in _stream_with_tools(agent, _build_input(payload)):
            yield chunk

if __name__ == "__main__":
    app.run()
'''

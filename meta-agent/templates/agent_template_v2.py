"""Agent code generation templates — multi-file structure.

Instead of assembling everything into a single main.py, we now generate:
- main.py: minimal entry point (reads prompt.txt, imports tools.py)
- tools.py: @tool decorated functions only
- prompt.txt: system prompt as plain text (zero escaping needed)
- config.json: model_id, tool_names, gateway_url
- stream_utils.py: shared streaming helpers (lives in base zip)
"""

# ── main.py template (no MCP) ──────────────────────────────────────────────
MAIN_PY_TEMPLATE = '''\
import json
from pathlib import Path
from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from stream_utils import _stream_with_tools, _build_input

app = BedrockAgentCoreApp()

_config = json.loads(Path("config.json").read_text())
MODEL_ID = _config["model_id"]
SYSTEM_PROMPT = Path("prompt.txt").read_text(encoding="utf-8")

# Import all @tool functions from tools.py
import tools as _tools_module
_ALL_TOOLS = []
for _name in _config.get("tool_names", []):
    if not hasattr(_tools_module, _name):
        import sys
        print(f"WARNING: Tool '{_name}' listed in config.json but not found in tools.py", file=sys.stderr)
        continue
    _ALL_TOOLS.append(getattr(_tools_module, _name))

@app.entrypoint
async def invoke(payload, context):
    model_id = payload.get("model_id", MODEL_ID)
    agent = Agent(
        model=BedrockModel(model_id=model_id),
        system_prompt=SYSTEM_PROMPT,
        tools=_ALL_TOOLS,
    )
    async for chunk in _stream_with_tools(agent, _build_input(payload)):
        yield chunk

if __name__ == "__main__":
    app.run()
'''

# ── main.py template (with MCP Gateway) ────────────────────────────────────
MAIN_PY_MCP_TEMPLATE = '''\
import json
from pathlib import Path
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from stream_utils import _stream_with_tools, _build_input
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import os

app = BedrockAgentCoreApp()

_config = json.loads(Path("config.json").read_text())
MODEL_ID = _config["model_id"]
SYSTEM_PROMPT = Path("prompt.txt").read_text(encoding="utf-8")
REGION = os.getenv("AWS_REGION", "us-east-1")
GATEWAY_URL = _config.get("gateway_url", "")

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

# Import all @tool functions from tools.py
import tools as _tools_module
_ALL_TOOLS = []
for _name in _config.get("tool_names", []):
    if not hasattr(_tools_module, _name):
        import sys
        print(f"WARNING: Tool '{_name}' listed in config.json but not found in tools.py", file=sys.stderr)
        continue
    _ALL_TOOLS.append(getattr(_tools_module, _name))

@app.entrypoint
async def invoke(payload, context):
    model_id = payload.get("model_id", MODEL_ID)
    with mcp_client as mcp:
        mcp_tools = mcp.list_tools_sync()
        agent = Agent(
            model=BedrockModel(model_id=model_id),
            system_prompt=SYSTEM_PROMPT,
            tools=_ALL_TOOLS + mcp_tools,
        )
        async for chunk in _stream_with_tools(agent, _build_input(payload)):
            yield chunk

if __name__ == "__main__":
    app.run()
'''

# ── stream_utils.py (shared, lives in base zip) ────────────────────────────
STREAM_UTILS_CODE = '''\
"""Shared streaming utilities for Agent Studio sub-agents."""

import json as _json
import base64 as _b64


async def _stream_with_tools(agent, input_data):
    """Stream agent response, yielding both text and tool-use markers."""
    _current_tool = None
    _tool_input_buf = ""
    _tool_use_id_map = {}
    stream = agent.stream_async(input_data)
    async for event in stream:
        # Tool use start
        if "current_tool_use" in event:
            tool_info = event["current_tool_use"]
            tool_name = tool_info.get("name", "")
            tool_use_id = tool_info.get("toolUseId", "")
            # Always register toolUseId (same tool can be called multiple times)
            if tool_use_id and tool_name:
                _tool_use_id_map[tool_use_id] = tool_name
            if tool_name and (tool_name != _current_tool or tool_use_id not in _tool_use_id_map or _tool_input_buf == ""):
                _current_tool = tool_name
                _tool_input_buf = ""
                yield _json.dumps({"__tool": "start", "name": tool_name})
            raw_input = tool_info.get("input", "")
            if raw_input:
                _tool_input_buf = raw_input
        # Tool result message
        if "message" in event:
            msg = event["message"]
            if isinstance(msg, dict) and msg.get("role") == "user":
                for block in msg.get("content", []):
                    tr = block.get("toolResult")
                    if not tr:
                        continue
                    t_id = tr.get("toolUseId", "")
                    t_name = _tool_use_id_map.get(t_id, "unknown")
                    output_parts = []
                    for c in tr.get("content", []):
                        if "text" in c:
                            output_parts.append(c["text"])
                    output_text = "\\n".join(output_parts)
                    inp_str = ""
                    try:
                        parsed_inp = _json.loads(_tool_input_buf) if isinstance(_tool_input_buf, str) and _tool_input_buf.strip() else _tool_input_buf
                        if isinstance(parsed_inp, dict) and parsed_inp:
                            inp_str = _json.dumps(parsed_inp, ensure_ascii=False, indent=2)
                    except Exception:
                        inp_str = str(_tool_input_buf) if _tool_input_buf else ""
                    inp_b64 = _b64.b64encode(inp_str.encode()).decode() if inp_str else ""
                    # SVG/HTML output must not be truncated (breaks rendering)
                    if output_text.lstrip().startswith("<"):
                        max_out = 50000
                    else:
                        max_out = 5000
                    if len(output_text) > max_out:
                        output_text = output_text[:max_out] + "\\n... (truncated)"
                    out_b64 = _b64.b64encode(output_text.encode()).decode() if output_text else ""
                    yield _json.dumps({"__tool": "result", "name": t_name, "input": inp_b64, "output": out_b64})
        # Text data
        if "data" in event and isinstance(event["data"], str):
            if _current_tool:
                yield _json.dumps({"__tool": "end", "name": _current_tool})
                _current_tool = None
                _tool_input_buf = ""
            yield event["data"]


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
                conversation += f"\\n<user>{content}</user>\\n"
            elif role == "assistant":
                conversation += f"\\n<assistant>{content}</assistant>\\n"
        prompt = (
            f"Here is our conversation so far:\\n{conversation}\\n"
            f"Now the user says:\\n<user>{prompt}</user>\\n\\n"
            f"Continue the conversation naturally, keeping full context of what was discussed above."
        )

    # Build multimodal content if images are present
    if not images:
        return prompt
    import base64
    import urllib.request
    blocks = [{"text": prompt}]
    for img_url in images:
        if ";base64," in img_url:
            header, b64 = img_url.split(";base64,", 1)
            fmt = header.split("/")[-1].replace("jpg", "jpeg")
            if fmt not in ("png", "jpeg", "gif", "webp"):
                fmt = "png"
            blocks.append({"image": {"format": fmt, "source": {"bytes": base64.b64decode(b64)}}})
        elif img_url.startswith("http"):
            try:
                req = urllib.request.Request(img_url)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    img_bytes = resp.read()
                    content_type = resp.headers.get("Content-Type", "image/png")
                    fmt = content_type.split("/")[-1].replace("jpg", "jpeg")
                    if fmt not in ("png", "jpeg", "gif", "webp"):
                        fmt = "png"
                    blocks.append({"image": {"format": fmt, "source": {"bytes": img_bytes}}})
            except Exception:
                pass
    return blocks
'''

# ── tools.py header ─────────────────────────────────────────────────────────
TOOLS_PY_HEADER = "from strands import tool\n\n"

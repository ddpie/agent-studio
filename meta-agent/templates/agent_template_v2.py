"""Agent code generation templates — multi-file structure.

Instead of assembling everything into a single main.py, we now generate:
- main.py: minimal entry point (reads prompt.txt, imports tools.py)
- tools.py: @tool decorated functions only
- prompt.txt: system prompt as plain text (zero escaping needed)
- config.json: model_id, tool_names, gateway_url
- stream_utils.py: shared streaming helpers (lives in base zip)
"""

# Secret hydration lives in builtin_tools.py (not main.py) so the
# forwarding helper (_secret_env_prefix_for_ci) is in the same module
# as run_command, which is the gate for every Code Interpreter call.
# main.py simply calls _builtin._hydrate_secrets_from_arns() at startup
# after its OTEL bootstrap.


# ── main.py template (no MCP) ──────────────────────────────────────────────
MAIN_PY_TEMPLATE = '''\
# OTEL bootstrap must run before strands / boto3 imports so that
# aws-opentelemetry-distro's auto-instrumentation can hook them.
import os as _os
if _os.environ.get("AGENT_OBSERVABILITY_ENABLED", "").lower() == "true":
    try:
        from opentelemetry.instrumentation.auto_instrumentation import initialize as _otel_init
        _otel_init()
    except Exception as _e:
        import sys as _sys
        print(f"OTEL auto-instrumentation disabled: {_e}", file=_sys.stderr)

# Hydrate secrets from Secrets Manager into os.environ BEFORE the
# agent starts. builtin_tools owns the logic so its CI-forwarding
# helper can see the same key list — otherwise run_command inside
# builtin_tools wouldn't know which env vars need to cross the
# sandbox boundary.
import builtin_tools as _builtin_bootstrap
_builtin_bootstrap._hydrate_secrets_from_arns()

import json
from pathlib import Path
from strands import Agent
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from stream_utils import _stream_with_tools, _build_input, _stream_and_record

app = BedrockAgentCoreApp()

_config = json.loads(Path("config.json").read_text())
MODEL_ID = _config["model_id"]
SYSTEM_PROMPT = Path("prompt.txt").read_text(encoding="utf-8")

def _get_max_tokens(mid):
    mid = mid.lower()
    for pat, lim in [("opus-4-7",128000),("opus-4-6",128000),("opus",128000),
                     ("sonnet-4-6",65536),("sonnet-4-5",16384),("sonnet-4",65536),("sonnet-3-5",8192),("sonnet",65536),
                     ("haiku-4-5",16384),("haiku",16384)]:
        if pat in mid:
            return lim
    return 16384

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
    import builtin_tools as _builtin
    _builtin._workspace_id = payload.get("workspace_id", "")
    skills_listing = _builtin.get_skills_listing()
    prompt = SYSTEM_PROMPT
    if skills_listing:
        prompt += (
            "\\n\\n## Available Skills\\n"
            + skills_listing
            + "\\n\\nSkills come in a few flavors — check_capabilities() tells you which is which (type: prompt | scripted | assets-only):"
            + "\\n- prompt skills (just a SKILL.md): use load_skill(name) and follow the instructions yourself."
            + "\\n- scripted skills (include .py/.sh): prefer run_skill_script(skill_name, script, args='...') — it stages the skill into the sandbox for you. Do NOT manually load_skill + run_command. Pass CLI flags via args= (shell-split). If unsure about flags, call with args='--help' first. Relative paths in args resolve against your current cwd by default; set cwd='skill' for scripts that read the skill's bundled assets."
            + "\\n- assets-only skills (SKILL.md + data files, no scripts): load_skill(name, file='path') to read individual files as needed."
            + "\\nBefore promising a file-generating task that depends on a specific skill, call check_capabilities() first. If a skill is missing or the wrong type, say so instead of trying and failing mid-turn."
        )
    prompt += "\\n\\n## File Sharing\\nFiles you generate via run_command / run_skill_script live inside the Code Interpreter sandbox, NOT on your own filesystem. /mnt/workspace/ is the agent's session storage — it does NOT exist inside the CI sandbox, so passing `--output /mnt/workspace/foo.pptx` to a script will fail with PermissionError. Save outputs to a relative path (e.g. `output.pptx`) or /tmp/ inside the sandbox, then call upload_to_s3(local_path) with the SAME path — it automatically reads from the sandbox when the file isn't local. Never tell the user you cannot send files. The download button appears automatically after upload — do NOT create markdown links like [filename](url) for downloads."
    prompt += "\\n\\n## File Reading\\nWhen the user attaches a PDF, Excel workbook (.xlsx/.xlsm), CSV, or TSV, call read_document(file_key=<s3 key>) to extract its text. The attachment marker in the user message includes the exact S3 key to pass. For generic text files (source code, logs, plain .txt), use read_file against a local path instead."
    agent = Agent(
        model=BedrockModel(model_id=model_id, max_tokens=_get_max_tokens(model_id)),
        system_prompt=prompt,
        tools=_ALL_TOOLS + [_builtin.load_skill, _builtin.run_command, _builtin.upload_to_s3, _builtin.read_document, _builtin.browser_use, _builtin.run_skill_script, _builtin.check_capabilities],
    )
    async for chunk in _stream_and_record(agent, payload):
        yield chunk

if __name__ == "__main__":
    app.run()
'''

# ── main.py template (with MCP) ───────────────────────────────────────────
MAIN_PY_MCP_TEMPLATE = '''\
# OTEL bootstrap must run before strands / boto3 imports so that
# aws-opentelemetry-distro's auto-instrumentation can hook them.
import os as _os
if _os.environ.get("AGENT_OBSERVABILITY_ENABLED", "").lower() == "true":
    try:
        from opentelemetry.instrumentation.auto_instrumentation import initialize as _otel_init
        _otel_init()
    except Exception as _e:
        import sys as _sys
        print(f"OTEL auto-instrumentation disabled: {_e}", file=_sys.stderr)

# Hydrate secrets from Secrets Manager into os.environ BEFORE the
# agent starts. builtin_tools owns the logic so its CI-forwarding
# helper can see the same key list — otherwise run_command inside
# builtin_tools wouldn't know which env vars need to cross the
# sandbox boundary.
import builtin_tools as _builtin_bootstrap
_builtin_bootstrap._hydrate_secrets_from_arns()

import json
import contextlib
import urllib.parse
from pathlib import Path
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from stream_utils import _stream_with_tools, _build_input, _stream_and_record
import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import os, sys

app = BedrockAgentCoreApp()

_config = json.loads(Path("config.json").read_text())
MODEL_ID = _config["model_id"]
SYSTEM_PROMPT = Path("prompt.txt").read_text(encoding="utf-8")
REGION = os.getenv("AWS_REGION", "us-east-1")

def _get_max_tokens(mid):
    mid = mid.lower()
    for pat, lim in [("opus-4-7",128000),("opus-4-6",128000),("opus",128000),
                     ("sonnet-4-6",65536),("sonnet-4-5",16384),("sonnet-4",65536),("sonnet-3-5",8192),("sonnet",65536),
                     ("haiku-4-5",16384),("haiku",16384)]:
        if pat in mid:
            return lim
    return 16384

_session = boto3.Session(region_name=REGION)

# --- SigV4 auth factories (per-service) ---
# Credentials are resolved per-request (not frozen at import) so AgentCore
# role-assumed credentials auto-refresh before the 1h expiry.
class _SigV4Auth(httpx.Auth):
    def __init__(self, service):
        self.service = service
    def auth_flow(self, request):
        headers = dict(request.headers)
        headers.pop("connection", None)
        aws_req = AWSRequest(
            method=request.method, url=str(request.url),
            headers=headers, data=request.content,
        )
        creds = _session.get_credentials().get_frozen_credentials()
        SigV4Auth(creds, self.service, REGION).add_auth(aws_req)
        request.headers.update(dict(aws_req.headers))
        yield request

_AUTH_MAP = {
    "runtime": _SigV4Auth("bedrock-agentcore"),
    "aws-mcp": _SigV4Auth("aws-mcp"),
    "none": None,
}

# --- Lazy resolve: runtime target_name → invoke URL at startup ---
def _resolve_runtime_url(target_name):
    """Resolve a runtime target name to its invoke URL.

    Handles naming inconsistency: catalog stores 'cloudwatch' but
    deploy-mcp.sh creates runtimes as 'mcp_cloudwatch'. Tries both.
    """
    base = target_name.replace("-", "_")
    # Try: exact, mcp_ prefixed, and stripped mcp_ prefix (covers both directions)
    candidates = {base}
    if not base.startswith("mcp_"):
        candidates.add(f"mcp_{base}")
    else:
        candidates.add(base[4:])  # strip mcp_ prefix
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    try:
        resp = control.list_agent_runtimes()
        runtimes = resp.get("agentRuntimes", [])
        while True:
            for rt in runtimes:
                if rt.get("agentRuntimeName") in candidates:
                    rt_info = control.get_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
                    arn = rt_info["agentRuntimeArn"]
                    encoded = urllib.parse.quote(arn, safe="")
                    return f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded}/invocations?qualifier=DEFAULT"
            if not resp.get("nextToken"):
                break
            resp = control.list_agent_runtimes(nextToken=resp["nextToken"])
            runtimes = resp.get("agentRuntimes", [])
    except Exception as e:
        print(f"WARNING: Failed to resolve runtime {target_name}: {e}", file=sys.stderr)
    return None

def _build_mcp_clients():
    """Build MCPClient list from config, lazy-resolving runtime URLs."""
    clients = []
    for ep in _config.get("mcp_endpoints", []):
        ep_type = ep.get("type", "runtime")
        auth = _AUTH_MAP.get(ep.get("auth", ep_type))

        if ep_type == "runtime":
            url = _resolve_runtime_url(ep["target_name"])
            if not url:
                print(f"WARNING: Skipping unresolvable MCP target: {ep['target_name']}", file=sys.stderr)
                continue
        else:
            url = ep["url"]

        clients.append(MCPClient(
            lambda u=url, a=auth: streamablehttp_client(u, auth=a, timeout=30),
        ))
    return clients

_mcp_clients = _build_mcp_clients()

# Import all @tool functions from tools.py
import tools as _tools_module
_ALL_TOOLS = []
for _name in _config.get("tool_names", []):
    if not hasattr(_tools_module, _name):
        print(f"WARNING: Tool \\'{_name}\\' listed in config.json but not found in tools.py", file=sys.stderr)
        continue
    _ALL_TOOLS.append(getattr(_tools_module, _name))

@app.entrypoint
async def invoke(payload, context):
    model_id = payload.get("model_id", MODEL_ID)
    import builtin_tools as _builtin
    _builtin._workspace_id = payload.get("workspace_id", "")
    skills_listing = _builtin.get_skills_listing()
    prompt = SYSTEM_PROMPT
    if skills_listing:
        prompt += (
            "\\n\\n## Available Skills\\n"
            + skills_listing
            + "\\n\\nSkills come in a few flavors — check_capabilities() tells you which is which (type: prompt | scripted | assets-only):"
            + "\\n- prompt skills (just a SKILL.md): use load_skill(name) and follow the instructions yourself."
            + "\\n- scripted skills (include .py/.sh): prefer run_skill_script(skill_name, script, args='...') — it stages the skill into the sandbox for you. Do NOT manually load_skill + run_command. Pass CLI flags via args= (shell-split). If unsure about flags, call with args='--help' first. Relative paths in args resolve against your current cwd by default; set cwd='skill' for scripts that read the skill's bundled assets."
            + "\\n- assets-only skills (SKILL.md + data files, no scripts): load_skill(name, file='path') to read individual files as needed."
            + "\\nBefore promising a file-generating task that depends on a specific skill, call check_capabilities() first. If a skill is missing or the wrong type, say so instead of trying and failing mid-turn."
        )
    prompt += "\\n\\n## File Sharing\\nFiles you generate via run_command / run_skill_script live inside the Code Interpreter sandbox, NOT on your own filesystem. /mnt/workspace/ is the agent's session storage — it does NOT exist inside the CI sandbox, so passing `--output /mnt/workspace/foo.pptx` to a script will fail with PermissionError. Save outputs to a relative path (e.g. `output.pptx`) or /tmp/ inside the sandbox, then call upload_to_s3(local_path) with the SAME path — it automatically reads from the sandbox when the file isn't local. Never tell the user you cannot send files. The download button appears automatically after upload — do NOT create markdown links like [filename](url) for downloads."
    prompt += "\\n\\n## File Reading\\nWhen the user attaches a PDF, Excel workbook (.xlsx/.xlsm), CSV, or TSV, call read_document(file_key=<s3 key>) to extract its text. The attachment marker in the user message includes the exact S3 key to pass. For generic text files (source code, logs, plain .txt), use read_file against a local path instead."
    with contextlib.ExitStack() as stack:
        mcp_tools = []
        for client in _mcp_clients:
            try:
                ctx = stack.enter_context(client)
                mcp_tools.extend(ctx.list_tools_sync())
            except Exception as _mcp_err:
                print(f"WARNING: MCP client failed to connect, skipping: {_mcp_err}", file=sys.stderr)
        agent = Agent(
            model=BedrockModel(model_id=model_id, max_tokens=_get_max_tokens(model_id)),
            system_prompt=prompt,
            tools=_ALL_TOOLS + mcp_tools + [_builtin.load_skill, _builtin.run_command, _builtin.upload_to_s3, _builtin.read_document, _builtin.browser_use, _builtin.run_skill_script, _builtin.check_capabilities],
        )
        async for chunk in _stream_and_record(agent, payload):
            yield chunk

if __name__ == "__main__":
    app.run()
'''

# ── stream_utils.py (shared, lives in base zip) ────────────────────────────
STREAM_UTILS_CODE = '''\
"""Shared streaming utilities for Agent Studio agents."""

import json as _json
import base64 as _b64


# ── Session tagging on spans ────────────────────────────────────────────
# AgentCore's managed OTEL pipeline injects `attributes.session.id` once
# per warm container and never refreshes it — so span queries filtered
# by the caller-supplied session_id miss every invocation after the
# first. We shadow that with our own attribute, updated per invocation
# via a module-level variable + a SpanProcessor that stamps on_start.
_CURRENT_SESSION_ID = ""
_SPAN_PROCESSOR_INSTALLED = False


def _install_session_span_processor():
    """Install a SpanProcessor that tags every new span with
    `agent_studio.session_id` read from the module-level variable.
    Idempotent — safe to call on every invocation."""
    import sys as _sys
    global _SPAN_PROCESSOR_INSTALLED
    if _SPAN_PROCESSOR_INSTALLED:
        return
    try:
        from opentelemetry import trace as _ot
        from opentelemetry.sdk.trace import SpanProcessor as _SP
    except Exception as e:
        print(f"AS_SPAN_TAG: import failed: {e}", file=_sys.stderr, flush=True)
        return

    class _SessionTagProcessor(_SP):
        def on_start(self, span, parent_context=None):
            sid = _CURRENT_SESSION_ID
            if sid:
                try:
                    span.set_attribute("agent_studio.session_id", sid)
                except Exception:
                    pass
        def on_end(self, span):
            pass
        def shutdown(self):
            pass
        def force_flush(self, timeout_millis=30000):
            return True

    provider = _ot.get_tracer_provider()
    provider_cls = type(provider).__name__
    add = getattr(provider, "add_span_processor", None)
    if add is None:
        print(f"AS_SPAN_TAG: provider {provider_cls} has no add_span_processor", file=_sys.stderr, flush=True)
        return
    try:
        add(_SessionTagProcessor())
        _SPAN_PROCESSOR_INSTALLED = True
        print(f"AS_SPAN_TAG: installed on {provider_cls}", file=_sys.stderr, flush=True)
    except Exception as e:
        print(f"AS_SPAN_TAG: add failed on {provider_cls}: {e}", file=_sys.stderr, flush=True)


def _set_current_session_id(session_id):
    """Update the module-level session id that the SpanProcessor reads
    when stamping new spans. Called at the start of every invocation."""
    global _CURRENT_SESSION_ID
    _CURRENT_SESSION_ID = session_id or ""


def _strip_images_from_messages(messages):
    """Replace image blocks in conversation messages with a text placeholder.

    Returns the number of images removed. Modifies messages in place.
    """
    removed = 0
    for msg in messages:
        new_content = []
        for block in msg.get("content", []):
            if "image" in block:
                new_content.append({"text": "[image removed — exceeded model dimension limit]"})
                removed += 1
            elif "toolResult" in block:
                tr = block["toolResult"]
                new_tr_content = []
                for c in tr.get("content", []):
                    if "image" in c:
                        new_tr_content.append({"text": "[image removed — exceeded model dimension limit]"})
                        removed += 1
                    else:
                        new_tr_content.append(c)
                tr["content"] = new_tr_content
                new_content.append(block)
            else:
                new_content.append(block)
        msg["content"] = new_content
    return removed


def _classify_error(exc):
    """Classify an exception into an auto-recoverable category or None."""
    msg = str(exc).lower()
    if "image" in msg and ("dimension" in msg or "size" in msg) and ("exceed" in msg or "too large" in msg):
        return "image_too_large"
    if "throttl" in msg or "too many request" in msg:
        return "throttled"
    return None


async def _stream_with_tools(agent, input_data, _retry_depth=0):
    """Stream agent response, yielding both text and tool-use markers.

    Auto-recoverable errors (image too large, throttling) are handled
    transparently: the problematic content is fixed and the request is
    retried once. Unrecoverable errors are surfaced as text to the user.
    Strands already handles ContextWindowOverflowException internally.
    """
    _current_tool = None
    _tool_input_buf = ""
    _tool_use_id_map = {}
    try:
        stream = agent.stream_async(input_data)
        async for event in stream:
            # Tool use start
            if "current_tool_use" in event:
                tool_info = event["current_tool_use"]
                tool_name = tool_info.get("name", "")
                tool_use_id = tool_info.get("toolUseId", "")
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
                        if output_text.lstrip().startswith("<"):
                            max_out = 50000
                        elif t_name == "load_skill":
                            max_out = 10000
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
    except Exception as _exc:
        import sys as _sys
        err_msg = str(_exc)
        print(f"STREAM_ERROR: {type(_exc).__name__}: {err_msg}", file=_sys.stderr)
        if _current_tool:
            yield _json.dumps({"__tool": "end", "name": _current_tool})

        category = _classify_error(_exc)

        # Auto-recover: strip oversized images and retry once
        if category == "image_too_large" and _retry_depth < 1:
            removed = _strip_images_from_messages(agent.messages)
            if removed:
                print(f"STREAM_RETRY: stripped {removed} image(s), retrying", file=_sys.stderr)
                async for chunk in _stream_with_tools(agent, input_data, _retry_depth=_retry_depth + 1):
                    yield chunk
                return

        # Auto-recover: throttled — wait and retry once
        if category == "throttled" and _retry_depth < 1:
            import asyncio as _aio
            print("STREAM_RETRY: throttled, waiting 5s", file=_sys.stderr)
            await _aio.sleep(5)
            async for chunk in _stream_with_tools(agent, input_data, _retry_depth=_retry_depth + 1):
                yield chunk
            return

        # Unrecoverable — surface to user
        if "EventLoopException" in type(_exc).__name__:
            cause = str(getattr(_exc, "__cause__", "")) or err_msg
            yield f"\\n\\n[Error: {cause}]"
        else:
            yield f"\\n\\n[Error: {err_msg}]"


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
                import socket, ipaddress
                from urllib.parse import urlparse as _urlparse
                _BLOCKED = [ipaddress.ip_network(n) for n in (
                    "0.0.0.0/8","10.0.0.0/8","100.64.0.0/10","127.0.0.0/8",
                    "169.254.0.0/16","172.16.0.0/12","192.0.0.0/24","192.168.0.0/16",
                    "198.18.0.0/15","224.0.0.0/4","240.0.0.0/4","255.255.255.255/32",
                    "::/128","::1/128","fc00::/7","fe80::/10","ff00::/8",
                )]
                def _ck(u):
                    p = _urlparse(u)
                    if p.scheme not in ("http","https"): raise ValueError(f"bad scheme: {p.scheme}")
                    if not p.hostname: raise ValueError("no hostname")
                    for _f,_t,_pr,_c,_sa in socket.getaddrinfo(p.hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
                        _ip = ipaddress.ip_address(_sa[0])
                        for net in _BLOCKED:
                            if _ip in net: raise ValueError(f"blocked IP: {_ip}")
                class _RH(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, rq, fp, code, msg, hdrs, newurl):
                        _ck(newurl)
                        return super().redirect_request(rq, fp, code, msg, hdrs, newurl)
                _ck(img_url)
                req = urllib.request.Request(img_url)
                with urllib.request.build_opener(_RH).open(req, timeout=10) as resp:
                    img_bytes = resp.read()
                    content_type = resp.headers.get("Content-Type", "image/png")
                    fmt = content_type.split("/")[-1].replace("jpg", "jpeg")
                    if fmt not in ("png", "jpeg", "gif", "webp"):
                        fmt = "png"
                    blocks.append({"image": {"format": fmt, "source": {"bytes": img_bytes}}})
            except Exception:
                pass
    return blocks


# ── Run recording (scheduled/manual only) ────────────────────────────
import os as _os
import re as _re
import time as _time

_RUNS_TABLE = _os.environ.get("AGENT_STUDIO_RUNS_TABLE", "")
_REGION = _os.environ.get("AWS_REGION", _os.environ.get("AGENT_STUDIO_REGION", "us-east-1"))
_S3_BUCKET = _os.environ.get("AGENT_STUDIO_S3_BUCKET", "")
_AGENT_ID = ""
# Parse agent id from OTEL_RESOURCE_ATTRIBUTES: "service.name=<id>,..."
for _kv in _os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
    if _kv.startswith("service.name="):
        _AGENT_ID = _kv.split("=", 1)[1]
        break

_ddb_resource = None
_s3_client = None

def _get_ddb():
    global _ddb_resource
    if _ddb_resource is None:
        import boto3
        _ddb_resource = boto3.resource("dynamodb", region_name=_REGION)
    return _ddb_resource

def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        _s3_client = boto3.client("s3", region_name=_REGION)
    return _s3_client

def _generate_ulid():
    """Simple ULID: 10-char timestamp (ms hex) + 10-char random."""
    import uuid as _uuid
    ts = format(int(_time.time() * 1000), "013x")
    rand = _uuid.uuid4().hex[:10]
    return f"{ts}-{rand}"

def _write_run_started(agent_id, run_id, session_id, payload):
    if not _RUNS_TABLE:
        return
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        ttl = int(_time.time()) + 365 * 86400
        trigger = "manual" if "-manual-" in session_id else "schedule"
        schedule_name = payload.get("__schedule_name") or None
        table = _get_ddb().Table(_RUNS_TABLE)
        table.put_item(Item={
            "agentId": agent_id,
            "runId": run_id,
            "workspaceId": payload.get("workspace_id", ""),
            "trigger": trigger,
            "scheduleId": schedule_name,
            "sessionId": session_id,
            "status": "running",
            "input": payload.get("prompt", ""),
            "startedAt": now,
            "ttl": ttl,
        })
    except Exception as e:
        import sys
        print(f"WARNING: failed to write run started: {e}", file=sys.stderr)

def _write_run_completed(agent_id, run_id, chunks, duration_ms=None, usage=None, model_id=None):
    if not _RUNS_TABLE:
        return
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Separate text and tool calls from chunks
        text_parts = []
        tool_calls = []
        for c in chunks:
            if c.startswith("{") and '"__tool"' in c:
                try:
                    parsed = _json.loads(c)
                    if parsed.get("__tool") == "result":
                        inp = ""
                        out = ""
                        try:
                            inp = _b64.b64decode(parsed.get("input", "")).decode("utf-8", errors="replace") if parsed.get("input") else ""
                        except Exception:
                            pass
                        try:
                            out = _b64.b64decode(parsed.get("output", "")).decode("utf-8", errors="replace") if parsed.get("output") else ""
                        except Exception:
                            pass
                        tool_calls.append({"name": parsed.get("name", ""), "input": inp[:2000], "output": out[:5000]})
                except Exception:
                    pass
            else:
                text_parts.append(c)

        full_text = "".join(text_parts)

        # Extract artifact refs from __S3_DOWNLOAD__ markers
        artifact_refs = []
        for m in _re.finditer(r"__S3_DOWNLOAD__:([^:\\s\\"\\}\\]]+):([^\\s\\"\\}\\]]+)", full_text):
            artifact_refs.append(m.group(1))

        # Write output.json to S3
        output_key = f"runs/{agent_id}/{run_id}/output.json"
        output_data = _json.dumps({"text": full_text, "toolCalls": tool_calls}, ensure_ascii=False)
        _get_s3().put_object(Bucket=_S3_BUCKET, Key=output_key, Body=output_data.encode("utf-8"), ContentType="application/json")

        # Update DDB
        table = _get_ddb().Table(_RUNS_TABLE)
        update_expr = "SET #st = :st, completedAt = :ca, outputRef = :oref, artifactRefs = :arefs"
        expr_values = {
            ":st": "completed",
            ":ca": now,
            ":oref": output_key,
            ":arefs": artifact_refs,
        }
        expr_names = {"#st": "status"}
        if duration_ms is not None:
            update_expr += ", durationMs = :dur"
            expr_values[":dur"] = duration_ms
        if usage:
            update_expr += ", promptTokens = :pt, completionTokens = :ct, totalTokens = :tt"
            expr_values[":pt"] = usage.get("promptTokens", 0)
            expr_values[":ct"] = usage.get("completionTokens", 0)
            expr_values[":tt"] = usage.get("totalTokens", 0)
        if model_id:
            update_expr += ", model = :mdl"
            expr_values[":mdl"] = model_id
        table.update_item(
            Key={"agentId": agent_id, "runId": run_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
        )
    except Exception as e:
        import sys
        print(f"WARNING: failed to write run completed: {e}", file=sys.stderr)

def _write_run_failed(agent_id, run_id, error):
    if not _RUNS_TABLE:
        return
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        table = _get_ddb().Table(_RUNS_TABLE)
        table.update_item(
            Key={"agentId": agent_id, "runId": run_id},
            UpdateExpression="SET #st = :st, completedAt = :ca, #err = :err",
            ExpressionAttributeNames={"#st": "status", "#err": "error"},
            ExpressionAttributeValues={
                ":st": "failed",
                ":ca": now,
                ":err": {"code": type(error).__name__, "message": str(error)[:500]},
            },
        )
    except Exception as e:
        import sys
        print(f"WARNING: failed to write run failed: {e}", file=sys.stderr)

async def _stream_and_record(agent, payload):
    """Wrap _stream_with_tools: for sched- sessions, record to DDB + S3."""
    session_id = payload.get("session_id", "")
    # Tag every span emitted during this invocation with the caller's
    # session_id so trace queries can find them. This replaces the
    # AgentCore-managed `attributes.session.id` which goes stale on
    # warm-container reuse (it sticks to the first session that started
    # the pod and silently misattributes every subsequent run).
    _install_session_span_processor()
    _set_current_session_id(session_id)
    if not session_id.startswith("sched-") or not _AGENT_ID:
        async for chunk in _stream_with_tools(agent, _build_input(payload)):
            yield chunk
        return

    run_id = _generate_ulid()
    _write_run_started(_AGENT_ID, run_id, session_id, payload)
    chunks = []
    start_ns = _time.time_ns()
    _completed = False
    _failed = False
    try:
        async for chunk in _stream_with_tools(agent, _build_input(payload)):
            chunks.append(chunk)
            yield chunk
        duration_ms = int((_time.time_ns() - start_ns) / 1_000_000)
        usage = None
        model_id = None
        try:
            m = agent.event_loop_metrics.accumulated_usage
            usage = {"promptTokens": m.get("inputTokens", 0), "completionTokens": m.get("outputTokens", 0), "totalTokens": m.get("totalTokens", 0)}
        except Exception:
            pass
        try:
            model_id = getattr(agent.model, "model_id", None) or (agent.model.config.get("model_id") if hasattr(agent.model, "config") else None)
            if not model_id:
                model_id = MODEL_ID
        except Exception:
            model_id = MODEL_ID
        _write_run_completed(_AGENT_ID, run_id, chunks, duration_ms=duration_ms, usage=usage, model_id=model_id)
        _completed = True
    except Exception as e:
        _write_run_failed(_AGENT_ID, run_id, e)
        _failed = True
        raise
    finally:
        if not _completed and not _failed:
            # Stream consumer disconnected before completion (browser closed,
            # network drop, Invoke Lambda timeout). The async generator is
            # being GC'd without an exception — write a partial record so the
            # run doesn't stay stuck as "running" forever.
            duration_ms = int((_time.time_ns() - start_ns) / 1_000_000)
            if chunks:
                _write_run_completed(_AGENT_ID, run_id, chunks, duration_ms=duration_ms)
            else:
                _write_run_failed(_AGENT_ID, run_id, RuntimeError("stream disconnected before any output"))
'''

# ── tools.py header ─────────────────────────────────────────────────────────
TOOLS_PY_HEADER = "from strands import tool\n\n"

# ── builtin_tools.py (injected into every agent zip) ──────────────────
BUILTIN_TOOLS_CODE = '''\
"""Built-in tools for Agent Studio agents — skill loading with local cache."""

import json as _json
import os as _os
from pathlib import Path as _Path
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor

import boto3 as _boto3
from strands import tool as _tool

_REGION = _os.getenv("AWS_REGION", "us-east-1")
_ACCOUNT_ID = _os.environ.get("AWS_ACCOUNT_ID", "")
if not _ACCOUNT_ID:
    try:
        _ACCOUNT_ID = _boto3.client("sts").get_caller_identity()["Account"]
    except Exception:
        pass
_S3_BUCKET = _os.getenv(
    "AGENT_STUDIO_S3_BUCKET",
    f"bedrock-agentcore-codebuild-sources-{_ACCOUNT_ID}-{_REGION}",
)
_s3 = _boto3.client("s3", region_name=_REGION)

# Per-invocation workspace id — main.py sets this before calling agent tools.
_workspace_id = ""

# Populated by _hydrate_secrets_from_arns() at startup: names of env
# vars that carry user-provided secrets and must be forwarded when we
# cross the Code Interpreter sandbox boundary. Skill scripts run inside
# the CI container — which has its own os.environ — so without this
# forwarding, os.environ.get("TOKEN") would always return empty there
# even though the agent itself has the value. The list is explicit
# (rather than sniffing for "TOKEN"/"KEY" name patterns) so new skills
# needing new secrets require zero template changes: user saves the
# secret in the UI, redeploys, cold-start registers the key, and every
# CI call auto-forwards it.
_SECRET_ENV_KEYS = []


def _hydrate_secrets_from_arns():
    """Fetch per-agent secrets from AWS Secrets Manager at cold start.

    Reads the comma-separated ARN list from AGENT_STUDIO_SECRET_ARNS
    (assembled at deploy time by deploy.py::_shared_env_vars), fetches
    each SecretString in parallel under the agent's own IAM role
    (scoped to agent-studio/*), and injects KEY=value pairs into
    os.environ. Records successfully-loaded keys in _SECRET_ENV_KEYS
    for later CI forwarding.

    Failures are non-fatal — a missing or expired secret shouldn't
    kill the agent at boot; tools that actually need the value will
    surface their own error when they read an empty env var.
    """
    arn_csv = _os.environ.get("AGENT_STUDIO_SECRET_ARNS", "")
    if not arn_csv:
        return 0
    arns = [a.strip() for a in arn_csv.split(",") if a.strip()]
    if not arns:
        return 0
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE
        from concurrent.futures import as_completed as _as_completed
    except ImportError:
        return 0
    import re as _re
    try:
        _sm = _boto3.client("secretsmanager", region_name=_REGION)
    except Exception as exc:
        import sys as _sys
        print(f"secret hydrate: secretsmanager client init failed: {exc}", file=_sys.stderr)
        return 0
    # Secrets Manager appends a 6-alphanumeric random suffix to ARNs
    # (agent-studio/ws/agent/FOO-aBc123). The CreateSecret validator
    # rejects hyphens in key names, so stripping the trailing
    # "-[A-Za-z0-9]{6}" recovers the original KEY.
    _SUFFIX_RE = _re.compile(r"-[A-Za-z0-9]{6}$")
    def _fetch(arn):
        last = arn.rsplit("/", 1)[-1]
        key_name = _SUFFIX_RE.sub("", last)
        try:
            resp = _sm.get_secret_value(SecretId=arn)
            return key_name, resp.get("SecretString", "")
        except Exception as exc:
            import sys as _sys
            print(f"secret hydrate failed for {arn}: {exc}", file=_sys.stderr)
            return key_name, None
    loaded = 0
    with _TPE(max_workers=min(8, len(arns))) as pool:
        for fut in _as_completed([pool.submit(_fetch, a) for a in arns]):
            k, v = fut.result()
            if k and v is not None and k not in _os.environ:
                _os.environ[k] = v
                if k not in _SECRET_ENV_KEYS:
                    _SECRET_ENV_KEYS.append(k)
                loaded += 1
    return loaded


def _secret_env_prefix_for_ci():
    """Build a Python snippet that reinjects hydrated secrets into
    os.environ inside the Code Interpreter sandbox.

    Prepended by run_command to every executeCode invocation. Idempotent:
    re-running the same session costs only the setdefault no-op, so we
    don't track per-session warm state. Uses repr() for quote-safety.
    setdefault (not assignment) means an already-set sandbox env value
    wins — we'd rather defer to the sandbox than trample a user who
    explicitly set an override.
    """
    if not _SECRET_ENV_KEYS:
        return ""
    lines = []
    for k in _SECRET_ENV_KEYS:
        v = _os.environ.get(k)
        if not isinstance(v, str) or not v:
            continue
        lines.append(f"_os.environ.setdefault({k!r}, {v!r})")
    if not lines:
        return ""
    return "import os as _os\\n" + "\\n".join(lines) + "\\n"


# The agent's own AgentCore runtime id — used as the S3 namespace for
# skill lookups (``agents/{_AGENT_ID}/skills/``). AgentCore doesn't inject
# a dedicated env var, but OTEL_RESOURCE_ATTRIBUTES carries it via the
# ``service.name`` attribute that deploy.py attaches for tracing.
# AGENT_STUDIO_AGENT_ID wins if set, then the OTEL attribute, else empty
# (skill lookups short-circuit with an explicit error).
_AGENT_ID = _os.environ.get("AGENT_STUDIO_AGENT_ID", "")
if not _AGENT_ID:
    for _kv in _os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        if _kv.startswith("service.name="):
            _AGENT_ID = _kv.split("=", 1)[1]
            break

# Local cache directory — uses managed session storage (persists across
# warm invocations) if available, else a per-process tmp dir.
_CACHE_ROOT = _Path("/mnt/workspace/skills") if _os.path.isdir("/mnt/workspace") else _Path("/tmp/skills_cache")
# Per-process record of which skills have been materialized into the cache
# this invocation. Skills are fetched on-demand (first call to load_skill
# or run_skill_script) rather than up front, so cold-start isn't penalized
# for skills the agent doesn't actually touch.
_CACHED_SKILLS: set = set()
# Cached copy of the agent's own attached-skills manifest. Derived from
# metadata.json under agents/{AGENT_ID}/, not the workspace-global skills
# index — agents only see skills explicitly attached to them.
_SKILLS_MANIFEST: list | None = None


def _agent_s3_prefix() -> str:
    """Per-agent S3 namespace for its skills.

    Agents don't read from the workspace-global ``skills/`` prefix —
    instead each skill they attach is copied by the CRUD Lambda into
    ``agents/{agent_id}/skills/{local_skill_id}/`` (with nested paths
    preserved). Returns an empty string if the agent id isn't known yet,
    which short-circuits all skill loading — caller should handle.
    """
    if not _AGENT_ID:
        return ""
    return f"agents/{_AGENT_ID}/skills/"


def _load_skills_manifest() -> list:
    """Read this agent's metadata.json to discover its attached skills.

    Cached in-process for the life of the warm container. Every manifest
    entry must have at least ``id`` (the local skill id used as the S3
    prefix) and ``name``. ``description`` is optional but shown in the
    prompt. Returns [] on any error — skills are a soft dependency.
    """
    global _SKILLS_MANIFEST
    if _SKILLS_MANIFEST is not None:
        return _SKILLS_MANIFEST
    if not _AGENT_ID:
        _SKILLS_MANIFEST = []
        return _SKILLS_MANIFEST
    try:
        obj = _s3.get_object(Bucket=_S3_BUCKET, Key=f"agents/{_AGENT_ID}/metadata.json")
        meta = _json.loads(obj["Body"].read().decode("utf-8"))
        skills = meta.get("skills") or []
        if not isinstance(skills, list):
            skills = []
        # Normalize: every entry must have id + name to be usable
        _SKILLS_MANIFEST = [s for s in skills if isinstance(s, dict) and s.get("id") and s.get("name")]
    except Exception:
        _SKILLS_MANIFEST = []
    return _SKILLS_MANIFEST


def _find_skill_by_name(name: str) -> dict | None:
    """Look up a skill in the agent's manifest by its name field."""
    for s in _load_skills_manifest():
        if s.get("name") == name:
            return s
    return None


def _download_skill_file(args):
    """Download a single S3 object to local cache. Used by ThreadPoolExecutor."""
    key, local_path = args
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _s3.download_file(_S3_BUCKET, key, str(local_path))
        return True
    except Exception:
        return False


def _ensure_skill_materialized(skill_id: str, skill_name: str | None = None) -> tuple[_Path | None, str | None]:
    """Download the skill's S3 tree to the local cache on first touch.

    Cheap after the first call for the same skill — only lists S3 and
    downloads missing files. Paths under the skill are preserved (nested
    dirs kept intact so scripts can ``open('assets/foo.svg')``).

    Returns (local_root, error_or_None). local_root is the per-skill
    directory in the local cache that a caller can chdir into.
    """
    prefix_root = _agent_s3_prefix()
    if not prefix_root:
        return None, "AGENT_STUDIO_AGENT_ID (OTEL_RESOURCE_ATTRIBUTES service.name) not set — cannot resolve skill location"
    if not skill_id:
        return None, "skill_id is required"

    local_root = _CACHE_ROOT / skill_id
    if skill_id in _CACHED_SKILLS:
        return local_root, None

    prefix = f"{prefix_root}{skill_id}/"
    download_tasks = []
    try:
        paginator = _s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=_S3_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(prefix):]
                if not rel:
                    continue
                local_path = local_root / rel
                if not local_path.exists():
                    download_tasks.append((key, local_path))
    except Exception as e:
        return None, f"list objects failed under {prefix}: {e}"

    if download_tasks:
        local_root.mkdir(parents=True, exist_ok=True)
        with _ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(_download_skill_file, download_tasks))
        if not all(results):
            return None, f"some files failed to download ({sum(1 for r in results if not r)}/{len(results)})"

    # Even with zero download_tasks, the skill is "cached" (empty skill is
    # legal — just SKILL.md) — as long as we reached here without error.
    _CACHED_SKILLS.add(skill_id)
    return local_root, None


def ensure_skills_cached():
    """Legacy alias kept for callers that expect it. No-op — skills are
    now loaded on demand by _ensure_skill_materialized. Retained so
    older skill-aware tools outside this module don't break.
    """
    return


def get_skills_listing() -> str:
    """Formatted listing of this agent's attached skills for prompt injection.

    Reads the per-agent manifest (metadata.json), not the workspace-global
    skill index — so agents see exactly the skills they were deployed
    with, no less and no more.
    """
    skills = _load_skills_manifest()
    if not skills:
        return ""
    lines = []
    for s in skills:
        desc = s.get("description") or ""
        lines.append(f"- {s['name']}: {desc}" if desc else f"- {s['name']}")
    return "\\n".join(lines)


@_tool
def load_skill(name: str, file: str = "") -> str:
    """Load a skill by name. Returns the full SKILL.md content, or a specific file.

    When called without `file`, returns SKILL.md and lists available files.
    When called with `file`, returns that file's content (e.g. "scripts/clean_csv.py").

    Args:
        name: The skill name (e.g. "data-analyzer").
        file: Optional path to a specific file within the skill (e.g. "scripts/demo.py").

    Returns:
        The skill content, or an error tool-result.
    """
    entry = _find_skill_by_name(name)
    if not entry:
        available = [s.get("name", "") for s in _load_skills_manifest()]
        return _tool_error(
            f"Skill '{name}' not attached to this agent. "
            f"Available: {', '.join(sorted(a for a in available if a)) or '(none)'}"
        )

    skill_id = entry["id"]
    local_root, err = _ensure_skill_materialized(skill_id, name)
    if err or local_root is None:
        return _tool_error(f"Failed to materialize skill '{name}': {err or 'unknown error'}")

    if file:
        target = local_root / file.lstrip("/")
        if not target.is_file():
            return _tool_error(f"File '{file}' not found in skill '{name}'")
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return _tool_error(f"File '{file}' in skill '{name}' is not UTF-8 text (likely binary — cannot load via load_skill)")

    skill_md = local_root / "SKILL.md"
    if not skill_md.is_file():
        return _tool_error(f"skill '{name}' has no SKILL.md")
    content = skill_md.read_text(encoding="utf-8")

    # List other files in this skill's tree
    all_files = sorted(str(p.relative_to(local_root)) for p in local_root.rglob("*") if p.is_file())
    files = [f for f in all_files if f and f != "SKILL.md"]
    if files:
        content += "\\n\\n---\\n## Skill Files\\n"
        content += "Use `load_skill(\\"" + name + "\\", file=\\"<path>\\")` to read:\\n"
        shown = files[:30]
        for f in shown:
            content += f"- `{f}`\\n"
        if len(files) > 30:
            content += f"\\n... and {len(files) - 30} more files. Use load_skill with file= to read specific files.\\n"

    # Truncate if content is too large
    if len(content) > 30000:
        content = content[:30000] + "\\n\\n... (truncated, use load_skill with file= to read specific files)"

    return content


class ci_fs:
    """Shared helper for crossing the Code Interpreter sandbox boundary.

    Public API — used by upload_to_s3, run_skill_script, check_capabilities,
    and any future tool that has to move data in or out of the CI session.

    The CI sandbox runs in a separate container from the agent, so
    the two filesystems are unrelated. Worse, even *inside* the sandbox
    there are two separate file views:

      - writeFiles / readFiles ops see a managed virtual FS used for
        uploads. `readFiles` rejects absolute paths and cannot see files
        that the sandbox's own Python process wrote.
      - executeCode / executeCommand see the real OS filesystem (cwd
        `/opt/amazon/genesis1p-tools/var` at time of writing). Anything
        matplotlib.savefig / open(...).write / pandas.to_csv produces
        lands here, invisible to readFiles.

    So `read_bytes` round-trips bytes through `executeCode` stdout (base64
    + sentinels). `write_bytes` keeps using writeFiles since uploads into
    the sandbox are what that op was built for. `sync_from_local` batches
    many writes into a single writeFiles call so skill staging is one
    round-trip instead of N.

    Every method returns `(value, error)` — `error is None` means success.
    """

    @staticmethod
    def _normalize(path):
        p = (path or "").lstrip("/")
        # matplotlib / PIL etc. default to `/tmp/foo.png`. The CI sandbox
        # workdir is the session root, so strip a leading `tmp/` to match.
        if p.startswith("tmp/"):
            p = p[len("tmp/"):]
        if p.startswith("./"):
            p = p[2:]
        return p

    @staticmethod
    def _session():
        session_id = getattr(run_command, "_session_id", None)
        ci_id = _os.environ.get("AGENT_STUDIO_CODE_INTERPRETER_ID")
        if not session_id or not ci_id:
            return None, None, None
        import boto3 as _boto3
        return _boto3.client("bedrock-agentcore", region_name=_REGION), ci_id, session_id

    @classmethod
    def _invoke(cls, name, arguments):
        """Call a CI filesystem op. Returns (stream_events, error_str_or_None)."""
        client, ci_id, session_id = cls._session()
        if client is None:
            return [], "no active Code Interpreter session — call run_command first"
        try:
            resp = client.invoke_code_interpreter(
                codeInterpreterIdentifier=ci_id,
                sessionId=session_id,
                name=name,
                arguments=arguments,
            )
        except Exception as e:
            return [], f"invoke_code_interpreter({name}) failed: {e}"
        events = list(resp.get("stream", []))
        for ev in events:
            result = ev.get("result") or {}
            if result.get("isError"):
                msgs = []
                for block in result.get("content", []) or []:
                    if isinstance(block.get("text"), str):
                        msgs.append(block["text"])
                    r = block.get("resource")
                    if isinstance(r, dict) and isinstance(r.get("text"), str):
                        msgs.append(r["text"])
                return events, "; ".join(msgs) or f"{name} returned isError with no message"
        return events, None

    @classmethod
    def _exec(cls, code):
        """Run Python in the CI session and return (stdout, stderr, exit_code, err_str_or_None)."""
        events, err = cls._invoke("executeCode", {"code": code, "language": "python"})
        if err:
            return "", "", -1, err
        stdout = stderr = ""
        exit_code = 0
        for ev in events:
            sc = (ev.get("result") or {}).get("structuredContent") or {}
            stdout += sc.get("stdout", "") or ""
            stderr += sc.get("stderr", "") or ""
            if sc.get("exitCode") is not None:
                exit_code = sc["exitCode"]
        return stdout, stderr, exit_code, None

    @classmethod
    def read_bytes(cls, path):
        """Read a file from the sandbox. Returns (bytes_or_None, error_str_or_None).

        Design note: CI's `readFiles` op only sees files placed via
        `writeFiles` — it does NOT see files the sandbox's Python code
        just wrote (matplotlib savefig, open().write(), pickle.dump,
        python-pptx, pandas.to_csv etc). Those live in the sandbox's
        real OS filesystem, reachable only from inside `executeCode`.

        So we round-trip bytes through stdout: base64-encode in-sandbox,
        print with sentinel markers, decode out here. Handles both
        absolute and relative paths since this runs inside executeCode's
        own cwd.
        """
        import base64 as _b64_
        # Try multiple candidate paths — the model may have passed an
        # absolute path like /tmp/chart.png, the sandbox cwd's relative
        # form, or a normalized version. Let the sandbox pick the one
        # that actually exists.
        raw = path or ""
        candidates = []
        for c in [raw, cls._normalize(raw), _os.path.basename(raw)]:
            if c and c not in candidates:
                candidates.append(c)
        code = (
            "import base64 as _b, os as _o, sys as _s\\n"
            "_cands = " + repr(candidates) + "\\n"
            "_picked = next((p for p in _cands if _o.path.isfile(p)), None)\\n"
            "if _picked is None:\\n"
            "    print('__CI_FS_ERR__:not_found:' + repr(_cands))\\n"
            "else:\\n"
            "    _sz = _o.path.getsize(_picked)\\n"
            "    with open(_picked,'rb') as _f: _data = _f.read()\\n"
            "    _s.stdout.write('__CI_FS_BEGIN__' + _picked + '|' + str(_sz) + '|' + _b.b64encode(_data).decode() + '__CI_FS_END__')\\n"
        )
        stdout, stderr, exit_code, err = cls._exec(code)
        if err:
            return None, err
        if "__CI_FS_ERR__:not_found" in stdout:
            return None, f"file not found in CI sandbox; tried {candidates}"
        import re as _re_
        m = _re_.search(r"__CI_FS_BEGIN__(.*?)\\|(\\d+)\\|([A-Za-z0-9+/=]+)__CI_FS_END__", stdout, _re_.DOTALL)
        if not m:
            msg = stderr.strip() or stdout.strip()[:500] or "no sentinel in stdout"
            return None, f"read_bytes exec returned no payload: {msg}"
        try:
            data = _b64_.b64decode(m.group(3))
        except Exception as e:
            return None, f"base64 decode failed: {e}"
        expected = int(m.group(2))
        if len(data) != expected:
            return None, f"length mismatch: got {len(data)}, expected {expected}"
        return data, None

    @classmethod
    def write_bytes(cls, path, data):
        """Write data to the sandbox. Returns error_str_or_None (None on success).

        Uses writeFiles (the documented upload path, which is what CI's
        own filesystem view will see on later readFiles calls).
        """
        import base64 as _b64_
        rel = cls._normalize(path)
        if isinstance(data, (bytes, bytearray)):
            payload = {"path": rel, "blob": _b64_.b64encode(bytes(data)).decode("ascii")}
        else:
            payload = {"path": rel, "text": str(data)}
        _, err = cls._invoke("writeFiles", {"content": [payload]})
        return err

    @classmethod
    def is_file(cls, path):
        """Return (exists: bool, error: str|None). Checks the sandbox OS FS."""
        if not path:
            return False, "empty path"
        raw = path or ""
        candidates = []
        for c in [raw, cls._normalize(raw), _os.path.basename(raw)]:
            if c and c not in candidates:
                candidates.append(c)
        code = (
            "import os as _o, sys as _s\\n"
            "_cands = " + repr(candidates) + "\\n"
            "_hit = next((p for p in _cands if _o.path.isfile(p)), '')\\n"
            "_s.stdout.write('__CI_FS_HIT__' + _hit + '__CI_FS_END__')\\n"
        )
        stdout, _stderr, _exit, err = cls._exec(code)
        if err:
            return False, err
        import re as _re_
        m = _re_.search(r"__CI_FS_HIT__(.*?)__CI_FS_END__", stdout)
        if not m:
            return False, "no sentinel in stdout"
        return bool(m.group(1)), None

    @classmethod
    def sync_from_local(cls, files, remote_base):
        """Copy a list of (local_path, remote_rel_path) pairs into the sandbox.

        Batches up to 20 small payloads per writeFiles call — exceeds that
        and the managed API starts rejecting with payload-too-large. Callers
        that need to sync a full skill directory should chunk accordingly.

        Text vs blob: AgentCore Code Interpreter's ``writeFiles`` silently
        stores ``blob`` fields as the literal base64 string instead of
        decoding them (empirically verified 2026-04-23 — contrary to the
        bedrock_agentcore SDK's own reference). So files that decode as
        UTF-8 text are sent via ``text`` and only true binaries fall back
        to ``blob``. Skills are mostly .py / .md / .svg — all text — so
        this path is the common one.

        Returns (count_uploaded, error_str_or_None).
        """
        import base64 as _b64_
        base = (remote_base or "").strip("/")
        content = []
        count = 0
        for local_path, rel in files:
            try:
                with open(local_path, "rb") as fh:
                    raw = fh.read()
            except Exception as e:
                return count, f"read {local_path}: {e}"
            remote_rel = (base + "/" + rel.lstrip("/")).lstrip("/") if base else rel.lstrip("/")
            entry = {"path": cls._normalize(remote_rel)}
            try:
                entry["text"] = raw.decode("utf-8")
            except UnicodeDecodeError:
                entry["blob"] = _b64_.b64encode(raw).decode("ascii")
            content.append(entry)
            count += 1
            if len(content) >= 20:
                _, err = cls._invoke("writeFiles", {"content": content})
                if err:
                    return count, err
                content = []
        if content:
            _, err = cls._invoke("writeFiles", {"content": content})
            if err:
                return count, err
        return count, None

    @classmethod
    def session_id(cls):
        """Current Code Interpreter session id, or None if not yet started."""
        return getattr(run_command, "_session_id", None)


def _tool_error(message: str) -> dict:
    """Strands-native error result — sets is_error on the assistant-visible
    content block so the model treats it as a failed tool call instead of
    misreading a JSON-stringified error as successful output.
    """
    return {"status": "error", "content": [{"text": str(message)}]}


@_tool
def run_command(command: str, language: str = "python") -> str:
    """Execute Python/JS/TS code or shell commands in a managed AgentCore sandbox.

    File paths tip: any file you save here (e.g. matplotlib savefig,
    pandas.to_csv, python-pptx) can later be passed to `upload_to_s3`
    using the same path — `upload_to_s3` will fetch the bytes back out
    of the sandbox for you. Absolute or relative paths both work.

    Args:
        command: The code or shell command to execute.
        language: "python" | "javascript" | "typescript" | "shell". Default: python.

    Returns:
        stdout on success, or an error tool-result (is_error=true) on failure.
    """
    import boto3 as _boto3

    ci_id = _os.environ.get("AGENT_STUDIO_CODE_INTERPRETER_ID")
    if not ci_id:
        return _tool_error("AGENT_STUDIO_CODE_INTERPRETER_ID not configured")

    region = _os.environ.get("AGENT_STUDIO_REGION", _REGION)
    client = _boto3.client("bedrock-agentcore", region_name=region)

    session_id = getattr(run_command, "_session_id", None)
    if not session_id:
        try:
            session_id = client.start_code_interpreter_session(
                codeInterpreterIdentifier=ci_id,
                name="agentstudio-subagent",
                sessionTimeoutSeconds=3600,
            )["sessionId"]
            run_command._session_id = session_id
        except Exception as e:
            return _tool_error(f"Failed to start code interpreter session: {e}")

    # Forward hydrated secrets into the CI sandbox. Skill scripts
    # (feishu, slack, jira, whatever) call os.environ.get(KEY) expecting
    # to see what the user saved in Agent Secrets. The sandbox runs in a
    # separate container with its own env, so without this prefix every
    # script would trip over empty credentials even though the agent
    # itself has them. Works for any key hydrated at cold start — new
    # skills that need new secrets require zero code changes here, just
    # save+redeploy in the UI.
    secret_prefix = _secret_env_prefix_for_ci()
    if language == "shell":
        op = "executeCommand"
        # For shell: translate the injected env into `export KEY='val'`
        # statements at the front of the command string. We don't reuse
        # the Python prefix because the sandbox's executeCommand runs
        # under /bin/bash, not Python.
        if _SECRET_ENV_KEYS:
            import shlex as _shlex
            exports = []
            for k in _SECRET_ENV_KEYS:
                v = _os.environ.get(k)
                if isinstance(v, str) and v:
                    exports.append(f"export {k}={_shlex.quote(v)}")
            if exports:
                command = "; ".join(exports) + "; " + command
        args = {"command": command}
    else:
        op = "executeCode"
        prefixed = (secret_prefix + command) if secret_prefix else command
        args = {"code": prefixed, "language": language}

    try:
        resp = client.invoke_code_interpreter(
            codeInterpreterIdentifier=ci_id,
            sessionId=session_id,
            name=op,
            arguments=args,
        )
    except Exception as e:
        return _tool_error(f"invoke_code_interpreter failed: {e}")

    stdout, stderr, exit_code = "", "", 0
    for event in resp.get("stream", []):
        if "result" not in event:
            continue
        r = event["result"]
        sc = r.get("structuredContent") or {}
        stdout += sc.get("stdout", "")
        stderr += sc.get("stderr", "")
        exit_code = sc.get("exitCode", exit_code)

    if exit_code and exit_code != 0:
        combined = (stdout + "\\n" + stderr).strip()
        return _tool_error(f"Exit code {exit_code}: {combined}")
    return stdout.strip() if stdout.strip() else "(no output)"


@_tool
def upload_to_s3(local_path: str, filename: str = "") -> str:
    """Upload a file to S3 for user download. Use this after generating files (e.g. PPTX, PDF, CSV, PNG).

    Accepts both agent local paths and Code Interpreter sandbox paths
    (e.g. a relative ``output.pptx`` or ``/tmp/chart.png`` created inside a
    run_command / run_skill_script call). The agent's /mnt/workspace/
    session storage is NOT mounted inside the CI sandbox, so do not pass
    ``/mnt/workspace/...`` as the output path of a CI-executed script —
    scripts should write a relative path or /tmp/ path inside the sandbox.
    The file is stored permanently and a download link appears automatically
    in the chat UI.

    Args:
        local_path: Absolute path to the file. Local filesystem checked
            first; if missing, the active Code Interpreter session is
            queried as a fallback.
        filename: Optional display filename. If empty, uses basename(local_path).

    Returns:
        JSON with s3_key for the uploaded file, or error message.
    """
    import os as _os2

    fname = filename or _os2.path.basename(local_path)
    import uuid as _uuid
    s3_key = f"outputs/{_uuid.uuid4().hex[:12]}_{fname}"

    # Detect content type
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    content_types = {
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "pdf": "application/pdf",
        "csv": "text/csv",
        "json": "application/json",
        "txt": "text/plain",
        "md": "text/markdown",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "svg": "image/svg+xml",
        "zip": "application/zip",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    content_type = content_types.get(ext, "application/octet-stream")
    # RFC 5987: non-ASCII filenames must go through `filename*=UTF-8''<url-encoded>`
    # — plain `filename="..."` only accepts US-ASCII and Chinese/Japanese/
    # emoji filenames silently get latin-1 mangled in the stored header.
    import urllib.parse as _urlparse
    if fname.isascii():
        disposition = f'attachment; filename="{fname}"'
    else:
        ascii_fallback = fname.encode("ascii", "replace").decode("ascii")
        quoted = _urlparse.quote(fname, safe="")
        disposition = f"attachment; filename=\\"{ascii_fallback}\\"; filename*=UTF-8''{quoted}"
    extra_args = {"ContentType": content_type, "ContentDisposition": disposition}

    # Fast path: file is on the agent's local filesystem
    if _os2.path.isfile(local_path):
        try:
            _s3.upload_file(local_path, _S3_BUCKET, s3_key, ExtraArgs=extra_args)
            return f"File uploaded successfully. Include this download link in your response:\\n__S3_DOWNLOAD__:{s3_key}:{fname}"
        except Exception as e:
            return _tool_error(f"Upload failed: {e}")

    # Fallback: file was generated inside the Code Interpreter sandbox.
    data, err = ci_fs.read_bytes(local_path)
    if err or data is None:
        return _tool_error(
            f"File not found: {local_path}. Tried the agent's "
            f"filesystem and the Code Interpreter sandbox ({err or 'no content'}). "
            "Tip: save artifacts with a relative path inside run_command "
            "(e.g. plt.savefig('chart.png'), not '/tmp/chart.png') and "
            "pass the same path here."
        )
    try:
        _s3.put_object(Bucket=_S3_BUCKET, Key=s3_key, Body=data, **extra_args)
        return f"File uploaded successfully. Include this download link in your response:\\n__S3_DOWNLOAD__:{s3_key}:{fname}"
    except Exception as e:
        return _tool_error(f"Upload failed: {e}")


# Per-session bookkeeping for run_skill_script. Maps (session_id, skill_name)
# to the *absolute* remote directory. Skill staging is not free — S3 reads +
# writeFiles round-trips scale with file count — so we only do it once per
# CI session per skill.
_STAGED_SKILLS: dict = {}
# Per-session record of the CI sandbox's workdir anchor (the absolute path
# where writeFiles drops relative uploads). Probed once per session by
# asking the sandbox for ``os.getcwd()`` before any user code has run.
# Writing absolute paths to _STAGED_SKILLS avoids the classic "persistent
# kernel cwd drift" bug documented across Jupyter/IPython, E2B, Modal,
# and papermill: a second ``os.chdir('skills/foo')`` after the first chdir
# lands in ``skills/foo/skills/foo`` (doesn't exist) because cwd persists
# across turns. The fix is to anchor once, then never re-chdir relative.
_SESSION_ANCHORS: dict = {}
_CI_SKILL_ROOT = "skills"  # remote layout: {_CI_SKILL_ROOT}/{name}/...


def _ci_sandbox_anchor() -> str:
    """Return the absolute path the CI sandbox treats as cwd at session boot.

    Probes once per CI session, caches per session. Falls back to the empty
    string (caller treats absent anchor as "use relative path and hope" —
    less robust, but covers the path before any run_command has started a
    session). Idempotent and cheap after first call.
    """
    session_id = ci_fs.session_id()
    if not session_id:
        return ""
    cached = _SESSION_ANCHORS.get(session_id)
    if cached is not None:
        return cached
    # Probe: print absolute cwd + a sentinel. We can't rely on user code
    # not having chdir'd yet, so run this from the sandbox-provided `/`
    # and ask for a stable reference (``pwd`` of /tmp's parent isn't
    # reliable — use os.path.realpath of the session's default HOME).
    probe = (
        "import os, sys\\n"
        # Try AgentCore's documented workspace anchor first, then HOME,
        # then fall back to current cwd (if the session hasn't drifted
        # yet this is correct).
        "for _c in ('/opt/amazon/genesis1p-tools/var', os.environ.get('HOME',''), os.getcwd()):\\n"
        "    if _c and os.path.isdir(_c):\\n"
        "        sys.stdout.write('__CI_ANCHOR__' + os.path.abspath(_c) + '__CI_ANCHOR_END__')\\n"
        "        break\\n"
    )
    stdout, _stderr, _exit, err = ci_fs._exec(probe)
    anchor = ""
    if not err:
        import re as _re_
        m = _re_.search(r"__CI_ANCHOR__(.*?)__CI_ANCHOR_END__", stdout)
        if m:
            anchor = m.group(1).strip()
    _SESSION_ANCHORS[session_id] = anchor
    return anchor


def _stage_skill_into_ci(name: str):
    """Copy a skill's files into the active Code Interpreter session.

    Returns (abs_remote_dir: str|None, error: str|None). The abs_remote_dir
    is a sandbox-absolute path (e.g. ``/opt/amazon/.../var/skills/ppt-
    generator``), resolved against the session anchor so repeat invocations
    don't suffer from cwd drift. Idempotent per (session, skill).
    """
    session_id = ci_fs.session_id()
    if not session_id:
        return None, "no active Code Interpreter session — call run_command first"

    cache_key = (session_id, name)
    if cache_key in _STAGED_SKILLS:
        return _STAGED_SKILLS[cache_key], None

    entry = _find_skill_by_name(name)
    if not entry:
        return None, f"skill '{name}' not attached to this agent"

    local_root, err = _ensure_skill_materialized(entry["id"], name)
    if err or local_root is None:
        return None, f"failed to fetch skill files: {err or 'unknown error'}"

    pairs = []
    for p in local_root.rglob("*"):
        if p.is_file():
            pairs.append((str(p), str(p.relative_to(local_root))))
    if not pairs:
        return None, f"skill '{name}' has no files"

    rel_remote = f"{_CI_SKILL_ROOT}/{name}"
    count, err = ci_fs.sync_from_local(pairs, rel_remote)
    if err:
        return None, f"sync {count}/{len(pairs)} files then failed: {err}"

    anchor = _ci_sandbox_anchor()
    abs_remote = f"{anchor}/{rel_remote}" if anchor else rel_remote

    _STAGED_SKILLS[cache_key] = abs_remote
    return abs_remote, None


@_tool
def run_skill_script(
    skill_name: str,
    script: str,
    args: str = "",
    cwd: str = "",
    language: str = "python",
) -> str:
    """Execute a script that ships with a skill inside Code Interpreter.

    Use this instead of manually `load_skill(..., file=...)` + `run_command`.
    The skill's files are copied into the sandbox once per session (cached),
    then the script runs as if the user invoked it from their cwd — so
    relative paths in ``args`` resolve against the files they just created,
    not against the skill's own directory.

    Scripts typically accept CLI arguments (--output, --theme, paths, etc).
    Pass them via ``args`` as a single shell-style string:

        run_skill_script("ppt-generator",
                         "ppt-master-assets/scripts/svg_to_pptx.py",
                         args="./ppt_svgs --output result.pptx -f ppt169")

    CWD selection (``cwd`` parameter):
        - ``""`` (default) — the CI session's current working directory,
          typically where the user wrote their output files (e.g.
          ``./ppt_svgs``). This is what most CLIs expect; a bare
          ``ppt_svgs`` in ``args`` resolves against user files, not the
          skill tree.
        - ``"skill"`` — the skill's own directory. Use this for scripts
          that read assets bundled with the skill via relative paths.
        - any absolute path — explicit override.

    Package-aware execution: if ``script`` points inside a Python package
    (any ancestor has ``__init__.py``), the tool runs the package's module
    via ``runpy.run_module`` so relative imports like
    ``from .pptx_cli import main`` work. Otherwise it executes the script
    as a top-level file. This is independent of ``cwd``.

    Args:
        skill_name: The skill to use (e.g. "ppt-generator"). Must be
            attached to this agent.
        script: Path to the script inside the skill. Examples:
            ``scripts/render.py``, ``ppt-master-assets/scripts/svg_to_pptx.py``,
            or a package module like
            ``ppt-master-assets/scripts/svg_to_pptx/pptx_cli.py``.
        args: CLI arguments passed to the script (space-separated, shell-
            parsed). Default empty.
        cwd: Working directory for the script. ``""`` (default) = CI
            session's current cwd, ``"skill"`` = skill directory, or an
            absolute path.
        language: "python" (default) | "shell" — how to invoke the script.

    Returns:
        The script's stdout, or an error tool-result (is_error=true).
    """
    if not skill_name or not isinstance(skill_name, str):
        return _tool_error("skill_name is required")
    if not script or not isinstance(script, str):
        return _tool_error("script path is required")

    # Force a session to exist before staging.
    if not ci_fs.session_id():
        boot = run_command("pass", "python")
        if isinstance(boot, dict) and boot.get("status") == "error":
            return boot

    remote_dir, err = _stage_skill_into_ci(skill_name)
    if err:
        return _tool_error(f"Failed to stage skill '{skill_name}': {err}")

    rel = script.lstrip("/")
    # remote_dir is already absolute (anchored via _ci_sandbox_anchor in
    # _stage_skill_into_ci). Using absolute everywhere avoids the classic
    # persistent-kernel cwd-drift bug — see E2B/Modal/Jupyter convention
    # of never trusting os.getcwd() across turns.

    import shlex as _shlex_
    try:
        argv_extra = _shlex_.split(args) if args else []
    except ValueError as e:
        return _tool_error(f"invalid args (shell parse failed): {e}")

    # cwd resolution: "" = preserve the CI session's current cwd (usually
    # where the user's output files live); "skill" = the skill's own
    # directory; any other value = treated as an absolute path. An invalid
    # relative string is rejected — ambiguous.
    cwd_spec = (cwd or "").strip()
    if cwd_spec == "":
        resolved_cwd = None  # signal: "don't chdir"
    elif cwd_spec == "skill":
        resolved_cwd = remote_dir
    elif cwd_spec.startswith("/"):
        resolved_cwd = cwd_spec
    else:
        return _tool_error(
            f"cwd must be '' (default user cwd), 'skill', or an absolute path — got {cwd_spec!r}"
        )

    if language == "shell":
        # Preserve quoting when passing args through shell.
        quoted_args = " ".join(_shlex_.quote(a) for a in argv_extra)
        script_abs = f"{remote_dir}/{rel}"
        if resolved_cwd:
            cmd = (
                f"cd {_shlex_.quote(resolved_cwd)} && "
                f"bash {_shlex_.quote(script_abs)} {quoted_args}"
            ).rstrip()
        else:
            cmd = f"bash {_shlex_.quote(script_abs)} {quoted_args}".rstrip()
        return run_command(cmd, "shell")

    # Python path: if the script sits inside a Python package (i.e. its
    # directory, or any ancestor up to the skill root, contains __init__.py),
    # use runpy.run_module so relative imports like ``from .sibling import x``
    # resolve against the package. Otherwise fall back to run_path for plain
    # top-level scripts.
    #
    # Matches the pytest-style package detection: walk up while each dir
    # has __init__.py AND is a valid Python identifier; parent of the top-
    # level package goes on sys.path; __init__.py resolves to the bare
    # package name; __main__.py runs via the package name.
    target_rel = rel
    # Emit a script that performs the detection + dispatch inside the sandbox.
    # Using pathlib since it's stdlib and simplifies the path math.
    code = (
        "from pathlib import Path as _P\\n"
        "import os, runpy, sys\\n"
        f"_abs = _P({remote_dir!r})\\n"
        f"_target = (_abs / {target_rel!r}).resolve()\\n"
        f"_argv_extra = {argv_extra!r}\\n"
        f"_cwd_override = {resolved_cwd!r}\\n"
        "_saved_cwd = os.getcwd()\\n"
        "_saved_path = list(sys.path)\\n"
        "_saved_argv = list(sys.argv)\\n"
        "if _cwd_override:\\n"
        "    os.chdir(_cwd_override)\\n"
        "if not _target.is_file():\\n"
        "    raise FileNotFoundError(str(_target))\\n"
        "# Walk up collecting package directories (pytest pattern).\\n"
        "_pkg_parts = []\\n"
        "_cur = _target.parent\\n"
        "_leaf = _target.stem if _target.name != '__init__.py' else None\\n"
        "while (_cur / '__init__.py').is_file() and _cur.name.isidentifier():\\n"
        "    _pkg_parts.insert(0, _cur.name)\\n"
        "    _parent = _cur.parent\\n"
        "    if _parent == _cur:\\n"
        "        break\\n"
        "    _cur = _parent\\n"
        "_pkg_root = _cur\\n"
        "try:\\n"
        "    sys.argv = [str(_target)] + _argv_extra\\n"
        "    if _pkg_parts:\\n"
        "        if str(_pkg_root) not in sys.path:\\n"
        "            sys.path.insert(0, str(_pkg_root))\\n"
        "        # __init__.py → bare package; __main__.py → package; else module.\\n"
        "        if _leaf in (None, '__main__'):\\n"
        "            _mod = '.'.join(_pkg_parts)\\n"
        "        else:\\n"
        "            _mod = '.'.join(_pkg_parts + [_leaf])\\n"
        "        runpy.run_module(_mod, run_name='__main__', alter_sys=True)\\n"
        "    else:\\n"
        "        if str(_target.parent) not in sys.path:\\n"
        "            sys.path.insert(0, str(_target.parent))\\n"
        "        runpy.run_path(str(_target), run_name='__main__')\\n"
        "finally:\\n"
        "    # Restore interpreter state so the next turn sees a clean\\n"
        "    # slate. Essential for the persistent-kernel case: without\\n"
        "    # this, each run_skill_script call would leave chdir / argv\\n"
        "    # drift for the next cell.\\n"
        "    sys.path[:] = _saved_path\\n"
        "    sys.argv[:] = _saved_argv\\n"
        "    os.chdir(_saved_cwd)\\n"
    )
    return run_command(code, "python")


@_tool
def check_capabilities() -> str:
    """Preflight probe — returns a structured JSON snapshot of what this
    agent can actually do right now. Call this before promising the user
    a file-generating skill so you don't commit to something that will fail
    mid-turn (missing CI session, missing skill assets, uninstalled deps).

    Returns JSON with keys:
      - ``code_interpreter`` — {"ready": bool, "session_id": str|None,
        "python_version": str|None, "cwd": str|None, "error": str|None}
      - ``skills`` — list of {"name", "id", "description", "loadable",
        "file_count", "materialized", "error"} for every skill attached
        to this agent. ``loadable`` is true when SKILL.md exists in
        S3; ``materialized`` means the skill's files have been pulled
        into the local cache this invocation.
      - ``agent_id`` — the runtime id used to scope S3 skill lookups,
        or null if the environment didn't expose it.

    Skills and CI runtime are probed independently. Safe to call
    multiple times; cheap after the first call (local in-memory caches).
    """
    report = {
        "code_interpreter": {"ready": False},
        "skills": [],
        "agent_id": _AGENT_ID or None,
    }

    ci_id = _os.environ.get("AGENT_STUDIO_CODE_INTERPRETER_ID")
    session_id = ci_fs.session_id()
    if not ci_id:
        report["code_interpreter"]["error"] = "AGENT_STUDIO_CODE_INTERPRETER_ID not configured"
    elif not session_id:
        report["code_interpreter"]["error"] = "no active session yet (call run_command once to start one)"
    else:
        probe = (
            "import sys, os, json\\n"
            "print(json.dumps({'python': sys.version.split()[0], 'cwd': os.getcwd()}))\\n"
        )
        stdout, _stderr, exit_code, err = ci_fs._exec(probe)
        if err or exit_code:
            report["code_interpreter"]["error"] = err or f"probe exit {exit_code}"
        else:
            try:
                meta = _json.loads(stdout.strip().splitlines()[-1])
                report["code_interpreter"].update({
                    "ready": True,
                    "session_id": session_id,
                    "python_version": meta.get("python"),
                    "cwd": meta.get("cwd"),
                })
            except Exception as e:
                report["code_interpreter"]["error"] = f"probe decode failed: {e}"

    # Enumerate the agent's attached skills from its manifest, then
    # for each one probe the S3 tree to count declared files. Declared
    # file counts reflect what IS available, whether or not the skill has
    # been materialized to local cache this invocation — that distinction
    # matters for preflight (we want to say "you have 783 files over
    # there" before the first use, not after).
    try:
        skills = _load_skills_manifest()
        prefix_root = _agent_s3_prefix()
        for s in skills:
            sid = s.get("id", "")
            name = s.get("name", "")
            description = s.get("description", "")
            entry_err = None
            file_count = 0
            loadable = False
            if not prefix_root:
                entry_err = "agent_id unknown (set AGENT_STUDIO_AGENT_ID or OTEL_RESOURCE_ATTRIBUTES)"
            has_script = False
            if sid and prefix_root:
                try:
                    paginator = _s3.get_paginator("list_objects_v2")
                    skill_prefix = f"{prefix_root}{sid}/"
                    for page in paginator.paginate(Bucket=_S3_BUCKET, Prefix=skill_prefix):
                        for obj in page.get("Contents", []):
                            key = obj["Key"]
                            rel = key[len(skill_prefix):]
                            if not rel:
                                continue
                            file_count += 1
                            if rel == "SKILL.md":
                                loadable = True
                            elif rel.endswith(".py") or rel.endswith(".sh"):
                                has_script = True
                except Exception as e:
                    entry_err = f"list failed: {e}"
            # "prompt" = SKILL.md only (instructions; use load_skill to read).
            # "scripted" = has .py or .sh — run_skill_script works here.
            # "assets-only" = other files but no scripts — load_skill(file=...)
            # is the read path; run_skill_script will fail.
            if not loadable:
                skill_type = "unknown"
            elif has_script:
                skill_type = "scripted"
            elif file_count <= 1:
                skill_type = "prompt"
            else:
                skill_type = "assets-only"
            report["skills"].append({
                "name": name,
                "id": sid,
                "description": description,
                "type": skill_type,
                "loadable": loadable,
                "file_count": file_count,
                "materialized": sid in _CACHED_SKILLS,
                "error": entry_err,
            })
    except Exception as e:
        report["skills_error"] = str(e)

    return _json.dumps(report, ensure_ascii=False, indent=2)


# Per-invocation limits for read_document
_DOC_MAX_BYTES = 10 * 1024 * 1024   # 10 MB download ceiling
_DOC_MAX_CHARS = 50_000             # output truncation ceiling


def _doc_truncate(text: str) -> str:
    """Truncate to _DOC_MAX_CHARS with a clear warning suffix."""
    if len(text) <= _DOC_MAX_CHARS:
        return text
    return text[:_DOC_MAX_CHARS] + (
        f"\\n\\n... [TRUNCATED: document exceeded "
        f"{_DOC_MAX_CHARS} characters, showing first portion only]"
    )


@_tool
def read_document(file_key: str) -> str:
    """Read an uploaded document from S3 and return its text content.

    Handles both binary documents (PDF, Excel, CSV, TSV) and plain-text
    files (Markdown, JSON, YAML, log files, source code, etc.). Two key
    shapes are accepted:

    * ``workspaces/<caller_ws>/storage/...`` — files stored in the caller's workspace.
    * ``uploads/attachments/<sessionId>/...`` — chat attachments uploaded from the UI
      (the session ID is an unguessable identifier produced client-side).

    Any other prefix (including other workspaces) is rejected. Agents
    cannot fetch S3 over HTTPS (bucket rejects anonymous GETs); always
    use this tool to read user-attached files.

    Args:
        file_key: Relative S3 key. Examples:
            ``workspaces/ws-abc/storage/uploads/u-xyz/report.pdf`` or
            ``uploads/attachments/sess-123/notes.md``.

    Returns:
        Plain text extracted from the document, or a short diagnostic string on error.
        Output is capped at 50,000 characters; downloads exceeding 10 MB are rejected.
    """
    if not file_key or not isinstance(file_key, str):
        return "Error: file_key is required."

    # Normalize leading slash; forbid absolute URLs
    if "://" in file_key:
        return "Error: file_key must be an S3 key, not a URL."
    key = file_key.lstrip("/")

    # Access scoping — allow either the caller's workspace storage, or chat-attachment
    # uploads (session IDs are unguessable UUIDs minted client-side).
    ws = _workspace_id or ""
    if not ws:
        return "Error: workspace context is not available; cannot validate file access."
    workspace_prefix = f"workspaces/{ws}/storage/"
    attachment_prefix = "uploads/attachments/"
    allowed = False
    if key.startswith(workspace_prefix):
        allowed = True
    elif key.startswith(attachment_prefix):
        # Require a non-empty session segment: uploads/attachments/<session>/<filename>
        remainder = key[len(attachment_prefix):]
        session_seg, _, rest = remainder.partition("/")
        if session_seg and rest:
            allowed = True
    if not allowed:
        return (
            "Error: file_key must start with "
            f"'workspaces/{ws}/storage/' or 'uploads/attachments/<session>/'. "
            "Cross-workspace reads are not allowed."
        )

    # Resolve kind — case-insensitive. Plain-text extensions fall through
    # to a single UTF-8 decode branch so they share the S3 access path.
    lower = key.lower()
    _PLAINTEXT_EXTS = (
        ".md", ".markdown", ".txt", ".log",
        ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env",
        ".xml", ".html", ".htm", ".css", ".svg",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
        ".sh", ".bash", ".zsh", ".fish",
        ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".kt", ".swift",
        ".sql", ".graphql", ".proto",
        ".dockerfile", ".gitignore", ".editorconfig",
    )
    if lower.endswith(".pdf"):
        kind = "pdf"
    elif lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        kind = "xlsx"
    elif lower.endswith(".csv"):
        kind = "csv"
    elif lower.endswith(".tsv"):
        kind = "tsv"
    elif any(lower.endswith(ext) for ext in _PLAINTEXT_EXTS):
        kind = "text"
    else:
        return (
            "Error: unsupported file type. read_document handles .pdf, .xlsx, .xlsm, "
            ".csv, .tsv, and common plain-text formats (.md, .txt, .json, .yaml, "
            ".log, source code, etc.)."
        )

    # Size check via HEAD before download
    try:
        head = _s3.head_object(Bucket=_S3_BUCKET, Key=key)
        size = int(head.get("ContentLength", 0) or 0)
    except Exception as e:
        return f"Error: failed to stat s3://{_S3_BUCKET}/{key}: {e}"

    if size > _DOC_MAX_BYTES:
        return (
            f"Error: file is {size} bytes, exceeds 10 MB limit. "
            "Ask the user to provide a smaller file or a specific excerpt."
        )

    try:
        obj = _s3.get_object(Bucket=_S3_BUCKET, Key=key)
        data = obj["Body"].read()
    except Exception as e:
        return f"Error: failed to download s3://{_S3_BUCKET}/{key}: {e}"

    if len(data) > _DOC_MAX_BYTES:
        return (
            f"Error: downloaded {len(data)} bytes, exceeds 10 MB limit."
        )

    try:
        if kind == "text":
            # Try UTF-8 first, fall back to latin-1 so binary-ish log files
            # still surface something useful instead of blowing up.
            try:
                body = data.decode("utf-8")
            except UnicodeDecodeError:
                body = data.decode("latin-1", errors="replace")
            if not body.strip():
                return "(File is empty.)"
            return _doc_truncate(body)

        if kind == "pdf":
            import io as _io
            try:
                from pypdf import PdfReader  # type: ignore
            except ImportError:
                return "Error: pypdf is not installed in the agent runtime."
            try:
                reader = PdfReader(_io.BytesIO(data))
            except Exception as e:
                return f"Error: failed to open PDF: {e}"
            pages = []
            for i, page in enumerate(reader.pages, start=1):
                try:
                    text = page.extract_text() or ""
                except Exception as e:
                    text = f"[page extraction failed: {e}]"
                pages.append(f"\\n\\n=== Page {i} ===\\n\\n" + text)
            body = "".join(pages).lstrip()
            if not body.strip():
                return "(PDF contains no extractable text — it may be scanned or image-only.)"
            return _doc_truncate(body)

        if kind == "xlsx":
            import io as _io
            try:
                from openpyxl import load_workbook  # type: ignore
            except ImportError:
                return "Error: openpyxl is not installed in the agent runtime."
            try:
                wb = load_workbook(filename=_io.BytesIO(data), data_only=True, read_only=True)
            except Exception as e:
                return f"Error: failed to open workbook: {e}"
            parts = []
            for sheet_name in wb.sheetnames:
                parts.append(f"=== Sheet: {sheet_name} ===")
                ws_obj = wb[sheet_name]
                for row in ws_obj.iter_rows(values_only=True):
                    cells = ["" if v is None else str(v) for v in row]
                    parts.append("\\t".join(cells))
                parts.append("")  # blank line between sheets
            body = "\\n".join(parts).rstrip()
            return _doc_truncate(body)

        # csv / tsv
        try:
            import pandas as _pd  # type: ignore
        except ImportError:
            return "Error: pandas is not installed in the agent runtime."
        import io as _io
        sep = "\\t" if kind == "tsv" else ","
        try:
            df = _pd.read_csv(_io.BytesIO(data), sep=sep)
        except Exception as e:
            return f"Error: failed to parse {kind}: {e}"
        try:
            body = df.to_string(index=False)
        except Exception as e:
            return f"Error: failed to render {kind} as text: {e}"
        return _doc_truncate(body)
    except Exception as e:
        return f"Error: unexpected failure while extracting document text: {e}"


# ── Browser (async Playwright over AgentCore Browser CDP) ────────────────
# Pattern follows AWS sample:
# agentcore-samples/02-use-cases/enterprise-web-intelligence-agent/strands/browser_tools.py
# — use async_playwright directly (not strands_tools.browser) with a single
# persistent event loop + nest_asyncio, since Strands agents call sync tools
# from inside an async event loop and strands_tools.browser's session dict
# gets lost across calls in that setup.

_browser_state = {"loop": None, "playwright": None, "browser": None, "page": None,
                  "client": None, "session_id": None}


def _ensure_playwright_driver_executable():
    """AgentCore extracts deployment.zip with Python zipfile which drops
    unix exec bits, AND /var/task is read-only so chmod fails in-place.

    Copy node to /tmp (writable), chmod +x, point Playwright at it via
    PLAYWRIGHT_NODEJS_PATH. cli.js etc. only need read permission, which
    zipfile preserves.
    """
    import shutil as _shutil
    src_node = "/var/task/playwright/driver/node"
    dst_node = "/tmp/playwright-node"
    if not _os.path.isfile(src_node):
        return
    try:
        if not _os.path.isfile(dst_node):
            _shutil.copyfile(src_node, dst_node)
        _os.chmod(dst_node, 0o755)
        _os.environ["PLAYWRIGHT_NODEJS_PATH"] = dst_node
    except Exception as _e:
        import sys as _sys
        print(f"playwright driver prep failed: {_e}", file=_sys.stderr)


def _run_async(coro):
    """Run an async coroutine on our persistent event loop."""
    import asyncio as _aio
    loop = _browser_state["loop"]
    if loop is None or loop.is_closed():
        try:
            import nest_asyncio as _nest
            _nest.apply()
        except Exception:
            pass
        loop = _aio.new_event_loop()
        _aio.set_event_loop(loop)
        _browser_state["loop"] = loop
    return loop.run_until_complete(coro)


async def _get_page_async():
    """Ensure browser session is started and return the active Page."""
    if _browser_state["page"] is not None:
        try:
            if not _browser_state["page"].is_closed():
                return _browser_state["page"]
        except Exception:
            pass

    _ensure_playwright_driver_executable()

    br_id = _os.environ.get("AGENT_STUDIO_BROWSER_ID")
    if not br_id:
        raise RuntimeError("AGENT_STUDIO_BROWSER_ID not configured")

    from bedrock_agentcore.tools.browser_client import BrowserClient
    from playwright.async_api import async_playwright

    client = BrowserClient(region=_REGION)
    session_id = client.start(identifier=br_id, session_timeout_seconds=3600)

    ws_url, headers = client.generate_ws_headers()

    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(ws_url, headers=headers)
    context = browser.contexts[0] if browser.contexts else await browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.pages[0] if context.pages else await context.new_page()
    await page.set_viewport_size({"width": 1280, "height": 800})

    # AgentCore Browser pre-configures its Chrome context with a 10s default
    # navigation timeout, which is too short for heavy pages (sina, etc).
    # Override at both context and page level so neither fallback is 10s.
    try:
        context.set_default_navigation_timeout(45000)
        context.set_default_timeout(45000)
        page.set_default_navigation_timeout(45000)
        page.set_default_timeout(45000)
    except Exception:
        pass

    _browser_state.update({
        "playwright": pw, "browser": browser, "page": page,
        "client": client, "session_id": session_id,
    })
    return page


async def _reset_browser_async():
    """Close the current browser connection; next call will reconnect."""
    for key, obj in (("browser", _browser_state.get("browser")),
                     ("playwright", _browser_state.get("playwright"))):
        if obj is None:
            continue
        try:
            if key == "browser":
                await obj.close()
            else:
                await obj.stop()
        except Exception:
            pass
    client = _browser_state.get("client")
    if client is not None:
        try:
            client.stop()
        except Exception:
            pass
    _browser_state.update({"playwright": None, "browser": None,
                           "page": None, "client": None, "session_id": None})


@_tool
def browser_use(action: str, url: str = "", selector: str = "",
                value: str = "", expression: str = "",
                wait_ms: int = 2000) -> str:
    """Interact with a managed headless Chrome via AgentCore Browser.

    Use this when a task requires navigating real web pages: login flows,
    clicking buttons, filling forms, evaluating JavaScript, or capturing a
    screenshot. Session is warm for 1h across calls.

    Args:
        action: One of "navigate", "text", "screenshot", "click", "fill", "eval".
        url: Target URL for "navigate" (http/https only).
        selector: CSS selector for "click" / "fill".
        value: Input value for "fill".
        expression: JavaScript expression for "eval".
        wait_ms: Extra wait after navigate/click before reading back, in ms.

    Returns:
        "navigate"   → "OK: navigated to <url>"
        "text"       → body.innerText (truncated to 8000 chars)
        "screenshot" → S3 download marker (auto-uploaded PNG)
        "click"      → "OK: clicked <selector>" or an error string
        "fill"       → "OK: filled <selector>" or an error string
        "eval"       → JSON-encoded result of the expression
    """
    import uuid as _uuid2
    if action not in ("navigate", "text", "screenshot", "click", "fill", "eval"):
        return _json.dumps({"error": f"unknown action: {action}"})
    if action == "navigate":
        if not url or not url.startswith(("http://", "https://")):
            return _json.dumps({"error": "navigate requires http(s) url"})

    async def _do_async():
        page = await _get_page_async()
        timeout_ms = max(wait_ms, 45000)

        if action == "navigate":
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return f"OK: navigated to {url}"

        if action == "text":
            text = await page.inner_text("body")
            import re as _re2
            text = _re2.sub(r"\\n{3,}", "\\n\\n", text)
            text = _re2.sub(r" {2,}", " ", text).strip()
            if len(text) > 8000:
                text = text[:8000] + "\\n\\n... (truncated at 8000 chars)"
            return text

        if action == "eval":
            if not expression:
                return _json.dumps({"error": "eval requires expression"})
            try:
                result = await page.evaluate(expression)
                return _json.dumps({"value": result})
            except Exception as e:
                return _json.dumps({"error": f"eval failed: {e}"})

        if action == "click":
            if not selector:
                return _json.dumps({"error": "click requires selector"})
            try:
                await page.click(selector, timeout=timeout_ms)
                return f"OK: clicked {selector}"
            except Exception as e:
                return _json.dumps({"error": f"click failed: {e}"})

        if action == "fill":
            if not selector:
                return _json.dumps({"error": "fill requires selector"})
            try:
                await page.fill(selector, value, timeout=timeout_ms)
                return f"OK: filled {selector}"
            except Exception as e:
                return _json.dumps({"error": f"fill failed: {e}"})

        # screenshot
        png_bytes = await page.screenshot(full_page=True, type="png")
        if not png_bytes:
            return _json.dumps({"error": "screenshot returned no data"})
        s3_key = f"outputs/{_uuid2.uuid4().hex[:12]}_screenshot.png"
        try:
            _s3.put_object(
                Bucket=_S3_BUCKET, Key=s3_key,
                Body=png_bytes,
                ContentType="image/png",
                ContentDisposition='attachment; filename="screenshot.png"',
            )
        except Exception as e:
            return _json.dumps({"error": f"s3 upload failed: {e}"})
        return {
            "status": "success",
            "content": [
                {"image": {"format": "png", "source": {"bytes": png_bytes}}},
                {"text": f"Screenshot captured. Include this download link in your response:\\n__S3_DOWNLOAD__:{s3_key}:screenshot.png"},
            ],
        }

    try:
        return _run_async(_do_async())
    except Exception as e:
        try:
            _run_async(_reset_browser_async())
        except Exception:
            pass
        try:
            return _run_async(_do_async())
        except Exception as e2:
            return _json.dumps({"error": f"browser_use failed: {e2}"})
'''

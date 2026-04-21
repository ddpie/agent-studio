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
            + "\\n\\nUse load_skill(name) to load a skill\\'s full instructions when needed."
        )
    prompt += "\\n\\n## File Sharing\\nWhen you generate files (PPTX, PDF, CSV, images, etc.), save them to /mnt/workspace/ (persistent across sessions) instead of /tmp/ (ephemeral). ALWAYS use upload_to_s3(local_path) to make them downloadable. Never tell the user you cannot send files. After uploading, the download button appears automatically — do NOT create markdown links like [filename](url) for downloads."
    prompt += "\\n\\n## File Reading\\nWhen the user attaches a PDF, Excel workbook (.xlsx/.xlsm), CSV, or TSV, call read_document(file_key=<s3 key>) to extract its text. The attachment marker in the user message includes the exact S3 key to pass. For generic text files (source code, logs, plain .txt), use read_file against a local path instead."
    agent = Agent(
        model=BedrockModel(model_id=model_id),
        system_prompt=prompt,
        tools=_ALL_TOOLS + [_builtin.load_skill, _builtin.run_command, _builtin.upload_to_s3, _builtin.read_document, _builtin.browser_use],
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
            + "\\n\\nUse load_skill(name) to load a skill\\'s full instructions when needed."
        )
    prompt += "\\n\\n## File Sharing\\nWhen you generate files (PPTX, PDF, CSV, images, etc.), save them to /mnt/workspace/ (persistent across sessions) instead of /tmp/ (ephemeral). ALWAYS use upload_to_s3(local_path) to make them downloadable. Never tell the user you cannot send files. After uploading, the download button appears automatically — do NOT create markdown links like [filename](url) for downloads."
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
            model=BedrockModel(model_id=model_id),
            system_prompt=prompt,
            tools=_ALL_TOOLS + mcp_tools + [_builtin.load_skill, _builtin.run_command, _builtin.upload_to_s3, _builtin.read_document, _builtin.browser_use],
        )
        async for chunk in _stream_and_record(agent, payload):
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
    if not session_id.startswith("sched-") or not _AGENT_ID:
        async for chunk in _stream_with_tools(agent, _build_input(payload)):
            yield chunk
        return

    run_id = _generate_ulid()
    _write_run_started(_AGENT_ID, run_id, session_id, payload)
    chunks = []
    start_ns = _time.time_ns()
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
    except Exception as e:
        _write_run_failed(_AGENT_ID, run_id, e)
        raise
'''

# ── tools.py header ─────────────────────────────────────────────────────────
TOOLS_PY_HEADER = "from strands import tool\n\n"

# ── builtin_tools.py (injected into every sub-agent zip) ──────────────────
BUILTIN_TOOLS_CODE = '''\
"""Built-in tools for Agent Studio sub-agents — skill loading with local cache."""

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

# Local cache directory — uses managed session storage if available, else /tmp
_CACHE_ROOT = _Path("/mnt/workspace/skills") if _os.path.isdir("/mnt/workspace") else _Path("/tmp/skills_cache")
_CACHE_READY = False


def _download_skill_file(args):
    """Download a single S3 object to local cache. Used by ThreadPoolExecutor."""
    key, local_path = args
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        _s3.download_file(_S3_BUCKET, key, str(local_path))
        return True
    except Exception:
        return False


def ensure_skills_cached():
    """Download all skills from S3 to local cache in parallel. Skips if already cached."""
    global _CACHE_READY
    if _CACHE_READY:
        return

    # Check if index already cached (session storage persists across invocations)
    index_path = _CACHE_ROOT / "index.json"
    if index_path.exists():
        _CACHE_READY = True
        return

    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    # Download index
    try:
        obj = _s3.get_object(Bucket=_S3_BUCKET, Key="skills/index.json")
        index_data = obj["Body"].read().decode("utf-8")
        index_path.write_text(index_data, encoding="utf-8")
        skills = _json.loads(index_data)
    except Exception:
        _CACHE_READY = True
        return

    if not skills:
        _CACHE_READY = True
        return

    # Collect all files to download
    download_tasks = []
    for skill in skills:
        sid = skill.get("id", "")
        if not sid:
            continue
        prefix = f"skills/{sid}/"
        try:
            paginator = _s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=_S3_BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    rel = key[len("skills/"):]  # e.g. "abc123/SKILL.md"
                    local_path = _CACHE_ROOT / rel
                    if not local_path.exists():
                        download_tasks.append((key, local_path))
        except Exception:
            pass

    # Parallel download (up to 20 concurrent)
    if download_tasks:
        with _ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(_download_skill_file, download_tasks))

    _CACHE_READY = True


def get_skills_listing() -> str:
    """Read skills index, return formatted listing for prompt injection."""
    ensure_skills_cached()
    index_path = _CACHE_ROOT / "index.json"
    try:
        if index_path.exists():
            skills = _json.loads(index_path.read_text(encoding="utf-8"))
        else:
            obj = _s3.get_object(Bucket=_S3_BUCKET, Key="skills/index.json")
            skills = _json.loads(obj["Body"].read().decode("utf-8"))
        if not skills:
            return ""
        lines = [f"- {s['name']}: {s['description']}" for s in skills if not s.get("deleted")]
        return "\\n".join(lines)
    except Exception:
        return ""


def _read_local_or_s3(s3_key: str) -> str | None:
    """Read from local cache first, fallback to S3."""
    rel = s3_key[len("skills/"):] if s3_key.startswith("skills/") else s3_key
    local_path = _CACHE_ROOT / rel
    if local_path.exists():
        return local_path.read_text(encoding="utf-8")
    try:
        obj = _s3.get_object(Bucket=_S3_BUCKET, Key=s3_key)
        content = obj["Body"].read().decode("utf-8")
        # Cache for next time
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content, encoding="utf-8")
        return content
    except Exception:
        return None


def _list_local_or_s3(prefix: str) -> list[str]:
    """List files from local cache first, fallback to S3."""
    # prefix like "skills/abc123/"
    rel_dir = prefix[len("skills/"):] if prefix.startswith("skills/") else prefix
    local_dir = _CACHE_ROOT / rel_dir
    if local_dir.exists() and local_dir.is_dir():
        files = []
        for p in local_dir.rglob("*"):
            if p.is_file():
                files.append(str(p.relative_to(local_dir)))
        return files
    try:
        resp = _s3.list_objects_v2(Bucket=_S3_BUCKET, Prefix=prefix, MaxKeys=500)
        return [o["Key"][len(prefix):] for o in resp.get("Contents", []) if o["Key"] != prefix]
    except Exception:
        return []


@_tool
def load_skill(name: str, file: str = "") -> str:
    """Load a skill by name. Returns the full SKILL.md content, or a specific file.

    When called without `file`, returns SKILL.md and lists available files.
    When called with `file`, returns that file's content (e.g. "scripts/clean_csv.py").

    Args:
        name: The skill name (e.g. "data-analyzer").
        file: Optional path to a specific file within the skill (e.g. "scripts/demo.py").

    Returns:
        The skill content, or an error message.
    """
    ensure_skills_cached()

    # Read index to find skill_id by name
    index_path = _CACHE_ROOT / "index.json"
    try:
        if index_path.exists():
            skills = _json.loads(index_path.read_text(encoding="utf-8"))
        else:
            obj = _s3.get_object(Bucket=_S3_BUCKET, Key="skills/index.json")
            skills = _json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        return _json.dumps({"error": f"Failed to read skill index: {e}"})

    skill_id = None
    for s in skills:
        if s.get("name") == name:
            skill_id = s.get("id")
            break

    if not skill_id:
        available = [s.get("name", "") for s in skills if not s.get("deleted")]
        return _json.dumps({
            "error": f"Skill \\'{name}\\' not found.",
            "available_skills": available,
        })

    prefix = f"skills/{skill_id}/"

    # If a specific file is requested, read it
    if file:
        content = _read_local_or_s3(prefix + file.lstrip("/"))
        if content is None:
            return _json.dumps({"error": f"File \\'{file}\\' not found in skill \\'{name}\\'"})
        return content

    # Default: read SKILL.md + list all files
    content = _read_local_or_s3(f"{prefix}SKILL.md")
    if content is None:
        return _json.dumps({"error": f"Failed to read skill SKILL.md"})

    # List other files
    all_files = _list_local_or_s3(prefix)
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


@_tool
def run_command(command: str, language: str = "python") -> str:
    """Execute Python/JS/TS code or shell commands in a managed AgentCore sandbox.

    Args:
        command: The code or shell command to execute.
        language: "python" | "javascript" | "typescript" | "shell". Default: python.

    Returns:
        stdout on success, or JSON { "error": ..., "output": ... } on failure.
    """
    import boto3 as _boto3

    ci_id = _os.environ.get("AGENT_STUDIO_CODE_INTERPRETER_ID")
    if not ci_id:
        return _json.dumps({"error": "AGENT_STUDIO_CODE_INTERPRETER_ID not configured"})

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
            return _json.dumps({"error": f"Failed to start code interpreter session: {e}"})

    op = "executeCode"
    args = {"code": command, "language": language if language != "shell" else "python"}
    if language == "shell":
        op = "executeCommand"
        args = {"command": command}

    try:
        resp = client.invoke_code_interpreter(
            codeInterpreterIdentifier=ci_id,
            sessionId=session_id,
            name=op,
            arguments=args,
        )
    except Exception as e:
        return _json.dumps({"error": f"invoke_code_interpreter failed: {e}"})

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
        return _json.dumps({"error": f"Exit code {exit_code}", "output": combined})
    return stdout.strip() if stdout.strip() else "(no output)"


@_tool
def upload_to_s3(local_path: str, filename: str = "") -> str:
    """Upload a local file to S3 for user download. Use this after generating files (e.g. PPTX, PDF, CSV).

    The file will be stored permanently. The user's browser will generate a download link on demand.

    Args:
        local_path: Absolute path to the file on the local filesystem (e.g. /tmp/output.pptx).
        filename: Optional display filename. If empty, uses the original filename.

    Returns:
        JSON with s3_key for the uploaded file, or error message.
    """
    import os as _os2
    import time as _time

    if not _os2.path.isfile(local_path):
        return _json.dumps({"error": f"File not found: {local_path}"})

    fname = filename or _os2.path.basename(local_path)
    import uuid as _uuid
    s3_key = f"outputs/{_uuid.uuid4().hex[:12]}_{fname}"

    try:
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

        _s3.upload_file(
            local_path, _S3_BUCKET, s3_key,
            ExtraArgs={"ContentType": content_type, "ContentDisposition": f'attachment; filename="{fname}"'},
        )

        # Return plain text with download marker — LLM should include this verbatim in response
        return f"File uploaded successfully. Include this download link in your response:\\n__S3_DOWNLOAD__:{s3_key}:{fname}"
    except Exception as e:
        return f"Upload failed: {e}"


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
    """Read an uploaded document (PDF, xlsx, csv, tsv) from S3 and return its text content.

    Prefer this over generic file readers when the user uploads a PDF, Excel workbook, or
    tabular text file. Two key shapes are accepted:

    * ``workspaces/<caller_ws>/storage/...`` — files stored in the caller's workspace.
    * ``uploads/attachments/<sessionId>/...`` — chat attachments uploaded from the UI
      (the session ID is an unguessable identifier produced client-side).

    Any other prefix (including other workspaces) is rejected.

    Args:
        file_key: Relative S3 key. Examples:
            ``workspaces/ws-abc/storage/uploads/u-xyz/report.pdf`` or
            ``uploads/attachments/sess-123/report.pdf``.

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

    # Resolve extension — case-insensitive
    lower = key.lower()
    if lower.endswith(".pdf"):
        kind = "pdf"
    elif lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        kind = "xlsx"
    elif lower.endswith(".csv"):
        kind = "csv"
    elif lower.endswith(".tsv"):
        kind = "tsv"
    else:
        return (
            "Error: unsupported file type. read_document supports .pdf, .xlsx, .xlsm, "
            ".csv, .tsv. For plain text use a different tool."
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
        if kind == "pdf":
            import io as _io
            try:
                from pypdf import PdfReader  # type: ignore
            except ImportError:
                return "Error: pypdf is not installed in the sub-agent runtime."
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
                return "Error: openpyxl is not installed in the sub-agent runtime."
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
            return "Error: pandas is not installed in the sub-agent runtime."
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


# ── Browser CDP helpers (shared by browser_use) ──────────────────────────
_browser_cdp_id_counter = [0]


def _browser_cdp_session():
    """Return an open (session_id, ws) pair, reusing across calls.

    Reconnects transparently if the stored WebSocket is dead. Session
    timeout is 1h; Agent Core will auto-recycle the browser session on
    expiry.
    """
    import websocket  # websocket-client
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session_id = getattr(browser_use, "_session_id", None)
    ws = getattr(browser_use, "_ws", None)
    if session_id and ws:
        return session_id, ws

    br_id = _os.environ.get("AGENT_STUDIO_BROWSER_ID")
    if not br_id:
        raise RuntimeError("AGENT_STUDIO_BROWSER_ID not configured")
    boto_session = _boto3.Session(region_name=_REGION)
    client = boto_session.client("bedrock-agentcore", region_name=_REGION)
    sess = client.start_browser_session(
        browserIdentifier=br_id,
        name="agentstudio-browser-use",
        sessionTimeoutSeconds=3600,
        viewPort={"width": 1280, "height": 800},
    )
    client.update_browser_stream(
        browserIdentifier=br_id,
        sessionId=sess["sessionId"],
        streamUpdate={"automationStreamUpdate": {"streamStatus": "ENABLED"}},
    )
    cdp = sess["streams"]["automationStream"]["streamEndpoint"]
    creds = boto_session.get_credentials().get_frozen_credentials()
    https_url = cdp.replace("wss://", "https://")
    host = cdp.split("/")[2]
    req = AWSRequest(method="GET", url=https_url, headers={"host": host})
    SigV4Auth(creds, "bedrock-agentcore", _REGION).add_auth(req)
    auth_headers = [
        f"{k}: {v}" for k, v in dict(req.headers).items()
        if k in ("Authorization", "X-Amz-Date", "X-Amz-Security-Token", "Host")
    ]
    w = websocket.create_connection(cdp, timeout=30, header=auth_headers)
    browser_use._session_id = sess["sessionId"]
    browser_use._ws = w
    return sess["sessionId"], w


def _browser_cdp_send(ws, method, params=None):
    """Send a CDP command and wait for the matching response."""
    _browser_cdp_id_counter[0] += 1
    mid = _browser_cdp_id_counter[0]
    ws.send(_json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        data = _json.loads(ws.recv())
        if data.get("id") == mid:
            return data


@_tool
def browser_use(action: str, url: str = "", selector: str = "",
                value: str = "", expression: str = "",
                wait_ms: int = 2000) -> str:
    """Interact with a managed headless Chrome via AgentCore Browser.

    Use this when a task requires navigating real web pages: login flows,
    clicking buttons, filling forms, evaluating JavaScript, or capturing a
    screenshot. For a static HTML scrape, fetch_webpage (if loaded) is
    cheaper. Session is warm for 1h across calls.

    Args:
        action: One of "navigate", "text", "screenshot", "click", "fill",
            "eval".
        url: Target URL for "navigate" (http/https only).
        selector: CSS selector for "click" / "fill".
        value: Input value for "fill".
        expression: JavaScript expression for "eval" (returnByValue=true).
        wait_ms: Extra wait after navigate/click before reading back, in ms.
            Default 2000.

    Returns:
        "navigate"   → "OK: navigated to <url>"
        "text"       → body.innerText (truncated to 8000 chars)
        "screenshot" → S3 download marker (auto-uploaded PNG)
        "click"      → "OK: clicked <selector>" or an error string
        "fill"       → "OK: filled <selector>" or an error string
        "eval"       → JSON-encoded result of the expression
    """
    import time as _time2
    import base64 as _base64
    if action not in ("navigate", "text", "screenshot", "click", "fill", "eval"):
        return _json.dumps({"error": f"unknown action: {action}"})
    if action == "navigate":
        if not url or not url.startswith(("http://", "https://")):
            return _json.dumps({"error": "navigate requires http(s) url"})

    try:
        _sid, ws = _browser_cdp_session()
    except Exception as e:
        return _json.dumps({"error": f"start_browser_session failed: {e}"})

    def _do():
        if action == "navigate":
            _browser_cdp_send(ws, "Page.enable")
            _browser_cdp_send(ws, "Page.navigate", {"url": url})
            _time2.sleep(max(0, wait_ms) / 1000.0)
            return f"OK: navigated to {url}"

        if action == "text":
            resp = _browser_cdp_send(ws, "Runtime.evaluate", {
                "expression": "document.body ? document.body.innerText : ''",
                "returnByValue": True,
            })
            text = ((resp.get("result") or {}).get("result") or {}).get("value") or ""
            import re as _re2
            text = _re2.sub(r"\\n{3,}", "\\n\\n", text)
            text = _re2.sub(r" {2,}", " ", text).strip()
            if len(text) > 8000:
                text = text[:8000] + "\\n\\n... (truncated at 8000 chars)"
            return text

        if action == "eval":
            if not expression:
                return _json.dumps({"error": "eval requires expression"})
            resp = _browser_cdp_send(ws, "Runtime.evaluate", {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            })
            r = (resp.get("result") or {}).get("result") or {}
            if "value" in r:
                return _json.dumps({"value": r["value"]})
            return _json.dumps({"value": None, "type": r.get("type")})

        if action == "click":
            if not selector:
                return _json.dumps({"error": "click requires selector"})
            expr = (
                "(() => { const el = document.querySelector(" + _json.dumps(selector) + "); "
                "if (!el) return 'not_found'; el.click(); return 'ok'; })()"
            )
            resp = _browser_cdp_send(ws, "Runtime.evaluate", {
                "expression": expr, "returnByValue": True,
            })
            outcome = ((resp.get("result") or {}).get("result") or {}).get("value")
            _time2.sleep(max(0, wait_ms) / 1000.0)
            if outcome == "ok":
                return f"OK: clicked {selector}"
            return _json.dumps({"error": f"click failed: {outcome}"})

        if action == "fill":
            if not selector:
                return _json.dumps({"error": "fill requires selector"})
            expr = (
                "(() => { const el = document.querySelector(" + _json.dumps(selector) + "); "
                "if (!el) return 'not_found'; el.focus(); "
                "el.value = " + _json.dumps(value) + "; "
                "el.dispatchEvent(new Event('input', {bubbles:true})); "
                "el.dispatchEvent(new Event('change', {bubbles:true})); "
                "return 'ok'; })()"
            )
            resp = _browser_cdp_send(ws, "Runtime.evaluate", {
                "expression": expr, "returnByValue": True,
            })
            outcome = ((resp.get("result") or {}).get("result") or {}).get("value")
            if outcome == "ok":
                return f"OK: filled {selector}"
            return _json.dumps({"error": f"fill failed: {outcome}"})

        # screenshot
        resp = _browser_cdp_send(ws, "Page.captureScreenshot", {"format": "png"})
        b64 = (resp.get("result") or {}).get("data")
        if not b64:
            return _json.dumps({"error": "screenshot returned no data"})
        import uuid as _uuid2
        s3_key = f"outputs/{_uuid2.uuid4().hex[:12]}_screenshot.png"
        try:
            _s3.put_object(
                Bucket=_S3_BUCKET, Key=s3_key,
                Body=_base64.b64decode(b64),
                ContentType="image/png",
                ContentDisposition='attachment; filename="screenshot.png"',
            )
        except Exception as e:
            return _json.dumps({"error": f"s3 upload failed: {e}"})
        return (
            "Screenshot captured. Include this download link in your response:\\n"
            f"__S3_DOWNLOAD__:{s3_key}:screenshot.png"
        )

    try:
        return _do()
    except Exception:
        # Session likely expired / ws dropped — reconnect once.
        try:
            try:
                ws.close()
            except Exception:
                pass
            browser_use._session_id = None
            browser_use._ws = None
            _sid, ws = _browser_cdp_session()
            return _do()
        except Exception as e:
            browser_use._session_id = None
            browser_use._ws = None
            return _json.dumps({"error": f"browser_use failed: {e}"})
'''

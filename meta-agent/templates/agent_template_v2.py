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
    import builtin_tools as _builtin
    skills_listing = _builtin.get_skills_listing()
    prompt = SYSTEM_PROMPT
    if skills_listing:
        prompt += (
            "\\n\\n## Available Skills\\n"
            + skills_listing
            + "\\n\\nUse load_skill(name) to load a skill\\'s full instructions when needed."
        )
    prompt += "\\n\\n## File Sharing\\nWhen you generate files (PPTX, PDF, CSV, images, etc.), ALWAYS use upload_to_s3(local_path) to make them downloadable. Never tell the user you cannot send files."
    agent = Agent(
        model=BedrockModel(model_id=model_id),
        system_prompt=prompt,
        tools=_ALL_TOOLS + [_builtin.load_skill, _builtin.run_command, _builtin.upload_to_s3],
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
    import builtin_tools as _builtin
    skills_listing = _builtin.get_skills_listing()
    prompt = SYSTEM_PROMPT
    if skills_listing:
        prompt += (
            "\\n\\n## Available Skills\\n"
            + skills_listing
            + "\\n\\nUse load_skill(name) to load a skill\\'s full instructions when needed."
        )
    prompt += "\\n\\n## File Sharing\\nWhen you generate files (PPTX, PDF, CSV, images, etc.), ALWAYS use upload_to_s3(local_path) to make them downloadable. Never tell the user you cannot send files."
    with mcp_client as mcp:
        mcp_tools = mcp.list_tools_sync()
        agent = Agent(
            model=BedrockModel(model_id=model_id),
            system_prompt=prompt,
            tools=_ALL_TOOLS + mcp_tools + [_builtin.load_skill, _builtin.run_command],
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
_ACCOUNT_ID = _os.getenv("AWS_ACCOUNT_ID", "557690613480")
_S3_BUCKET = _os.getenv(
    "AGENT_STUDIO_S3_BUCKET",
    f"bedrock-agentcore-codebuild-sources-{_ACCOUNT_ID}-{_REGION}",
)
_s3 = _boto3.client("s3", region_name=_REGION)

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
    """Execute Python code or shell commands. Use for data processing, calculations, or running scripts.

    Args:
        command: The code or command to execute.
        language: "python" to run Python code, "shell" to run a shell command. Default: python.

    Returns:
        The stdout output, or an error message if execution failed.
    """
    import subprocess as _sp

    timeout = 30
    # Use persistent workspace if available, fallback to /tmp
    cwd = "/mnt/workspace" if _os.path.isdir("/mnt/workspace") else "/tmp"

    if language == "python":
        try:
            result = _sp.run(
                ["python3", "-c", command],
                capture_output=True, text=True, timeout=timeout, cwd="/tmp",
            )
            output = result.stdout
            if result.returncode != 0:
                output += ("\\n" + result.stderr) if result.stderr else ""
                return _json.dumps({"error": f"Exit code {result.returncode}", "output": output.strip()})
            return output.strip() if output.strip() else "(no output)"
        except _sp.TimeoutExpired:
            return _json.dumps({"error": f"Execution timed out after {timeout}s"})
        except Exception as e:
            return _json.dumps({"error": str(e)})

    elif language == "shell":
        try:
            result = _sp.run(
                command, shell=True,
                capture_output=True, text=True, timeout=timeout, cwd="/tmp",
            )
            output = result.stdout
            if result.returncode != 0:
                output += ("\\n" + result.stderr) if result.stderr else ""
                return _json.dumps({"error": f"Exit code {result.returncode}", "output": output.strip()})
            return output.strip() if output.strip() else "(no output)"
        except _sp.TimeoutExpired:
            return _json.dumps({"error": f"Execution timed out after {timeout}s"})
        except Exception as e:
            return _json.dumps({"error": str(e)})

    else:
        return _json.dumps({"error": f"Unsupported language: {language}. Use 'python' or 'shell'."})


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
    # Determine agent ID from environment or use generic path
    agent_id = _os.getenv("AGENT_RUNTIME_ID", "unknown")
    timestamp = int(_time.time())
    s3_key = f"agents/{agent_id}/outputs/{timestamp}_{fname}"

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
'''

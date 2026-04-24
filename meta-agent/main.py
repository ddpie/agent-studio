"""Agent Studio — Meta-Agent Entry Point (Kiro-backed).

This entrypoint replaces the legacy Strands loop with Kiro CLI as the
reasoning backbone. Per-invocation flow:

  1. Ensure the local HTTP MCP server (wrapping the 34 Meta-Agent tools)
     is running on 127.0.0.1:<port>. Started lazily on first invoke;
     reused across invocations in the same runtime pod.
  2. Apply per-invocation identity (caller_id, workspace_id) to the
     tools._scope module + the handful of tool modules that read their
     own _caller_id — same wiring the Strands path used.
  3. Materialize / update the custom-agent config under the AgentCore
     sessionStorage mount (/mnt/kiro). The mount survives across
     invocations of the same runtimeSessionId, so Kiro's session/load
     can restore the full turn history natively.
  4. Spawn `kiro-cli-chat acp --agent meta-agent`. Try session/load
     with the previously-saved uuid; fall back to session/new if it
     fails (e.g. after a runtime version bump clears the store).
  5. Stream user turn -> ACP session/update events -> SSE wire frames
     the existing frontend consumes. Keepalive pump matches the legacy
     30s cadence to survive CloudFront's 60s origin idle timeout.

Images: the legacy path forwarded base64 images to Strands. Kiro ACP
also accepts image prompts (promptCapabilities.image=true) but the
payload shape differs. Out of scope for the first cut — log a warning
and ignore images for now; reinstate once the core path is verified.
"""

# OTEL bootstrap MUST run before strands / boto3 / bedrock_agentcore are
# imported so that auto-instrumentation can monkey-patch them. See the
# comment on the legacy path for the full rationale.
import os as _os
if _os.environ.get("AGENT_OBSERVABILITY_ENABLED", "").lower() == "true":
    try:
        from opentelemetry.instrumentation.auto_instrumentation import initialize as _otel_init  # type: ignore
        _otel_init()
    except Exception as _e:  # noqa: BLE001
        import sys as _sys
        print(f"OTEL auto-instrumentation disabled: {_e}", file=_sys.stderr)

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Tool imports: every @tool function the Meta-Agent exposes. We do not
# instantiate a Strands Agent anymore, but we still need these callables
# so mcp_server.py can register them, and so tools._scope._caller_id
# plumbing keeps working unchanged.
from tools.create_agent import create_agent, list_prompt_templates

# Auto-publish tool catalog on startup (kept from the legacy path — the
# catalog is consumed by sub-agents, not the Meta-Agent itself).
try:
    from tools_library.registry import upload_tool_catalog
    _catalog_count = upload_tool_catalog()
    print(f"Tool catalog published: {_catalog_count} tools")
except Exception as _e:
    print(f"Warning: Failed to publish tool catalog: {_e}")

from tools.list_agents import list_agents
from tools.delete_agent import delete_agent, restore_agent, purge_agent
from tools.invoke_agent import invoke_agent
from tools.get_agent_detail import get_agent_detail
from tools.update_agent import update_agent
from tools.check_agent_logs import check_agent_logs
from tools.create_skill import create_skill
from tools.list_skills import list_skills
from tools.update_skill import update_skill
from tools.delete_skill import delete_skill
from tools.import_skill import import_skill
from tools.read_skill_file import list_skill_files, read_skill_file
from tools.write_skill_file import write_skill_file, delete_skill_file as delete_skill_file_in_skill
from tools.sync_agent_skill import sync_agent_skill
from tools.attach_agent_skill import attach_agent_skill
from tools.list_mcp_servers import list_mcp_servers
from tools.list_mcp_target_tools import list_mcp_target_tools
from tools.manage_secrets import set_agent_secrets, list_agent_secrets, delete_agent_secret
from tools_library.registry import list_tool_library, get_tool_library_code
from tools.analyze_trace import analyze_trace
from tools.create_schedule import create_schedule
from tools.validate_agent import validate_agent
from tools.preview_code import preview_assembled_code
from tools.link_agent import link_agent, unlink_agent

from kiro_adapter.acp_client import ACPError, KiroACPClient
from kiro_adapter.kiro_home import (
    DEFAULT_MODEL as KIRO_DEFAULT_MODEL,
    KIRO_HOME_DEFAULT,
    clear_kiro_session,
    default_system_prompt_src,
    ensure_kiro_home,
    load_kiro_session_uuid,
    save_kiro_session_uuid,
)
from kiro_adapter.mcp_server import (
    MCP_HOST_DEFAULT,
    MCP_PORT_DEFAULT,
    apply_scope,
    build_mcp_server,
    serve_forever,
)
from kiro_adapter.sse_mapper import ACPToSSEMapper, keepalive

log = logging.getLogger("meta_agent")

# --------------------------------------------------------------------------
# Module-level configuration
# --------------------------------------------------------------------------

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
    list_skill_files,
    read_skill_file,
    write_skill_file,
    delete_skill_file_in_skill,
    update_skill,
    delete_skill,
    import_skill,
    sync_agent_skill,
    attach_agent_skill,
    list_mcp_servers,
    list_mcp_target_tools,
    analyze_trace,
    create_schedule,
    validate_agent,
    preview_assembled_code,
    list_tool_library,
    get_tool_library_code,
    set_agent_secrets,
    list_agent_secrets,
    delete_agent_secret,
    link_agent,
    unlink_agent,
]

# The deployment zip unpacks at the Runtime container root, so kiro-cli-chat
# ships alongside main.py under kiro-bin/. Locate it relative to this file
# rather than hard-coding /var/runtime or similar.
_KIRO_BINARY = str(Path(__file__).resolve().parent / "kiro-bin" / "kiro-cli-chat")

# The AgentCore sessionStorage mount point. Must match `mountPath` in
# deploy-agentcore.sh's filesystemConfigurations. We also accept an override
# via env for local testing (where /mnt/kiro may not exist).
_KIRO_HOME = os.environ.get("AGENT_STUDIO_KIRO_HOME", KIRO_HOME_DEFAULT)

# Kiro API key is required. If unset we fail fast on first invoke rather
# than letting kiro-cli-chat produce a confusing stderr.
_KIRO_API_KEY = os.environ.get("KIRO_API_KEY", "")

# Default model id (claude-opus-4.6 per decision). Overridable per-invoke
# via payload.model_id, or globally via env.
_KIRO_DEFAULT_MODEL = os.environ.get("AGENT_STUDIO_KIRO_MODEL", KIRO_DEFAULT_MODEL)

# Keep-alive cadence matching the legacy path (CloudFront 60s origin idle).
_KEEPALIVE_INTERVAL_S = 30.0

# Per-turn ACP prompt budget. Headroom over anything realistic; the
# container itself will be torn down long before this trips.
_PROMPT_TIMEOUT_S = 600.0

# --------------------------------------------------------------------------
# MCP server bootstrap (lazy, one-shot per container)
# --------------------------------------------------------------------------

_mcp_server = None
_mcp_task: asyncio.Task | None = None
_mcp_start_lock = asyncio.Lock()


async def _ensure_mcp_started() -> None:
    """Start the local MCP server once per container.

    AgentCore reuses the same process across invocations that share a
    runtimeSessionId (and often even across unrelated sessionIds on a
    warm pod), so we only want one server binding to :8765.
    """
    global _mcp_server, _mcp_task
    if _mcp_task is not None and not _mcp_task.done():
        return
    async with _mcp_start_lock:
        if _mcp_task is not None and not _mcp_task.done():
            return
        _mcp_server = build_mcp_server(
            ALL_TOOLS, host=MCP_HOST_DEFAULT, port=MCP_PORT_DEFAULT
        )
        _mcp_task = asyncio.create_task(
            serve_forever(_mcp_server), name="local-mcp-server"
        )
        # FastMCP sets up its transport inside run_streamable_http_async;
        # give it a beat to bind before the first Kiro connection arrives.
        # The alternative (wait for a health probe) adds complexity for
        # vanishing benefit — cold start already has room for 200ms.
        await asyncio.sleep(0.2)


# --------------------------------------------------------------------------
# History replay: Kiro is fresh on the first turn (session/new) and
# likewise after a session/load fallback. We only prepend history then,
# to avoid Kiro and AgentCore both holding the same conversation twice.
# --------------------------------------------------------------------------


def _format_history(history: list[dict]) -> str:
    """Render the frontend-supplied history into a single context blob.

    Mirrors the format the legacy Strands path used (main.py:486-500 in
    pre-Kiro) so the prompt text the model sees on turn 1 is the same.
    """
    if not history:
        return ""
    parts: list[str] = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user":
            parts.append(f"<user>{content}</user>")
        elif role == "assistant":
            parts.append(f"<assistant>{content}</assistant>")
    return "\n".join(parts)


def _compose_user_text(prompt: str, history_blob: str, is_new_session: bool) -> str:
    """Build the text Kiro's session/prompt receives."""
    if is_new_session and history_blob:
        return (
            f"Here is our conversation so far:\n{history_blob}\n\n"
            f"Now the user says:\n<user>{prompt}</user>\n\n"
            f"Continue the conversation naturally, keeping full context of "
            f"what was discussed above."
        )
    return prompt


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload, context):
    prompt = payload.get("prompt", "Hello! I'm Agent Studio.")
    history = payload.get("history", [])
    images = payload.get("images") or []
    model_id = payload.get("model_id") or _KIRO_DEFAULT_MODEL
    caller_id = payload.get("caller_id", "unknown")
    workspace_id = payload.get("workspace_id", "")

    if images:
        log.warning(
            "images forwarded from payload but the Kiro backend does not "
            "accept image prompts yet; dropping %d image(s)",
            len(images),
        )

    if not _KIRO_API_KEY:
        err = "KIRO_API_KEY is not set; Meta-Agent cannot reach Kiro backend"
        log.error(err)
        yield json.dumps({"__error": err})
        return

    apply_scope(caller_id, workspace_id)
    await _ensure_mcp_started()

    # Per-invocation home. Idempotent: every invoke rewrites the custom
    # agent config + prompt (so prompt edits take effect without a redeploy),
    # never touches Kiro-owned sessions/cli/.
    saved_uuid = ensure_kiro_home(
        mcp_host=MCP_HOST_DEFAULT,
        mcp_port=MCP_PORT_DEFAULT,
        system_prompt_src=default_system_prompt_src(),
        model_id=model_id,
        home_root=_KIRO_HOME,
    )

    client = KiroACPClient(
        binary=_KIRO_BINARY,
        kiro_home=_KIRO_HOME,
        api_key=_KIRO_API_KEY,
        agent_name="meta-agent",
        trust_all_tools=True,
    )
    mapper = ACPToSSEMapper()

    try:
        await client.start()

        session_id, is_new = await client.ensure_session(
            saved_uuid=saved_uuid,
            cwd=_KIRO_HOME,
            mcp_servers=[],
        )
        if is_new:
            # Whether saved_uuid was None (first turn) or the uuid was
            # stale after a runtime version bump, Kiro has no context.
            # Pin the newly-assigned uuid for subsequent turns and
            # prepend the replayed history.
            save_kiro_session_uuid(session_id, home_root=_KIRO_HOME)
        elif saved_uuid and session_id != saved_uuid:
            # Defensive: shouldn't happen (session/load reuses the input
            # uuid), but if it ever does, re-pin.
            save_kiro_session_uuid(session_id, home_root=_KIRO_HOME)

        user_text = _compose_user_text(
            prompt, _format_history(history), is_new
        )

        async for frame in _stream_with_keepalive(
            client.prompt(session_id, user_text, timeout_s=_PROMPT_TIMEOUT_S),
            mapper,
        ):
            yield frame

    except ACPError as e:
        log.exception("ACP error")
        if "Session not found" in (e.message or ""):
            clear_kiro_session(home_root=_KIRO_HOME)
        yield json.dumps({"__error": f"kiro_acp_error: {e.message}"})
    except Exception as e:  # noqa: BLE001
        log.exception("Meta-Agent invoke failed")
        yield json.dumps({"__error": f"meta_agent_error: {e!r}"})
    finally:
        await client.close()


async def _stream_with_keepalive(events_iter, mapper: ACPToSSEMapper):
    """Adapt an async iterator of ACP events into SSE frames with heartbeat.

    Iterates `events_iter` (from `KiroACPClient.prompt`). For each event,
    pipes it through the mapper and yields all resulting SSE frames. If
    the upstream is silent for `_KEEPALIVE_INTERVAL_S`, yields a keepalive
    sentinel so CloudFront doesn't close the origin stream.
    """
    ait = events_iter.__aiter__()
    last_yield = time.monotonic()
    while True:
        try:
            event = await asyncio.wait_for(
                ait.__anext__(), timeout=_KEEPALIVE_INTERVAL_S
            )
        except asyncio.TimeoutError:
            yield keepalive()
            last_yield = time.monotonic()
            continue
        except StopAsyncIteration:
            return
        for frame in mapper.translate(event):
            yield frame
            last_yield = time.monotonic()


if __name__ == "__main__":
    app.run()

"""Agent Studio — Meta-Agent Entry Point (Kiro-backed).

Replaces the legacy Strands loop with Kiro CLI as the reasoning backbone.
Per-invocation flow:

  1. Apply per-invocation identity (caller_id, workspace_id) to the
     tools._scope module + the handful of tool modules that read their
     own _caller_id. Same wiring the Strands path used.
  2. Materialize / update the custom-agent config under the AgentCore
     sessionStorage mount (/mnt/kiro). The mount survives across
     invocations of the same runtimeSessionId, so Kiro's session/load
     can restore the full turn history natively.
  3. Spawn `kiro-cli-chat acp --agent meta-agent`. Kiro itself spawns
     our MCP tool server (stdio transport) as a subprocess.
  4. Try session/load with the previously-saved uuid; fall back to
     session/new if it fails (e.g. after a runtime version bump clears
     the store).
  5. Stream user turn -> ACP session/update events -> SSE wire frames
     the existing frontend consumes. Keepalive pump matches the legacy
     30s cadence to survive CloudFront's 60s origin idle timeout.

Images: the legacy path forwarded base64 images to Strands. Kiro ACP
also accepts image prompts (promptCapabilities.image=true) but the
payload shape differs. Out of scope for the first cut — log a warning
and ignore images for now; reinstate once the core path is verified.

Diagnostic instrumentation
--------------------------
The boot-phase timer (`_log_phase`), OS fingerprint, and Kiro binary
readiness helper are permanent — we've been burned twice already by
AgentCore container behavior that is not documented elsewhere (zip
extractor drops the execute bit, read-only /var/task, loopback TCP
blocked). Every boot leaves enough of a trail in CloudWatch to isolate
a fresh regression without redeploying debug builds.
"""

# OTEL bootstrap MUST run before strands / boto3 / bedrock_agentcore are
# imported so that auto-instrumentation can monkey-patch them.
import os as _os
import sys as _sys
import time as _time
import logging as _boot_logging

_t_boot_start = _time.monotonic()
_boot_log = _boot_logging.getLogger("meta_agent.boot")
_boot_log.setLevel(_boot_logging.INFO)
if not _boot_log.handlers:
    _h = _boot_logging.StreamHandler()
    _h.setFormatter(_boot_logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    _boot_log.addHandler(_h)


def _log_phase(label: str) -> None:
    # Python logging rather than print() — AgentCore's log pipeline hooks
    # the root logger via the OTEL distro, and print() to stderr was
    # observed to be dropped during cold start.
    _boot_log.info(f"[init+{_time.monotonic()-_t_boot_start:5.2f}s] {label}")


_log_phase("boot start")

if _os.environ.get("AGENT_OBSERVABILITY_ENABLED", "").lower() == "true":
    try:
        from opentelemetry.instrumentation.auto_instrumentation import initialize as _otel_init  # type: ignore
        _otel_init()
    except Exception as _e:  # noqa: BLE001
        _boot_log.warning(f"OTEL auto-instrumentation disabled: {_e}")
_log_phase("OTEL bootstrap done")

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
_log_phase("bedrock_agentcore imported")

# Tool imports: every @tool function the Meta-Agent exposes. The Kiro
# backend itself no longer calls these — Kiro reaches them through the
# stdio MCP subprocess (kiro_adapter.mcp_stdio_server). But we still
# import them here so ALL_TOOLS is the single source of truth; the stdio
# server re-imports `main` and reads ALL_TOOLS from it.
from tools.create_agent import create_agent, list_prompt_templates
_log_phase("create_agent imported")

# Auto-publish tool catalog on startup (kept from the legacy path — the
# catalog is consumed by sub-agents, not the Meta-Agent itself).
try:
    from tools_library.registry import upload_tool_catalog
    _catalog_count = upload_tool_catalog()
    _boot_log.info(f"Tool catalog published: {_catalog_count} tools")
except Exception as _e:
    _boot_log.warning(f"Failed to publish tool catalog: {_e}")
_log_phase("tool catalog published")

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

_log_phase("all tools imported")


from kiro_adapter.acp_client import ACPError, KiroACPClient
from kiro_adapter.kiro_home import (
    DEFAULT_MODEL as KIRO_DEFAULT_MODEL,
    KIRO_HOME_DEFAULT,
    KIRO_PERSIST_ROOT,
    clear_kiro_session,
    default_system_prompt_src,
    ensure_kiro_home,
    save_kiro_session_uuid,
)
from kiro_adapter.mcp_server import apply_scope
from kiro_adapter.sse_mapper import ACPToSSEMapper, keepalive
_log_phase("kiro_adapter imported")

# One-time OS fingerprint. Helps future-us diagnose binary-compat issues
# (glibc vs musl, new EBADF on unexpected kernels, etc.) from logs alone.
try:
    import platform as _platform
    import subprocess as _subprocess_diag
    _os_release = ""
    try:
        with open("/etc/os-release") as _f:
            _os_release = _f.read().strip().replace("\n", " | ")
    except Exception:
        pass
    try:
        _r = _subprocess_diag.run(
            ["ldd", "--version"], capture_output=True, text=True, timeout=3
        )
        _ldd_v = ((_r.stdout or _r.stderr or "").splitlines() or [""])[0]
    except Exception as _e:
        _ldd_v = f"(ldd failed: {_e})"
    _boot_log.info(
        f"runtime fingerprint: platform={_platform.platform()} "
        f"machine={_platform.machine()} libc={_platform.libc_ver()} "
        f"ldd='{_ldd_v}' os_release='{_os_release[:200]}'"
    )
except Exception as _e:
    _boot_log.warning(f"runtime fingerprint skipped: {_e}")

log = logging.getLogger("meta_agent")

# --------------------------------------------------------------------------
# Module-level configuration
# --------------------------------------------------------------------------

# Single source of truth for the Meta-Agent's tool surface. Referenced by
# mcp_stdio_server (which imports this module and reads ALL_TOOLS).
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

# Path to the meta-agent package root. Passed to the MCP stdio subprocess
# as PYTHONPATH so `kiro_adapter` and `tools` imports resolve regardless
# of Kiro's cwd.
_META_AGENT_DIR = str(Path(__file__).resolve().parent)

# kiro-cli-chat binary — ships alongside main.py under kiro-bin/ in the
# deployment zip. Reassigned by _ensure_kiro_binary_ready() if we end up
# using a /tmp copy (see the function).
_KIRO_BINARY = str(Path(_META_AGENT_DIR) / "kiro-bin" / "kiro-cli-chat")

# Where kiro-cli-chat treats as $HOME. Local /tmp path by default — see
# kiro_home.py for why it is NOT the AgentCore sessionStorage mount.
_KIRO_HOME = os.environ.get("AGENT_STUDIO_KIRO_HOME", KIRO_HOME_DEFAULT)

# Cross-invocation pointer storage. Must match the `mountPath` declared
# in deploy-agentcore.sh's filesystemConfigurations.
_KIRO_PERSIST = os.environ.get("AGENT_STUDIO_KIRO_PERSIST", KIRO_PERSIST_ROOT)

# Kiro API key is required. Fail fast on first invoke rather than letting
# kiro-cli-chat produce a confusing stderr.
_KIRO_API_KEY = os.environ.get("KIRO_API_KEY", "")

# Default model. Overridable per-invoke via payload.model_id or globally
# via env.
_KIRO_DEFAULT_MODEL = os.environ.get("AGENT_STUDIO_KIRO_MODEL", KIRO_DEFAULT_MODEL)

# Keep-alive cadence matching the legacy path (CloudFront 60s origin idle).
_KEEPALIVE_INTERVAL_S = 30.0

# Per-turn ACP prompt budget. Headroom over anything realistic; the
# container itself will be torn down long before this trips.
_PROMPT_TIMEOUT_S = 600.0

# --------------------------------------------------------------------------
# Kiro binary readiness
# --------------------------------------------------------------------------

_kiro_binary_ready = False


def _ensure_kiro_binary_ready() -> None:
    """Ensure kiro-cli-chat is executable and launchable.

    AgentCore Runtime extracts the deployment zip to /var/task, which we
    observed to have two quirks:
      - Execute bit from the zip archive (0o755) is dropped; on disk the
        binary lands at 0o644, so create_subprocess_exec raises EACCES.
      - /var/task is read-only, so in-place chmod raises EPERM.
    Fix: if the binary isn't already executable, copy it to /tmp (writable)
    and repoint _KIRO_BINARY. Skip the copy entirely when the file is
    already +x (future-proofs against AgentCore fixing the extractor).
    """
    global _kiro_binary_ready, _KIRO_BINARY
    if _kiro_binary_ready:
        return
    try:
        st = _os.stat(_KIRO_BINARY)
        _boot_log.info(
            f"kiro binary stat: path={_KIRO_BINARY} size={st.st_size} "
            f"mode={oct(st.st_mode & 0o777)} uid={st.st_uid} gid={st.st_gid}"
        )
        if st.st_mode & 0o111:
            _kiro_binary_ready = True
            return
        try:
            _os.chmod(_KIRO_BINARY, st.st_mode | 0o111)
            _boot_log.info("kiro binary: chmod +x succeeded in place")
        except (PermissionError, OSError) as e:
            _boot_log.info(
                f"kiro binary: in-place chmod failed ({e}); copying to /tmp"
            )
            import shutil as _shutil
            dst = "/tmp/kiro-cli-chat"
            _shutil.copyfile(_KIRO_BINARY, dst)
            _os.chmod(dst, 0o755)
            _KIRO_BINARY = dst
            _boot_log.info(f"kiro binary: copied and chmodded at {dst}")
    except FileNotFoundError:
        _boot_log.error(f"kiro binary missing at {_KIRO_BINARY}")
    _kiro_binary_ready = True


# --------------------------------------------------------------------------
# History replay: Kiro is fresh on the first turn (session/new) and
# likewise after a session/load fallback. We only prepend history then,
# to avoid Kiro and AgentCore both holding the same conversation twice.
# --------------------------------------------------------------------------


def _format_history(history: list[dict]) -> str:
    """Render the frontend-supplied history into a single context blob.

    Same format the legacy Strands path used, so the prompt text the
    model sees on turn 1 is unchanged.
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

    _ensure_kiro_binary_ready()
    apply_scope(caller_id, workspace_id)

    # Per-invocation HOME. Rewrites the custom agent config + prompt on
    # every invoke so prompt edits take effect without a container
    # restart; never touches Kiro-owned sessions/cli/.
    saved_uuid = ensure_kiro_home(
        system_prompt_src=default_system_prompt_src(),
        meta_agent_dir=_META_AGENT_DIR,
        caller_id=caller_id,
        workspace_id=workspace_id,
        model_id=model_id,
        home_root=_KIRO_HOME,
        persist_root=_KIRO_PERSIST,
    )

    client = KiroACPClient(
        binary=_KIRO_BINARY,
        kiro_home=_KIRO_HOME,
        api_key=_KIRO_API_KEY,
        agent_name="meta-agent",
        trust_all_tools=True,
        # Kiro stores its SQLite DB and runtime assets under $XDG_DATA_HOME
        # (default $HOME/.local/share). On /mnt/kiro (NFS-backed
        # sessionStorage) SQLite file locks are unreliable — we saw
        # "database is locked" on the second turn. Redirecting to /tmp
        # keeps SQLite on local ext4 while ~/.kiro/sessions/cli/ (append-
        # only JSONL files, no locks) remains on the persistent mount.
        extra_env={"XDG_DATA_HOME": "/tmp/kiro-xdg"},
    )
    mapper = ACPToSSEMapper()

    try:
        await client.start()

        session_id, is_new = await client.ensure_session(
            saved_uuid=saved_uuid,
            cwd=_KIRO_HOME,
            mcp_servers=[],
        )
        # Save the uuid on the first turn, or re-pin defensively if
        # session/load somehow returned a different id.
        if is_new or (saved_uuid and session_id != saved_uuid):
            save_kiro_session_uuid(session_id, persist_root=_KIRO_PERSIST)

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
            clear_kiro_session(persist_root=_KIRO_PERSIST)
        yield json.dumps({"__error": f"kiro_acp_error: {e.message}"})
    except Exception as e:  # noqa: BLE001
        log.exception("Meta-Agent invoke failed")
        yield json.dumps({"__error": f"meta_agent_error: {e!r}"})
    finally:
        await client.close()


async def _stream_with_keepalive(events_iter, mapper: ACPToSSEMapper):
    """Adapt an async iterator of ACP events into SSE frames with heartbeat.

    Iterates `events_iter` (from `KiroACPClient.prompt`), pipes each event
    through the mapper, and yields all resulting SSE frames. When the
    upstream is silent for `_KEEPALIVE_INTERVAL_S`, yields a keepalive
    sentinel so CloudFront doesn't close the origin stream.
    """
    ait = events_iter.__aiter__()
    while True:
        try:
            event = await asyncio.wait_for(
                ait.__anext__(), timeout=_KEEPALIVE_INTERVAL_S
            )
        except asyncio.TimeoutError:
            yield keepalive()
            continue
        except StopAsyncIteration:
            return
        for frame in mapper.translate(event):
            yield frame


if __name__ == "__main__":
    app.run()

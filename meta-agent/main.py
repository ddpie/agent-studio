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
    # Use Python logging (not print) because AgentCore's log pipeline hooks
    # the root logger via the OTEL distro. print() to stderr was being
    # silently dropped during cold start.
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
import sys
import time
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
_log_phase("bedrock_agentcore imported")

# Tool imports: every @tool function the Meta-Agent exposes. We do not
# instantiate a Strands Agent anymore, but we still need these callables
# so mcp_server.py can register them, and so tools._scope._caller_id
# plumbing keeps working unchanged.
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
_log_phase("kiro_adapter imported")

# One-time OS fingerprint so we know what kind of container kiro-cli-chat
# is being run on. Printed once at module import; useful when we see
# binary compatibility errors (glibc vs musl, EBADF on unexpected kernels).
try:
    import platform as _platform
    import subprocess as _subprocess_diag
    _os_release = ""
    try:
        with open("/etc/os-release") as _f:
            _os_release = _f.read().strip().replace("\n", " | ")
    except Exception:
        pass
    _ldd_v = ""
    try:
        _r = _subprocess_diag.run(
            ["ldd", "--version"], capture_output=True, text=True, timeout=3
        )
        _ldd_v = (_r.stdout or _r.stderr or "").splitlines()[0] if _r.stdout or _r.stderr else ""
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

# AgentCore's zip extractor (like Lambda's) does not preserve the execute
# bit from the archive. kiro-cli-chat lives in the zip with 0o755, but on
# disk after extraction it ends up 0o644, which makes
# create_subprocess_exec('.../kiro-cli-chat') fail with EACCES. We re-apply
# +x at import time. Idempotent and cheap (one stat + chmod if needed).
# NOTE: chmod of the 102MB kiro-cli-chat binary moved out of module-import
# path. On AgentCore's overlay filesystem the first stat/chmod of a freshly-
# extracted 100MB+ file can take 20+ seconds, pushing us past the 30s init
# budget. _ensure_kiro_binary_ready() does it lazily on the first invoke.
_kiro_binary_ready = False

# One-shot diagnostic flag for _probe_kiro_binary().
_kiro_probed = False


def _probe_kiro_binary() -> None:
    """Run kiro-cli-chat --version under several stdio configurations.

    Isolates 'Bad file descriptor (os error 9)' under `kiro-cli-chat acp`
    on AgentCore. Probe 1 uses sync subprocess.run (same as asyncio uses
    under the hood); probes 2-N use asyncio to match the actual code path.
    Runs once per container on first invoke.
    """
    import subprocess as _subp
    import asyncio as _asyncio

    env = {**_os.environ, "KIRO_API_KEY": _KIRO_API_KEY,
           "XDG_DATA_HOME": "/tmp/kiro-xdg-probe",
           "HOME": "/tmp/kiro-home-probe"}

    # --- Sync subprocess.run with --version
    try:
        r = _subp.run(
            [_KIRO_BINARY, "--version"],
            stdin=_subp.PIPE, stdout=_subp.PIPE, stderr=_subp.PIPE,
            timeout=10, env=env,
        )
        _boot_log.info(f"probe sync-version: exit={r.returncode} stdout={r.stdout[:120]!r} stderr={r.stderr[:120]!r}")
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe sync-version raised {type(e).__name__}: {e}")

    # --- Sync subprocess.run with `acp` — does subcommand itself crash?
    try:
        r = _subp.run(
            [_KIRO_BINARY, "acp", "--agent", "nonexistent"],
            input=b"",  # close stdin immediately — acp server should error cleanly
            stdout=_subp.PIPE, stderr=_subp.PIPE,
            timeout=10, env=env,
        )
        _boot_log.info(f"probe sync-acp-badagent: exit={r.returncode} stdout={r.stdout[:200]!r} stderr={r.stderr[:300]!r}")
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe sync-acp-badagent raised {type(e).__name__}: {e}")

    # --- Async subprocess with --version — matches how we spawn in acp_client
    async def _async_version():
        p = await _asyncio.create_subprocess_exec(
            _KIRO_BINARY, "--version",
            stdin=_asyncio.subprocess.PIPE,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await _asyncio.wait_for(p.communicate(), timeout=10)
            return p.returncode, out, err
        except _asyncio.TimeoutError:
            p.kill(); await p.wait()
            raise
    try:
        rc, out, err = _asyncio.get_event_loop().run_until_complete(_async_version())
        _boot_log.info(f"probe async-version: exit={rc} stdout={out[:120]!r} stderr={err[:200]!r}")
    except RuntimeError:
        # "cannot be called from a running event loop" — schedule on it
        try:
            loop = _asyncio.new_event_loop()
            rc, out, err = loop.run_until_complete(_async_version())
            loop.close()
            _boot_log.info(f"probe async-version (new-loop): exit={rc} stdout={out[:120]!r} stderr={err[:200]!r}")
        except Exception as e:  # noqa: BLE001
            _boot_log.warning(f"probe async-version (new-loop) raised {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe async-version raised {type(e).__name__}: {e}")


async def _probe_kiro_async() -> None:
    """Async-subprocess variant of the kiro probe.

    Sync subprocess.run succeeds on AgentCore for both --version and
    `acp --agent nonexistent`. The failure only surfaces with
    asyncio.create_subprocess_exec + ACP handshake, so we want to isolate
    whether it is: (a) asyncio vs sync, (b) ACP with real agent name, or
    (c) the JSON-RPC writes we send on stdin.
    """
    import asyncio as _a
    env = {**_os.environ, "KIRO_API_KEY": _KIRO_API_KEY,
           "XDG_DATA_HOME": "/tmp/kiro-xdg-probe",
           "HOME": "/tmp/kiro-home-probe"}

    # a) async + --version
    try:
        p = await _a.create_subprocess_exec(
            _KIRO_BINARY, "--version",
            stdin=_a.subprocess.PIPE, stdout=_a.subprocess.PIPE, stderr=_a.subprocess.PIPE,
            env=env,
        )
        out, err = await _a.wait_for(p.communicate(), timeout=10)
        _boot_log.info(f"probe async-version: exit={p.returncode} stdout={out[:120]!r} stderr={err[:200]!r}")
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe async-version raised {type(e).__name__}: {e}")

    # b) async + acp subcommand but stdin closed immediately
    try:
        p = await _a.create_subprocess_exec(
            _KIRO_BINARY, "acp", "--agent", "nonexistent",
            stdin=_a.subprocess.PIPE, stdout=_a.subprocess.PIPE, stderr=_a.subprocess.PIPE,
            env=env,
        )
        p.stdin.close()
        out, err = await _a.wait_for(p.communicate(), timeout=10)
        _boot_log.info(f"probe async-acp-stdin-closed: exit={p.returncode} stdout={out[:200]!r} stderr={err[:300]!r}")
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe async-acp-stdin-closed raised {type(e).__name__}: {e}")

    # Use the *real* HOME/XDG so agents/meta-agent.json actually resolves.
    # (ensure_kiro_home was just called by the outer invoke, so /mnt/kiro
    # is populated.)
    real_env = {**_os.environ, "KIRO_API_KEY": _KIRO_API_KEY,
                "XDG_DATA_HOME": "/tmp/kiro-xdg",
                "HOME": "/mnt/kiro"}

    async def _run_init_probe(label: str, agent_name: str, cfg_dir: str) -> None:
        try:
            p = await _a.create_subprocess_exec(
                _KIRO_BINARY, "acp", "--agent", agent_name, "--trust-all-tools",
                stdin=_a.subprocess.PIPE, stdout=_a.subprocess.PIPE, stderr=_a.subprocess.PIPE,
                env={**real_env, "HOME": cfg_dir},
            )
            p.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{}}}\n')
            await p.stdin.drain()
            try:
                line = await _a.wait_for(p.stdout.readline(), timeout=8)
                _boot_log.info(f"probe {label}: first-line={line[:300]!r}")
            except _a.TimeoutError:
                _boot_log.info(f"probe {label}: no line in 8s")
            p.stdin.close()
            try:
                out, err = await _a.wait_for(p.communicate(), timeout=5)
                _boot_log.info(f"probe {label}: exit={p.returncode} stderr={err[:400]!r}")
            except _a.TimeoutError:
                p.kill(); await p.wait()
                _boot_log.info(f"probe {label}: killed after stdin close")
        except Exception as e:  # noqa: BLE001
            _boot_log.warning(f"probe {label} raised {type(e).__name__}: {e}")

    # c) async + acp + real meta-agent config (with MCP server declared)
    await _run_init_probe("acp-real-meta", "meta-agent", "/mnt/kiro")

    # d) async + acp + a minimal agent config without MCP (isolates whether
    # MCP connection is the trigger)
    try:
        minimal = "/tmp/kiro-probe-minimal"
        import json as _jsonm
        _os.makedirs(f"{minimal}/.kiro/agents", exist_ok=True)
        with open(f"{minimal}/.kiro/agents/mini.json", "w") as f:
            _jsonm.dump({
                "name": "mini",
                "description": "",
                "prompt": "You are test.",
                "mcpServers": {},
                "tools": [],
                "toolAliases": {},
                "allowedTools": [],
                "resources": [],
                "hooks": {},
                "toolsSettings": {},
                "includeMcpJson": False,
                "model": "claude-haiku-4.5"
            }, f)
        await _run_init_probe("acp-minimal-noMcp", "mini", minimal)
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe acp-minimal setup failed: {e}")

    # e) async + acp + config with only STDIO MCP (python dummy server).
    # Isolates whether HTTP MCP specifically is the trigger.
    try:
        stdio_cfg = "/tmp/kiro-probe-stdio-mcp"
        _os.makedirs(f"{stdio_cfg}/.kiro/agents", exist_ok=True)
        import json as _jsonm
        with open(f"{stdio_cfg}/.kiro/agents/stdio.json", "w") as f:
            _jsonm.dump({
                "name": "stdio",
                "description": "",
                "prompt": "You are test.",
                "mcpServers": {
                    "echo": {
                        "command": "/bin/cat",
                        "args": [],
                        "env": {}
                    }
                },
                "tools": [],
                "toolAliases": {},
                "allowedTools": [],
                "resources": [],
                "hooks": {},
                "toolsSettings": {},
                "includeMcpJson": False,
                "model": "claude-haiku-4.5"
            }, f)
        await _run_init_probe("acp-stdio-mcp", "stdio", stdio_cfg)
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe acp-stdio-mcp setup failed: {e}")

    # f) Can Kiro even connect to our MCP HTTP server from within AgentCore?
    # Use curl via subprocess to see if loopback HTTP works at all.
    try:
        import subprocess as _subp
        r = _subp.run(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
             "-X", "POST", "-H", "Content-Type: application/json",
             "-H", "Accept: application/json, text/event-stream",
             "--data", '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}',
             "http://127.0.0.1:8765/mcp"],
            capture_output=True, text=True, timeout=5,
        )
        _boot_log.info(f"probe curl-local-mcp: http_status={r.stdout!r} stderr={r.stderr[:200]!r}")
    except Exception as e:  # noqa: BLE001
        _boot_log.warning(f"probe curl-local-mcp raised {type(e).__name__}: {e}")


def _ensure_kiro_binary_ready() -> None:
    """Ensure kiro-cli-chat is executable and launchable.

    AgentCore Runtime extracts the deployment zip to /var/task read-only.
    We measured two failure modes:
      - First deploy: execute bit (0o111) appeared to be missing at runtime
        even though the zip records 0o755.
      - Second deploy: chmod raises 'Operation not permitted' because
        /var/task is read-only.
    Fix: if the binary isn't already executable where it sits, copy it to
    /tmp (writable) and repoint _KIRO_BINARY at the copy. If it IS already
    executable we keep the /var/task path and skip the ~100MB copy.
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
            # Already executable in-place, nothing to do.
            _kiro_binary_ready = True
            return
        # Not executable; try chmod first (cheap if it works), fall back to
        # copying to a writable tmp location.
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

    # chmod kiro binary on first invoke rather than at import — see
    # _ensure_kiro_binary_ready for rationale.
    _ensure_kiro_binary_ready()

    # One-shot diagnostic: try several stdio configs against `kiro-cli-chat
    # --version`. Remove once the EBADF-on-AgentCore root cause is known.
    global _kiro_probed
    if not _kiro_probed:
        _kiro_probed = True
        _probe_kiro_binary()
        await _probe_kiro_async()

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
        # Kiro stores its SQLite DB and downloaded runtime assets (bun,
        # tui.js) under $XDG_DATA_HOME (defaulting to $HOME/.local/share).
        # On /mnt/kiro (NFS-backed sessionStorage) SQLite file locks are
        # unreliable — observed as "Failed to open database: database is
        # locked" on the second turn. Redirect XDG_DATA_HOME to local /tmp
        # so SQLite lives on ext4, while ~/.kiro/sessions/cli/ (pure
        # append-only JSONL files, no locks) stays on the persistent mount.
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

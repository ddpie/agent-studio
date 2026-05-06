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
from tools.create_agent import create_agent
# list_prompt_templates is deliberately NOT imported — the function
# still exists as a deprecated no-op shim in create_agent.py for any
# external caller linking to it, but the Meta-Agent's tool surface no
# longer advertises prompt templates (they're retired; see
# templates/prompt_templates.py). Removing it from ALL_TOOLS hides it
# from Kiro's tool-choice planner so the model never lists a tool it
# shouldn't use.
_log_phase("create_agent imported")

# Auto-publish tool catalog on startup (kept from the legacy path — the
# catalog is consumed by agents, not the Meta-Agent itself).
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
from tools.create_harness_agent import create_harness_agent
from tools.update_harness_agent import update_harness_agent
from tools.delete_harness_agent import delete_harness_agent
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
from tools.check_workspace_permissions import check_workspace_permissions

_log_phase("all tools imported")


from kiro_adapter.acp_client import ACPError, KiroACPClient
from kiro_adapter.kiro_home import (
    AGENT_EDIT_AGENT_NAME,
    DEFAULT_MODEL as KIRO_DEFAULT_MODEL,
    KIRO_HOME_DEFAULT,
    KIRO_PERSIST_ROOT,
    META_AGENT_NAME,
    SKILL_EDIT_AGENT_NAME,
    clear_kiro_session,
    default_agent_edit_prompt_src,
    default_skill_edit_prompt_src,
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
    create_harness_agent,
    list_agents,
    get_agent_detail,
    update_agent,
    update_harness_agent,
    delete_agent,
    delete_harness_agent,
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
    check_workspace_permissions,
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

# Kiro API key is required. Primary source is the Invoke Lambda payload
# (per-workspace, fetched from Secrets Manager just-in-time) so the key
# never sits in Runtime env / control-plane config. The env var is kept
# as an admin fallback for local dev / debugging only.
_KIRO_API_KEY_FALLBACK = os.environ.get("KIRO_API_KEY", "")

# Default model. Overridable per-invoke via payload.model_id or globally
# via env.
_KIRO_DEFAULT_MODEL = os.environ.get("AGENT_STUDIO_KIRO_MODEL", KIRO_DEFAULT_MODEL)

# Keep-alive cadence matching the legacy path (CloudFront 60s origin idle).
# Keep-alive cadence. CloudFront's origin readTimeout is 60s (our quota
# default), so we need comfortable headroom — a single missed frame on a
# flaky network, plus the 15s auto-continue silence threshold, both want
# to happen WELL before CloudFront starts the close. 15s = 4x safety vs
# the 60s ceiling and halves the idle window users notice as "stuck".
_KEEPALIVE_INTERVAL_S = 15.0

# Max rounds of auto-continue the supervisor fires per user turn when the
# model ends without emitting [[TASK_COMPLETE]]. 3 rounds is a hard stop
# against infinite loops when the model simply forgets the marker, while
# still covering the observed failure mode: Opus 4.6 occasionally cuts
# end_turn after 1-2 tool calls, another round usually finishes the task.
_AUTO_CONTINUE_MAX_ROUNDS = 3

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


# Payload `mode` → Kiro agent name. "skill_edit" swaps the meta-agent
# (full 34-tool surface) for the tool-less skill-edit agent whose job is
# to emit `__file_content:PATH` fenced blocks the browser captures
# directly. Unknown modes fall back to meta-agent, matching legacy
# callers that don't send the field at all.
_MODE_TO_AGENT = {
    "skill_edit": SKILL_EDIT_AGENT_NAME,
    "agent_edit": AGENT_EDIT_AGENT_NAME,
}


# Regex bank for parsing the human-readable `/usage` slash-command
# output. Kiro ships a structured AWS API under the hood
# (AmazonCodeWhispererService.GetUsageLimits) but the 36-char
# KIRO_API_KEY isn't a bearer token for that endpoint — only the CLI
# can auth to it, after doing its own internal exchange. So we shell
# out to `kiro-cli-chat chat --no-interactive "/usage"` and parse the
# TUI output. Format (verified 2026-04-25 against kiro 0.11.x):
#
#   Estimated Usage | resets on 2026-05-01 | KIRO POWER
#   Credits (204.98 of 10000 covered in plan)
#   ██████...████ 2%
#   Overages: Enabled  billed at $0.04 per request
#   Credits used: 0.00
#   Est. cost: $0.00 USD
#
# `(?s)` not used — each pattern is line-scoped after ANSI stripping.
_USAGE_ANSI = __import__("re").compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_USAGE_RE_HEADER = __import__("re").compile(
    r"resets on (\d{4}-\d{2}-\d{2})\s*\|\s*(.+?)\s*$",
    __import__("re").MULTILINE,
)
_USAGE_RE_CREDITS = __import__("re").compile(
    r"Credits\s*\(([\d.]+)\s*of\s*([\d.]+)\s*covered",
    __import__("re").IGNORECASE,
)
_USAGE_RE_OVERAGE = __import__("re").compile(
    r"Overages:\s*(Enabled|Disabled)(?:.*?\$([\d.]+)\s*per\s*request)?",
    __import__("re").IGNORECASE,
)
_USAGE_RE_OVERUSED = __import__("re").compile(
    r"Credits\s*used:\s*([\d.]+)", __import__("re").IGNORECASE,
)


async def _get_usage(api_key: str, region: str):
    """Short-circuit branch for `action=get_usage`.

    Spawns `kiro-cli-chat chat --no-interactive "/usage"` with the
    caller's KIRO_API_KEY (and AWS_REGION pinned so Kiro hits the right
    q.<region>.amazonaws.com endpoint). Strips ANSI and regex-parses
    the TUI into one of:

        {"__usage": {currentUsage, usageLimit, resetsOn, tier,
                     overagesEnabled, overageRate, overageUsed, currency}}

    or an error envelope:

        {"__error": "kiro_not_configured"}                (no key)
        {"__error": "usage_cli_failed", "detail": ...}    (CLI rc != 0)
        {"__error": "usage_parse_failed", "raw": ...}     (regex miss)

    The whole thing is one SSE frame — caller awaits `async for` and
    gets exactly one yield.
    """
    import re

    if not api_key:
        yield json.dumps({"__error": "kiro_not_configured"})
        return
    _ensure_kiro_binary_ready()

    env = os.environ.copy()
    env["HOME"] = _KIRO_HOME
    env["KIRO_API_KEY"] = api_key
    env["XDG_DATA_HOME"] = "/tmp/kiro-xdg"
    # Kiro's CLI picks the AWS region from AWS_REGION when it calls the
    # CodeWhisperer API internally. us-east-1 default matches the
    # AgentCore runtime region; eu-central-1 is the only other Kiro-
    # supported region at the time of writing. Anything else we reject
    # upstream in the Lambda so we don't need to defend here.
    if region:
        env["AWS_REGION"] = region

    try:
        proc = await asyncio.create_subprocess_exec(
            _KIRO_BINARY, "chat", "--no-interactive", "--trust-all-tools", "/usage",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # 360 MB kiro binary's first-ever exec can take 20-40 s (linker
        # + auth bootstrap). Subsequent invocations reuse OS page cache
        # and return in < 2 s. 60 s cap lets cold path complete.
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except asyncio.TimeoutError:
        log.warning("kiro-cli /usage timed out")
        yield json.dumps({"__error": "usage_cli_failed", "detail": "timeout"})
        return
    except Exception as e:  # noqa: BLE001
        log.exception("kiro-cli /usage spawn failed")
        yield json.dumps({"__error": "usage_cli_failed", "detail": repr(e)[:120]})
        return

    out = stdout_b.decode("utf-8", errors="replace")
    err = stderr_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        log.warning("kiro-cli /usage rc=%s stderr=%s", proc.returncode, err[:400])
        yield json.dumps({
            "__error": "usage_cli_failed",
            "detail": f"rc={proc.returncode}",
        })
        return

    # Kiro's TUI writes the `/usage` report to stderr when stdout isn't a
    # TTY (verified against kiro 0.11.x). stdout carries structured data
    # if and only if a machine-readable flag is set; under
    # `--no-interactive /usage` it stays empty. Concatenate both so a
    # future CLI version switching the stream doesn't silently break us.
    clean = _USAGE_ANSI.sub("", out + "\n" + err)
    m_credits = _USAGE_RE_CREDITS.search(clean)
    if not m_credits:
        log.warning("usage parse failed; raw=%s", clean[:400])
        # Don't echo stdout to the browser — keep it server-side.
        yield json.dumps({"__error": "usage_parse_failed"})
        return

    current = float(m_credits.group(1))
    limit = float(m_credits.group(2))

    reset_on = ""
    tier = ""
    m_hdr = _USAGE_RE_HEADER.search(clean)
    if m_hdr:
        reset_on = m_hdr.group(1)
        tier = m_hdr.group(2).strip()

    overages_enabled = False
    overage_rate = 0.0
    m_ov = _USAGE_RE_OVERAGE.search(clean)
    if m_ov:
        overages_enabled = m_ov.group(1).lower() == "enabled"
        if m_ov.group(2):
            try:
                overage_rate = float(m_ov.group(2))
            except ValueError:
                overage_rate = 0.0

    overage_used = 0.0
    m_ou = _USAGE_RE_OVERUSED.search(clean)
    if m_ou:
        try:
            overage_used = float(m_ou.group(1))
        except ValueError:
            overage_used = 0.0

    yield json.dumps({
        "__usage": {
            "currentUsage": current,
            "usageLimit": limit,
            "resetsOn": reset_on,
            "tier": tier,
            "overagesEnabled": overages_enabled,
            "overageRate": overage_rate,
            "overageUsed": overage_used,
            "currency": "USD",
        }
    }, ensure_ascii=False)


async def _list_models(api_key: str):
    """Short-circuit entrypoint branch for `action=list_models`.

    Tries the CLI path first (`kiro-cli-chat chat --list-models`) as the
    canonical catalog. Falls back to ACP `session/new` (whose response
    includes a `models` array) if the CLI doesn't print a parseable
    list. Both paths converge on the same `[{id, name}]` shape.

    Yields one `__models` SSE frame. Failures produce `__error` so the
    frontend falls back to its hard-coded list without the picker
    breaking.
    """
    import re

    # Kiro's CLI tacks on usage multipliers like "(2.2x credits)" after
    # the display name; the frontend also strips these defensively, but
    # stripping server-side keeps downstream log output / future API
    # consumers clean too.
    credits_re = re.compile(r"\s*\([^()]*\bcredits?\b[^()]*\)\s*$", re.IGNORECASE)

    if not api_key:
        yield json.dumps({"__error": "kiro_not_configured"})
        return
    _ensure_kiro_binary_ready()

    # --- CLI path ---------------------------------------------------------
    env = os.environ.copy()
    env["HOME"] = _KIRO_HOME
    env["KIRO_API_KEY"] = api_key
    env["XDG_DATA_HOME"] = "/tmp/kiro-xdg"

    models: list[dict[str, str]] = []
    cli_stdout = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            _KIRO_BINARY, "chat", "--list-models", "--no-interactive",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # First-exec cold path can take 20-40 s; warm path < 2 s.
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=60.0)
        cli_stdout = stdout_b.decode("utf-8", errors="replace")
        cli_stderr = stderr_b.decode("utf-8", errors="replace")
        if proc.returncode == 0:
            ansi_re = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
            seen: set[str] = set()
            for raw in cli_stdout.splitlines():
                line = ansi_re.sub("", raw).strip()
                if not line:
                    continue
                lower = line.lower()
                if lower.startswith(("available", "model", "id ", "====", "----")):
                    continue
                parts = line.split(None, 1)
                model_id = parts[0].strip()
                if " " in model_id or not any(c in model_id for c in ".-"):
                    continue
                if model_id in seen:
                    continue
                seen.add(model_id)
                label = parts[1].strip() if len(parts) > 1 else model_id
                label = credits_re.sub("", label).strip() or model_id
                models.append({"id": model_id, "name": label})
        else:
            log.warning("kiro-cli --list-models rc=%s stderr=%s",
                        proc.returncode, cli_stderr[:400])
    except asyncio.TimeoutError:
        log.warning("kiro-cli --list-models timed out; falling back to ACP")
    except Exception:  # noqa: BLE001
        log.exception("kiro-cli --list-models spawn failed; falling back to ACP")

    # --- ACP fallback -----------------------------------------------------
    # Only needed if the CLI didn't produce anything we could parse.
    # Uses the real meta-agent config (tool list + real model surface) so
    # the `models` array Kiro advertises isn't filtered by a tool-less
    # probe config. This does NOT rewrite the config — ensure_kiro_home is
    # already written per-invoke in the main path; we assume it's present.
    if not models:
        try:
            client = KiroACPClient(
                binary=_KIRO_BINARY,
                kiro_home=_KIRO_HOME,
                api_key=api_key,
                agent_name=META_AGENT_NAME,
                trust_all_tools=True,
                extra_env={"XDG_DATA_HOME": "/tmp/kiro-xdg"},
            )
            try:
                await client.start()
                raw_models = await client.list_models(cwd=_KIRO_HOME)
                for m in raw_models:
                    if isinstance(m, dict) and m.get("id"):
                        models.append({
                            "id": str(m["id"]),
                            "name": str(m.get("name") or m.get("displayName") or m["id"]),
                        })
                    elif isinstance(m, str):
                        models.append({"id": m, "name": m})
            finally:
                await client.close()
        except Exception:  # noqa: BLE001
            log.exception("ACP list_models fallback failed")

    if not models:
        log.warning("list_models produced 0 entries; cli_stdout=%s", cli_stdout[:400])
    yield json.dumps({"__models": models}, ensure_ascii=False)


@app.entrypoint
async def invoke(payload, context):
    # Yield a zero-byte heartbeat BEFORE doing anything slow. AgentCore's
    # InvokeAgentRuntime has a ~30 s first-byte cap. Kiro CLI's cold-start
    # (360 MB aarch64 binary + auth bootstrap) can eat that entire budget
    # before our action handlers even start. An immediate yield pins the
    # stream open so the long-running work runs on the caller's timeline,
    # not AgentCore's invoke timeout.
    yield ""

    prompt = payload.get("prompt", "Hello! I'm Agent Studio.")
    history = payload.get("history", [])
    images = payload.get("images") or []
    model_id = payload.get("model_id") or _KIRO_DEFAULT_MODEL
    caller_id = payload.get("caller_id", "unknown")
    workspace_id = payload.get("workspace_id", "")
    mode = (payload.get("mode") or "").strip()
    action = (payload.get("action") or "").strip()
    agent_name = _MODE_TO_AGENT.get(mode, META_AGENT_NAME)

    # Lightweight control-plane actions that don't need a full turn.
    # list_models: spawn Kiro, open a throwaway session, read the `models`
    # list the ACP server advertised, emit as a single __models frame.
    # Prefer the per-workspace key from payload (Invoke Lambda hydrates
    # it from Secrets Manager). Fall back to KIRO_API_KEY env only for
    # local dev / admin debugging.
    kiro_api_key = (payload.get("kiro_api_key") or _KIRO_API_KEY_FALLBACK or "").strip()

    if action == "list_models":
        async for frame in _list_models(kiro_api_key):
            yield frame
        return
    if action == "get_usage":
        # region passed through from the caller (Lambda) so we hit the
        # right q.<region>.amazonaws.com when Kiro's CLI makes its
        # internal GetUsageLimits call. Values are whitelisted in the
        # Lambda; we trust the payload here.
        kiro_region = (payload.get("kiro_region") or "us-east-1").strip()
        async for frame in _get_usage(kiro_api_key, kiro_region):
            yield frame
        return
    log.warning(
        "invoke: payload_keys=%s mode=%r model_id=%r -> agent=%r",
        sorted(payload.keys()), mode, model_id, agent_name,
    )

    if images:
        log.warning(
            "images forwarded from payload but the Kiro backend does not "
            "accept image prompts yet; dropping %d image(s)",
            len(images),
        )

    if not kiro_api_key:
        err = "kiro_not_configured"
        log.error(err)
        yield json.dumps({"__error": err})
        return

    _ensure_kiro_binary_ready()
    # Forward UI language ("zh" / "en") into the tool scope so
    # create_agent/update_agent pick the right BASE_GUIDELINES variant.
    # Value is also used by the auto-continue supervisor further down.
    invoke_lang = (payload.get("language") or payload.get("lang") or "").strip()
    apply_scope(caller_id, workspace_id, language=invoke_lang)

    # Per-invocation HOME. Rewrites both custom agent configs (meta-agent
    # and skill-edit) + prompts on every invoke so prompt edits take
    # effect without a container restart; never touches Kiro-owned
    # sessions/cli/. Returns the session uuid for `agent_name`, which we
    # then use to either session/load or session/new.
    saved_uuid = ensure_kiro_home(
        system_prompt_src=default_system_prompt_src(),
        skill_edit_prompt_src=default_skill_edit_prompt_src(),
        agent_edit_prompt_src=default_agent_edit_prompt_src(),
        meta_agent_dir=_META_AGENT_DIR,
        caller_id=caller_id,
        workspace_id=workspace_id,
        model_id=model_id,
        home_root=_KIRO_HOME,
        persist_root=_KIRO_PERSIST,
        agent_name=agent_name,
        creator_language=invoke_lang,
    )

    client = KiroACPClient(
        binary=_KIRO_BINARY,
        kiro_home=_KIRO_HOME,
        api_key=kiro_api_key,
        agent_name=agent_name,
        trust_all_tools=True,
        # Per-invocation model pin via `kiro-cli-chat acp --model`. This
        # replaces the earlier approach of rewriting the agent-config
        # JSON's `model` field on every turn — that path had a race
        # against concurrent invokes sharing the same HOME directory,
        # and was indirect (Kiro reads the config as a "default"). The
        # CLI flag is explicit per spawn and concurrency-safe.
        model_id=model_id,
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
            save_kiro_session_uuid(
                session_id, persist_root=_KIRO_PERSIST, agent_name=agent_name
            )

        user_text = _compose_user_text(
            prompt, _format_history(history), is_new
        )

        # Auto-continue supervisor. When the model ends a turn without
        # the [[TASK_COMPLETE]] marker AND it actually ran tools
        # (tool_events_seen > 0), we assume Kiro cut out prematurely
        # and silently send "Continue." (or locale-appropriate text) as
        # a fresh prompt on the same session. Capped at 3 rounds to
        # avoid infinite loops when the model forgets the marker.
        #
        # Only enabled for the full meta-agent turn. The tool-less
        # skill-edit/agent-edit sidebars have no tool use, their
        # "completion" is just the final assistant text — gating on
        # tool_events_seen > 0 naturally skips them.
        continue_prompt = "继续。" if invoke_lang.lower().startswith("zh") else "Continue."
        current_prompt = user_text
        prev_tool_events = 0  # captured from last round's state before it's replaced
        for round_idx in range(_AUTO_CONTINUE_MAX_ROUNDS + 1):
            if round_idx > 0:
                # Log AFTER capturing prev_tool_events from the prior
                # round's state but BEFORE constructing the fresh state.
                # The earlier version read from a just-reset state and
                # always logged 0.
                log.warning(
                    "auto-continue round %d/%d (prev_tool_events=%d)",
                    round_idx, _AUTO_CONTINUE_MAX_ROUNDS, prev_tool_events,
                )
                # Surface to frontend so the UI can show a badge.
                yield json.dumps(
                    {"__auto_continue": round_idx, "max": _AUTO_CONTINUE_MAX_ROUNDS},
                    ensure_ascii=False,
                )
            state = _TurnStreamState()
            async for frame in _stream_with_keepalive(
                client.prompt(session_id, current_prompt, timeout_s=_PROMPT_TIMEOUT_S),
                mapper,
                state,
            ):
                yield frame
            prev_tool_events = state.tool_events_seen

            if state.marker_seen:
                break
            if state.fake_tool_seen:
                # We already emitted tool_schema_missing and tore down
                # the turn in _stream_with_keepalive. Re-prompting would
                # just produce more fake-XML text on the same broken
                # Kiro session — the frontend retry handler is the only
                # correct recovery path.
                log.warning(
                    "fake-tool detected — skipping auto-continue, "
                    "frontend should retry with a fresh session_id"
                )
                break
            if state.tool_events_seen == 0:
                # Model answered in pure text (no tool use). Pretend
                # the marker was there — most likely the model just
                # forgot to emit it on a trivial chit-chat turn, and
                # auto-continuing an already-finished answer would
                # just produce noise.
                log.warning(
                    "turn ended without marker but no tool events — "
                    "skipping auto-continue"
                )
                break
            if round_idx == _AUTO_CONTINUE_MAX_ROUNDS:
                log.warning(
                    "auto-continue budget exhausted (%d rounds, "
                    "last tool_events=%d); surfacing partial response",
                    _AUTO_CONTINUE_MAX_ROUNDS, state.tool_events_seen,
                )
                break
            current_prompt = continue_prompt

    except ACPError as e:
        # Pull Kiro's `data` field into the error log. code=-32603 "Internal
        # error" means Kiro itself gave up on this turn (LLM timeout, model
        # refusal, overflow, etc.); the data blob is the only clue to which.
        log.error(
            "ACP error: method=%s code=%s message=%r data=%r",
            e.method, e.code, e.message, e.data,
        )
        if "Session not found" in (e.message or ""):
            clear_kiro_session(persist_root=_KIRO_PERSIST, agent_name=agent_name)
        # Don't leak `data` to the browser — it may contain prompt snippets
        # or model IDs. Surface the code + message only; operator digs into
        # CloudWatch for the data blob.
        yield json.dumps({"__error": f"kiro_acp_error: {e.message} (code {e.code})"})
    except Exception:  # noqa: BLE001
        # See the list_models branch — repr(e) leaks subprocess argv,
        # filesystem paths, and env fragments to the browser. Surface a
        # stable code only; the full traceback goes to CloudWatch.
        log.exception("Meta-Agent invoke failed")
        yield json.dumps({"__error": "meta_agent_error"})
    finally:
        await client.close()


# Completion marker the Meta-Agent prompt asks the model to emit as the
# last line of a finished response (see prompts/meta-agent.md →
# "Task Completion Marker"). _stream_with_keepalive watches every
# outgoing text chunk for it and flags the turn as complete; the
# auto-continue supervisor in `invoke` uses that flag to decide whether
# to re-prompt.
#
# We strip the marker from user-visible output by buffering the *trailing
# edge* of every text chunk — up to MARKER_BUFFER bytes — and only
# flushing characters that are definitely not part of a partial marker.
# A naive `text.replace(marker, "")` wouldn't catch the case where the
# marker is split across two streamed chunks (e.g. "...answer.\n[[TASK_" |
# "COMPLETE]]"), because by the time the second chunk arrives we've
# already sent the first half to the browser.
_COMPLETION_MARKER = "[[TASK_COMPLETE]]"

# Fingerprints of "the model printed a tool call as text" failure mode.
# When Kiro's ACP session hits the known tool-schema-missing bug (issue
# #7839 — affects ~40% of fresh sessions even on Kiro CLI 2.2.1), the
# model has no real MCP tool_use affordance so it falls back to whatever
# tool-use encoding it saw most during training. Three shapes observed:
#
#   1. `<invoke name="...">…</invoke>`       (legacy Anthropic XML)
#   2. `<tool_call>{"name":…}</tool_call>`   (ChatML / OpenAI)
#   3. `<function_calls>…</function_calls>`  (older Anthropic)
#
# These are pure text — nothing ever gets deployed. Detecting them
# server-side lets us abort the turn, surface a retriable error code,
# and let the frontend restart with a fresh session_id. That's strictly
# cheaper than shipping the dead text to the browser and hoping the
# user notices.
_FAKE_TOOL_MARKERS = (
    "<invoke name=",
    "<tool_call>",
    "<function_calls>",
)
# Longest marker length determines how many trailing bytes we must hold
# back in case one is split across stream chunks. Mirrors the completion-
# marker split-handling logic below.
_FAKE_TOOL_MAX_LEN = max(len(m) for m in _FAKE_TOOL_MARKERS)


class _TurnStreamState:
    """Carries per-turn stream metadata between _stream_with_keepalive
    invocations: was the marker seen, did the model actually run tools,
    how many frames did we relay.

    Kept as a plain class (not dataclass) so callers can mutate fields
    cheaply without triggering frozen-dataclass surprises.
    """

    __slots__ = ("marker_seen", "tool_events_seen", "frames_out",
                 "fake_tool_seen")

    def __init__(self) -> None:
        self.marker_seen = False
        self.tool_events_seen = 0
        self.frames_out = 0
        # Set when we detect a text-encoded tool call (model stuck in
        # fallback because Kiro dropped the tool schema this turn).
        self.fake_tool_seen = False


async def _stream_with_keepalive(
    events_iter,
    mapper: ACPToSSEMapper,
    state: _TurnStreamState,
):
    """Adapt an async iterator of ACP events into SSE frames with heartbeat.

    Iterates `events_iter` (from `KiroACPClient.prompt`), pipes each event
    through the mapper, and yields all resulting SSE frames. Side effects
    on `state`:

    - `marker_seen` flips to True once we've observed the completion
      marker in an outgoing text chunk. The marker itself is stripped
      from the stream.
    - `tool_events_seen` counts tool_call/tool_result frames so the
      supervisor can tell "trivial chit-chat turn" from "agent was
      clearly doing work when it got cut".
    - `frames_out` is total frames shipped to the caller (debugging).

    When upstream is silent for `_KEEPALIVE_INTERVAL_S`, yields a
    keepalive sentinel so CloudFront doesn't close the origin stream.

    Implementation note: we DO NOT wrap `ait.__anext__()` in `wait_for`.
    Python async generators that get cancelled mid-`__anext__` enter an
    unrecoverable "aclose-pending" state, so the next `__anext__` raises
    StopAsyncIteration immediately — one keepalive tick would kill the
    stream. Instead we forward events through a queue from a detached
    pump task; the keepalive timer races the queue.get(), which is a
    plain coroutine and safe to cancel repeatedly.
    """
    keepalive_count = 0
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    # Forward events_iter -> queue. A sentinel marks natural EOF; any
    # unexpected exception propagates as an (is_error, exc) tuple so the
    # consumer side raises it faithfully rather than silently hanging.
    #
    # The pump MUST guarantee _EOF (or an error packet) on any exit
    # path. If it dies silently, the consumer's `queue.get()` blocks
    # until `_PROMPT_TIMEOUT_S` (600s) while keepalives keep firing —
    # which looks exactly like the original pre-fix bug. Two places
    # this could leak before the outer `try/finally` existed:
    #   - `except Exception` misses `asyncio.CancelledError` on
    #     Python 3.8+ (BaseException subclass).
    #   - Any sync exception before the `try` obviously can't be
    #     caught at all, but the body is trivially async-only.
    # Route both via a plain `finally` that stuffs a sentinel onto the
    # queue, re-raising cancellation so the task's state reflects
    # reality for any outer await.
    _EOF = object()
    queue: asyncio.Queue = asyncio.Queue()

    async def _pump():
        eof_sent = False
        try:
            async for evt in events_iter:
                await queue.put(evt)
        except asyncio.CancelledError:
            # Outer consumer aborted us. Put EOF so a racing get()
            # wakes up cleanly instead of hanging, then re-raise so
            # the task is properly marked cancelled.
            try:
                queue.put_nowait(_EOF)
                eof_sent = True
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception as e:  # noqa: BLE001
            try:
                queue.put_nowait(("__pump_error__", e))
                eof_sent = True
            except Exception:  # noqa: BLE001
                pass
            return
        finally:
            if not eof_sent:
                # Natural exhaustion of `async for` lands here. Also
                # the safety net for any exit path that didn't already
                # enqueue something.
                try:
                    queue.put_nowait(_EOF)
                except Exception:  # noqa: BLE001
                    pass

    pump_task = asyncio.create_task(_pump(), name="acp-pump")
    # Trailing bytes we might need to hold back in case they're the start
    # of a split marker. Cap at marker_len-1 — at most that much of the
    # marker could have arrived without completing.
    pending_tail = ""
    marker_len = len(_COMPLETION_MARKER)
    # Running buffer of text already shipped this turn, capped at a bounded
    # window so we catch fake-tool markers that straddle chunk boundaries.
    # `_FAKE_TOOL_MAX_LEN - 1` would be the theoretical minimum; we keep a
    # few kilobytes so the detector can still fire even if the marker lands
    # in a chunk we've already shipped. Text frames are small enough that
    # the memory cost is negligible vs the user-safety win.
    _FAKE_SCAN_WINDOW = 4096
    recent_text = ""

    def _detect_fake_tool(chunk: str) -> str | None:
        """Return the fake-tool marker found (if any) once we've seen it.

        Scans a rolling window of recently-shipped text plus the new chunk.
        Caller must still ship the prefix up to the match — trimming the
        visible fake-text is done in `_stream_with_keepalive`'s main loop.
        """
        nonlocal recent_text
        combined = recent_text + chunk
        hit: str | None = None
        for m in _FAKE_TOOL_MARKERS:
            if m in combined:
                hit = m
                break
        # Keep the tail within window for the next call. Preserve enough
        # to detect a split marker across the boundary.
        if len(combined) > _FAKE_SCAN_WINDOW:
            recent_text = combined[-_FAKE_SCAN_WINDOW:]
        else:
            recent_text = combined
        return hit

    def _filter_text(chunk: str) -> str | None:
        """Strip completion marker from a text chunk, handling split-across-chunks.

        Returns the safe prefix to ship now; keeps any trailing bytes
        that could still be the start of a partial marker in
        pending_tail. Returns None when after filtering there's nothing
        to ship (chunk was entirely held for split-detection).
        """
        nonlocal pending_tail
        combined = pending_tail + chunk
        # Fully-formed marker: strip it, flag state.
        while _COMPLETION_MARKER in combined:
            state.marker_seen = True
            combined = combined.replace(_COMPLETION_MARKER, "", 1)
        # Hold back up to marker_len-1 bytes that could be the start of
        # the marker. Any whole-prefix that can't possibly be part of
        # the marker is safe to ship now.
        keep_back = 0
        # Find the longest suffix of `combined` that is a proper prefix
        # of the marker. Those bytes must stay in pending_tail.
        for k in range(min(marker_len - 1, len(combined)), 0, -1):
            if _COMPLETION_MARKER.startswith(combined[-k:]):
                keep_back = k
                break
        if keep_back:
            ship = combined[:-keep_back]
            pending_tail = combined[-keep_back:]
        else:
            ship = combined
            pending_tail = ""
        return ship if ship else None

    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=_KEEPALIVE_INTERVAL_S
                )
            except asyncio.TimeoutError:
                keepalive_count += 1
                log.warning(
                    "keepalive #%d emitted at +%.1fs (frames_out=%d)",
                    keepalive_count, loop.time() - t0, state.frames_out,
                )
                yield keepalive()
                continue
            if event is _EOF:
                # Flush any held bytes that turned out NOT to be a marker.
                if pending_tail and _COMPLETION_MARKER not in pending_tail:
                    state.frames_out += 1
                    yield pending_tail
                pending_tail = ""
                log.warning(
                    "stream ended at +%.1fs (frames_out=%d keepalives=%d "
                    "tool_events=%d marker_seen=%s)",
                    loop.time() - t0, state.frames_out, keepalive_count,
                    state.tool_events_seen, state.marker_seen,
                )
                return
            if isinstance(event, tuple) and len(event) == 2 and event[0] == "__pump_error__":
                raise event[1]
            for frame in mapper.translate(event):
                # sse_mapper emits two kinds of strings:
                #   1. raw text (model prose) — MIGHT contain the marker
                #   2. JSON control frames like `{"__tool": ...}` — never
                # Detect by leading `{` which is safe because prose chunks
                # Kiro ships are never JSON-object-shaped.
                if frame.startswith("{") and frame.endswith("}"):
                    # Tool markers count as "agent did real work".
                    if '"__tool"' in frame:
                        state.tool_events_seen += 1
                    state.frames_out += 1
                    yield frame
                    continue
                # Text chunk — run through marker filter.
                shipped = _filter_text(frame)
                if shipped is not None:
                    # Fake-tool-call detection: if this chunk (combined
                    # with the recent rolling window) contains a fake
                    # tool marker, the turn is effectively dead text.
                    # Truncate the ship to the point before the marker
                    # so the browser doesn't render the fake XML, emit
                    # a retriable error control frame, and bail out of
                    # the turn entirely. Auto-continue is skipped by
                    # the supervisor because tool_events_seen stays 0.
                    hit = _detect_fake_tool(shipped)
                    if hit:
                        safe_prefix = shipped.split(hit, 1)[0]
                        if safe_prefix:
                            state.frames_out += 1
                            yield safe_prefix
                        state.fake_tool_seen = True
                        err_payload = {
                            "__error": "tool_schema_missing",
                            "marker": hit,
                            "hint": (
                                "工具 schema 本轮未注入，请开启新会话后重试 / "
                                "Tool schema was not registered this turn; "
                                "please start a new session and retry."
                            ),
                        }
                        state.frames_out += 1
                        yield json.dumps(err_payload, ensure_ascii=False)
                        log.warning(
                            "fake-tool marker detected (%r) — bailing turn "
                            "after frames_out=%d",
                            hit, state.frames_out,
                        )
                        return
                    state.frames_out += 1
                    yield shipped
    finally:
        # Always tear down the pump task so its upstream async-generator
        # gets a clean aclose. Cancelling is safe even if _pump already
        # completed; in that case `task.cancel()` is a no-op.
        pump_task.cancel()
        try:
            await pump_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


if __name__ == "__main__":
    app.run()

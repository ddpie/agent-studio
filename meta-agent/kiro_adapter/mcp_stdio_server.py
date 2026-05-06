"""Stdio MCP server entry point spawned by Kiro CLI.

Invoked as `python3 -m kiro_adapter.mcp_stdio_server`. Kiro writes JSON-RPC
to our stdin and reads responses from our stdout. stderr is piped to Kiro,
which currently discards it — we route our own logging to a file under
/tmp so it can be fished out from CloudWatch after a run if needed.

Per-invocation scope
--------------------
Kiro spawns this subprocess every time an ACP session starts. The parent
(main.py) injects AGENT_STUDIO_CALLER_ID and AGENT_STUDIO_WORKSPACE_ID
into the subprocess env via the mcpServers config it writes. We read
them here and call `apply_scope()` before any tool can run.

Running this file directly (`python3 meta-agent/kiro_adapter/mcp_stdio_server.py`)
also works for local development.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# === Protect the stdio MCP channel from stray stdout writes ===
# Kiro frames MCP JSON-RPC over our stdin/stdout; ANY non-JSON-RPC byte on
# stdout kills the transport with "server may have written non-JSON-RPC
# output to stdout which caused the connection to close" and the session
# bails with code -32603.
#
# Strands' `Agent(...)` constructor (pulled in transitively by validate_agent)
# initializes a MetricsClient which logs to root-level stdout. OTEL
# auto-instrumentation likewise emits the occasional banner. We can't audit
# every transitive dependency. So: quarantine the original stdout fd on a
# private Python file object, and redirect everything that writes via
# `print()` / `sys.stdout` / fd 1 to stderr instead.
#
#   _MCP_STDOUT            → private file wrapping the REAL stdout fd
#                            (handed to FastMCP below via sys.stdout swap
#                            just before run_stdio_async)
#   sys.stdout / fd 1      → stderr (Kiro logs that stream; it doesn't
#                            parse it as JSON-RPC)
#
# Must happen BEFORE importing anything that might touch stdout at import
# time. asyncio/logging/os/sys are safe.
_real_stdout_fd = os.dup(1)
os.dup2(2, 1)  # fd 1 → stderr: stray write(1, ...) and print() go there
_MCP_STDOUT = os.fdopen(_real_stdout_fd, "w", buffering=1, encoding="utf-8")
# Python-level sys.stdout now also points at stderr so `print()` from any
# code that ran *before* this file (unlikely, but harmless) or after it
# also goes to stderr. We re-point sys.stdout at _MCP_STDOUT just around
# run_stdio_async() in main(), because FastMCP reads sys.stdout.buffer.
sys.stdout = os.fdopen(2, "w", buffering=1, encoding="utf-8", closefd=False)

# Make `tools.*` importable when Kiro spawns us with cwd=/mnt/kiro or any
# other directory. The binary's file path is stable regardless of cwd.
_THIS = Path(__file__).resolve()
_META_AGENT_DIR = _THIS.parent.parent  # .../meta-agent/
if str(_META_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_META_AGENT_DIR))

# Side-effect-free logging setup. We do NOT log to stderr because Kiro
# forwards the subprocess stderr to its own pipe where our formatter's
# ANSI codes and multi-line tracebacks can confuse the parser. File log
# is robust and survives the process.
_LOG_DIR = Path(os.environ.get("AGENT_STUDIO_MCP_LOG_DIR", "/tmp"))
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / f"mcp-stdio-{os.getpid()}.log"
logging.basicConfig(
    filename=str(_LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("meta_agent.mcp_stdio")


def main() -> None:
    log.info("mcp_stdio_server starting, pid=%d cwd=%s", os.getpid(), os.getcwd())
    log.info("python=%s", sys.executable)
    log.info("argv=%s", sys.argv)

    # Pick up scope from env. Kiro-spawned subprocess inherits whatever
    # env we set on the mcpServers entry. Fall back to empty strings so
    # the subprocess boots even if main.py forgot to pass them — tools
    # will then fail ownership checks, which is the right loud failure.
    caller_id = os.environ.get("AGENT_STUDIO_CALLER_ID", "")
    workspace_id = os.environ.get("AGENT_STUDIO_WORKSPACE_ID", "")
    # Forwarded from main.py via the mcpServers env in kiro_home.py.
    # create_agent / update_agent need it to pick zh vs en BASE_GUIDELINES.
    creator_language = os.environ.get("AGENT_STUDIO_CREATOR_LANGUAGE", "")

    # Import inside main so a misconfigured env doesn't explode at module
    # import time (and also so the file-based log captures import errors).
    try:
        from kiro_adapter.mcp_server import apply_scope, build_mcp_server
        # main module holds the ALL_TOOLS list. Using import-of-main is
        # awkward but matches what the legacy Strands path did — the list
        # is the authoritative tool registry.
        import main as _meta_main  # type: ignore
    except Exception:
        log.exception("import failed; exiting")
        raise

    apply_scope(caller_id, workspace_id, language=creator_language)
    log.info(
        "scope applied caller_id=%r workspace_id=%r tools=%d",
        caller_id, workspace_id, len(_meta_main.ALL_TOOLS),
    )

    srv = build_mcp_server(_meta_main.ALL_TOOLS)

    # Diagnostic: record every JSON-RPC request Kiro sends so we can tell
    # from CloudWatch whether a given session received a `tools/list`
    # (and thus the MCP schema made it to the model). Installed before
    # run_stdio_async via FastMCP's lowlevel server request handlers.
    # Uses module-level attribute probe so a FastMCP API bump doesn't
    # break the startup path; if the attribute moves we still boot.
    try:
        lowlevel = getattr(srv, "_mcp_server", None)
        if lowlevel is not None:
            orig_handlers = dict(getattr(lowlevel, "request_handlers", {}))
            log.info("mcp diag: %d request_handlers registered: %s",
                     len(orig_handlers), sorted(str(k) for k in orig_handlers))

            def _wrap(name, fn):
                async def _logged(req):
                    log.info("mcp request: %s", name)
                    try:
                        res = await fn(req)
                    except Exception:
                        log.exception("mcp request %s raised", name)
                        raise
                    log.info("mcp reply   : %s ok", name)
                    return res
                return _logged

            for key, fn in list(orig_handlers.items()):
                lowlevel.request_handlers[key] = _wrap(str(key), fn)
        else:
            log.warning("mcp diag: srv._mcp_server not found; cannot wrap handlers")
    except Exception:
        log.exception("mcp diag hook install failed (non-fatal)")

    # Re-point sys.stdout at the quarantined MCP fd only for the duration
    # of run_stdio_async. FastMCP reads sys.stdout.buffer inside its
    # setup, so it captures the REAL stdout there; meanwhile fd 1 still
    # points at stderr so any stray write(1, ...) from deep libs stays
    # harmless. Restore afterwards so our own logging / teardown doesn't
    # accidentally write JSON into the MCP pipe.
    saved_stdout = sys.stdout
    sys.stdout = _MCP_STDOUT
    try:
        asyncio.run(srv.run_stdio_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutdown requested")
    except Exception:
        log.exception("mcp_stdio_server crashed")
        raise
    finally:
        sys.stdout = saved_stdout
        log.info("mcp_stdio_server exiting")


if __name__ == "__main__":
    main()

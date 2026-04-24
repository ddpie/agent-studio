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

    apply_scope(caller_id, workspace_id)
    log.info(
        "scope applied caller_id=%r workspace_id=%r tools=%d",
        caller_id, workspace_id, len(_meta_main.ALL_TOOLS),
    )

    srv = build_mcp_server(_meta_main.ALL_TOOLS)

    try:
        asyncio.run(srv.run_stdio_async())
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("shutdown requested")
    except Exception:
        log.exception("mcp_stdio_server crashed")
        raise
    finally:
        log.info("mcp_stdio_server exiting")


if __name__ == "__main__":
    main()

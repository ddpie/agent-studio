"""Local HTTP MCP server exposing Meta-Agent tools to Kiro CLI.

Runs inside the AgentCore Runtime instance on 127.0.0.1:<port>. Kiro CLI is
configured via .kiro/settings/mcp.json to call this server.

Design notes
------------
- FastMCP accepts Strands' `DecoratedFunctionTool` objects verbatim via
  `add_tool()` — name, description, and signature are preserved. No rewrap
  needed. Verified against mcp==1.27.0.

- Per-invocation context (caller_id / workspace_id) is plumbed the same way
  the legacy Strands loop does today (main.py:466-477): by writing onto
  module-level variables inside tools/*.py. AgentCore Runtime is
  per-invocation, so a process-global scope is safe — only one user's session
  is active at a time. `apply_scope()` centralizes that plumbing.

- The server is started in a background asyncio task by main.py. It does not
  own the runloop. `serve_forever()` drives FastMCP's streamable-HTTP
  transport; shutdown happens when the task is cancelled.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

MCP_HOST_DEFAULT = "127.0.0.1"
MCP_PORT_DEFAULT = 8765
MCP_SERVER_NAME = "agent-studio-tools"


def build_mcp_server(
    tools: Iterable[Any],
    host: str = MCP_HOST_DEFAULT,
    port: int = MCP_PORT_DEFAULT,
) -> FastMCP:
    """Construct a FastMCP server with the given tool set registered.

    Args:
        tools: Iterable of Strands `@tool`-decorated callables. The name and
            description come from the function's `__name__` / `__doc__`, which
            is exactly how the SYSTEM_PROMPT already addresses them.
        host: Bind address. Defaults to loopback.
        port: TCP port.

    Returns:
        A configured FastMCP instance. Call `.streamable_http_app()` to get
        the Starlette app, or `.run_streamable_http_async()` to run it.
    """
    srv = FastMCP(
        name=MCP_SERVER_NAME,
        host=host,
        port=port,
        # stateless_http avoids per-client session bookkeeping on the MCP side.
        # Kiro opens a single MCP connection per ACP session; we don't need
        # FastMCP's own session layer.
        stateless_http=True,
        # Kill the duplicate-tool warning noise — we register 25 tools at once.
        warn_on_duplicate_tools=False,
    )

    count = 0
    for fn in tools:
        name = getattr(fn, "__name__", None) or getattr(fn, "name", None)
        if name is None:
            log.warning("skipping tool with no resolvable name: %r", fn)
            continue
        srv.add_tool(fn, name=name)
        count += 1

    log.info(
        "registered %d tools on MCP server at http://%s:%d/mcp", count, host, port
    )
    return srv


def apply_scope(caller_id: str, workspace_id: str) -> None:
    """Plumb per-invocation identity to the tool modules.

    Mirrors the legacy `main.py:466-477` assignment block. Called by the
    Meta-Agent entrypoint on every incoming invocation, before Kiro is asked
    to handle the turn.

    Tools that still read their own module-level `_caller_id` keep working
    unchanged; tools that switched to `tools._scope` also pick up the update
    via the shared module.
    """
    # Local imports so this module can be imported standalone (e.g. under
    # pytest without the full tools/ dependency tree).
    import tools._scope as _scope
    import tools.create_agent as _ca
    import tools.update_agent as _ua
    import tools.delete_agent as _da
    import tools.manage_secrets as _ms

    _scope._caller_id = caller_id
    _scope._workspace_id = workspace_id
    _ca._caller_id = caller_id
    _ca._workspace_id = workspace_id
    _ua._caller_id = caller_id
    _da._caller_id = caller_id
    _ms._caller_id = caller_id


async def serve_forever(srv: FastMCP) -> None:
    """Run the MCP server's HTTP transport until cancelled.

    Intended to be awaited as a background task from the Meta-Agent
    entrypoint. Shutdown happens when the task is cancelled.
    """
    await srv.run_streamable_http_async()

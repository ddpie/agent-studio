"""MCP server wrapping the Meta-Agent tools for Kiro CLI.

Build + scope helpers only. The transport (stdio) is driven by
`mcp_stdio_server.py`, which Kiro spawns as a subprocess (see
`.kiro/agents/meta-agent.json`'s `mcpServers` block).

Transport history: we originally ran FastMCP as an HTTP server on
127.0.0.1:8765 and configured Kiro with an HTTP MCP entry. AgentCore
Runtime's network sandbox blocks loopback TCP, so Kiro's HTTP MCP
client could never reach us — crashed with 'Bad file descriptor (os
error 9)' on the socket fd. Switching to stdio sidesteps the network
stack entirely: Kiro spawns us, we talk JSON-RPC over stdin/stdout.

FastMCP accepts Strands' `DecoratedFunctionTool` objects verbatim via
`add_tool()` — name, description, and signature are preserved. Verified
against mcp==1.27.0.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from mcp.server.fastmcp import FastMCP

log = logging.getLogger(__name__)

MCP_SERVER_NAME = "agent-studio-tools"


def build_mcp_server(tools: Iterable[Any]) -> FastMCP:
    """Construct a FastMCP server with the given tool set registered.

    Args:
        tools: Iterable of Strands `@tool`-decorated callables. The name
            and description come from the function's `__name__` / `__doc__`,
            which is exactly how the SYSTEM_PROMPT already addresses them.

    Returns:
        A configured FastMCP instance. Callers run the transport they need
        (we use `run_stdio_async()` from mcp_stdio_server.py).
    """
    srv = FastMCP(
        name=MCP_SERVER_NAME,
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

    log.info("registered %d tools on MCP server", count)
    return srv


def apply_scope(caller_id: str, workspace_id: str, language: str = "") -> None:
    """Plumb per-invocation identity to the tool modules.

    Mirrors the legacy Strands plumbing: writes onto module-level variables
    in tools/*.py so every tool call inside this process sees the current
    user. Called once per invocation in the Meta-Agent entrypoint AND once
    at mcp_stdio_server startup (since the stdio server is a separate
    subprocess and reads its scope from env).

    `language` is the creator's UI language ("zh" / "en" / ""); create_agent
    and update_agent use it to select the right BASE_GUIDELINES variant
    when assembling a freshly-authored sub-agent system_prompt. Empty
    string means "unknown" and downstream treats it as English.

    Tools that still read their own module-level `_caller_id` keep working
    unchanged; tools that switched to `tools._scope` pick up the update via
    the shared module.
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
    _scope._creator_language = language
    _ca._caller_id = caller_id
    _ca._workspace_id = workspace_id
    _ua._caller_id = caller_id
    _da._caller_id = caller_id
    _ms._caller_id = caller_id

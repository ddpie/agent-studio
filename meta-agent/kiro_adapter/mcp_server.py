"""Local HTTP MCP server exposing the 25 Meta-Agent tools.

Runs inside the AgentCore Runtime instance on 127.0.0.1:<port>. Kiro CLI is
configured via .kiro/settings/mcp.json to call this server.

Responsibilities:
- Register every @tool function from meta-agent/tools/ as an MCP tool.
- Inject per-invocation context (caller_id, workspace_id) into each tool call
  the same way main.py:466-477 does today.
- Return JSON-serialized tool output matching the existing @tool return format.

Not implemented yet — skeleton only.
"""

# TODO: pick one of
#   (a) mcp-python-sdk FastMCP (already in base/requirements.txt as `mcp==1.27.0`)
#   (b) a lighter aiohttp handler that speaks MCP's JSON-RPC-over-HTTP directly
# FastMCP is the safer default; revisit if cold-start budget needs trimming.

MCP_PORT_DEFAULT = 8765


def start_mcp_server(
    port: int = MCP_PORT_DEFAULT,
    caller_id: str = "",
    workspace_id: str = "",
) -> None:
    """Start the MCP server in a background thread.

    Args:
        port: Loopback port Kiro will connect to.
        caller_id: User identity from invoke payload, set on tools._scope.
        workspace_id: Workspace identity from invoke payload, set on tools._scope.
    """
    raise NotImplementedError

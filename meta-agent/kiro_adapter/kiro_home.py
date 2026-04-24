"""Materialize a per-invocation KIRO_HOME directory tree.

Kiro CLI persists session state under $HOME/.kiro/. To isolate per-user
invocations on a shared AgentCore Runtime instance, we override HOME to a
per-invocation temp dir and lay down the custom agent + prompt + MCP config
inside it before spawning kiro-cli.

Layout produced:

  /tmp/kiro_home_<uuid>/
    .kiro/
      agents/
        meta-agent.json          (custom agent config; tools, model, prompt ref)
      settings/
        mcp.json                 (global MCP server config, points at 127.0.0.1:<port>)
      sessions/cli/              (kiro writes session .json/.jsonl here — discarded after)
    prompts/
      meta-agent.md              (the 342-line Meta-Agent system prompt)

Not implemented yet — skeleton only.
"""


def build_kiro_home(mcp_port: int, model_id: str) -> str:
    """Create a fresh KIRO_HOME directory and write agent + mcp config.

    Args:
        mcp_port: Loopback port the local MCP server is listening on.
        model_id: Kiro model id to pin (e.g. "claude-opus-4.6").

    Returns:
        Absolute path to the new KIRO_HOME, to be passed as $HOME when spawning kiro-cli.
    """
    raise NotImplementedError


def cleanup_kiro_home(home_path: str) -> None:
    """Best-effort removal of a per-invocation KIRO_HOME."""
    raise NotImplementedError

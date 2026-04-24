"""Materialize a per-invocation KIRO_HOME directory tree.

Kiro CLI persists session state under $HOME/.kiro/. To isolate per-user
invocations on a shared AgentCore Runtime instance we override HOME to a
per-invocation temp dir and lay down the custom agent + prompt + MCP config
inside it before spawning kiro-cli-chat.

Layout produced:

  /tmp/kiro_home_<uuid>/
    .kiro/
      agents/
        meta-agent.json          (custom agent config; tools, model, prompt ref)
      sessions/cli/              (kiro writes session .json/.jsonl here; discarded on cleanup)
    prompts/
      meta-agent.md              (copy of meta-agent/prompts/meta-agent.md)

Tool allowlist (verified end-to-end on EC2 via ACP session/new):

- 4 Kiro built-ins: web_search, web_fetch, subagent, todo_list
- Full Meta-Agent MCP server: "@agent-studio-tools" (34 tools, wildcard import)

Intentionally omitted built-ins:
- shell / use_aws     — bypass the Meta-Agent's workspace permission checks
- read / write / code / grep / glob — Meta-Agent should not touch the runtime FS
- introspect / knowledge — Kiro-product-centric, not useful here

Built-in names above come from the ACP `_kiro.dev/commands/available` probe
against kiro-cli-chat 2.0.0 — they differ from the legacy names in the
`agent_config.json.example` shipped with the CLI (aws/report/thinking/todo/
delegate). Stick to the probed names.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

# Built-in Kiro tools exposed to the Meta-Agent. Keep this list minimal —
# every built-in is an extra way for the model to produce side effects outside
# the Meta-Agent's sanctioned 34-tool surface.
KIRO_BUILTIN_TOOLS = ["web_search", "web_fetch", "subagent", "todo_list"]

# MCP server name registered in the custom agent config. The FastMCP instance
# started by mcp_server.py uses the same name (MCP_SERVER_NAME).
MCP_SERVER_NAME = "agent-studio-tools"

# Name of the custom agent — passed to kiro-cli-chat as `--agent <name>`.
META_AGENT_NAME = "meta-agent"

DEFAULT_MODEL = "claude-opus-4.6"


def build_kiro_home(
    mcp_host: str,
    mcp_port: int,
    system_prompt_src: str,
    model_id: str = DEFAULT_MODEL,
    base_tmp_dir: str = "/tmp",
) -> str:
    """Create a fresh KIRO_HOME directory and write agent + prompt files.

    Args:
        mcp_host: Loopback host the local MCP server is listening on.
        mcp_port: Port the local MCP server is listening on.
        system_prompt_src: Absolute path to the source system-prompt markdown
            file (meta-agent/prompts/meta-agent.md in the deployment zip).
        model_id: Kiro model id to pin.
        base_tmp_dir: Parent directory under which the per-invocation HOME is
            created. AgentCore Runtime gives us /tmp; tests override this.

    Returns:
        Absolute path to the new KIRO_HOME, suitable for passing as $HOME when
        spawning kiro-cli-chat.
    """
    home = tempfile.mkdtemp(
        prefix=f"kiro_home_{uuid.uuid4().hex[:8]}_", dir=base_tmp_dir
    )
    home_path = Path(home)

    agents_dir = home_path / ".kiro" / "agents"
    sessions_dir = home_path / ".kiro" / "sessions" / "cli"
    prompts_dir = home_path / "prompts"
    agents_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Copy the system prompt so Kiro reads a path inside the isolated HOME.
    # A hard copy (not symlink) keeps cleanup simple and avoids any permission
    # quirks with Kiro's file-reader.
    prompt_dst = prompts_dir / "meta-agent.md"
    shutil.copyfile(system_prompt_src, prompt_dst)

    # Custom agent config.
    # Schema source: the agent_config.json.example shipped with kiro-cli, plus
    # the live probe of `_kiro.dev/commands/available` on 2.0.0.
    agent_config = {
        "name": META_AGENT_NAME,
        "description": "Agent Studio Meta-Agent (Kiro-backed)",
        "prompt": f"file://{prompt_dst}",
        "mcpServers": {
            MCP_SERVER_NAME: {
                "type": "http",
                "url": f"http://{mcp_host}:{mcp_port}/mcp",
            }
        },
        # Merge 4 built-ins with the full Meta-Agent MCP surface.
        # "@<server>" without /tool means import all tools from that server.
        "tools": [*KIRO_BUILTIN_TOOLS, f"@{MCP_SERVER_NAME}"],
        "toolAliases": {},
        # Empty allowedTools means "ask before each tool call" in interactive
        # mode; for the headless ACP path we rely on --trust-all-tools passed
        # to kiro-cli-chat. Leave the list empty here so the config stays
        # honest about intent (everything listed in `tools` is callable).
        "allowedTools": [],
        "resources": [],
        "hooks": {},
        "toolsSettings": {},
        # Do not auto-import the example agent's mcp.json — we declare ours
        # inline. Prevents surprise merges from any stray .kiro/settings/mcp.json
        # that may exist on the runtime filesystem.
        "includeMcpJson": False,
        "model": model_id,
    }
    agent_path = agents_dir / f"{META_AGENT_NAME}.json"
    with agent_path.open("w", encoding="utf-8") as f:
        json.dump(agent_config, f, indent=2, ensure_ascii=False)

    return str(home_path)


def cleanup_kiro_home(home_path: str) -> None:
    """Best-effort removal of a per-invocation KIRO_HOME.

    Swallows errors — the directory lives under /tmp and will be reaped by the
    runtime eventually even if shutil.rmtree fails on some edge case.
    """
    if not home_path or not os.path.isdir(home_path):
        return
    shutil.rmtree(home_path, ignore_errors=True)

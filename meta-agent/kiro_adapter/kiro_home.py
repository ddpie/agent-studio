"""Materialize the Kiro HOME directory tree for a Meta-Agent conversation.

AgentCore Runtime exposes a per-conversation persistent mount via
`filesystemConfigurations.sessionStorage` (scoped by `runtimeSessionId`, up to
8h, 1GB, replicated across microVMs). We point Kiro at this mount so its
native `session/load` can resume a previous turn with full tool_use /
tool_result history instead of rebuilding context from a replayed string.

Process (per invocation):

  1. AgentCore routes by runtimeSessionId → the same `/mnt/kiro` directory
     is restored regardless of which microVM handles this call.
  2. Meta-Agent entrypoint calls `ensure_kiro_home()`:
       - if `/mnt/kiro/.kiro/...` already exists → this is a follow-up turn,
         keep everything, just return the saved `kiro_session_uuid` (may be
         None if first turn crashed before saving one).
       - otherwise → lay down a fresh tree (agents/meta-agent.json,
         prompts/meta-agent.md, sessions/cli/, etc.) and return None.
  3. ACP client:
       - uuid is None → send `session/new`, then `save_kiro_session_uuid()`.
       - uuid is not None → send `session/load <uuid>`.

Layout inside the AgentCore-managed mount:

  /mnt/kiro/
    .kiro/
      agents/meta-agent.json          (custom agent config)
      sessions/cli/<uuid>.{json,jsonl}  (Kiro-owned session state)
    prompts/meta-agent.md             (system prompt; rewritten every invoke
                                       so prompt bumps take effect)
    kiro_session.txt                  (uuid pointer for session/load)

Tool allowlist on the custom agent (verified on EC2 via ACP probe):

- 4 Kiro built-ins: web_search, web_fetch, subagent, todo_list
- Full Meta-Agent MCP server: "@agent-studio-tools" (all 34 tools, wildcard)

Intentionally omitted built-ins:
- shell / use_aws     — bypass the Meta-Agent's workspace permission checks
- read / write / code / grep / glob — Meta-Agent should not touch the FS
- introspect / knowledge — Kiro-product-centric, not useful here

Built-in names come from the live ACP `_kiro.dev/commands/available` probe
against kiro-cli-chat 2.0.0. They do NOT match the names in the
`agent_config.json.example` shipped with the CLI (aws/report/thinking/todo/
delegate are legacy aliases); stick to the probed names.

Caveats (from AgentCore docs):
- An agent-runtime version update wipes every session's filesystem. Every
  `deploy-agentcore.sh` run is such an update, so ongoing conversations
  will lose their Kiro sessions. Acceptable for demo; document elsewhere.
- 14 days of inactivity also wipes. Effectively "new conversation" on return.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def _os_env_get(key: str, default: str) -> str:
    """Small helper so the agent config literal stays readable."""
    return os.environ.get(key, default)

# AgentCore's sessionStorage mount point. Must match the `mountPath` passed
# in `filesystemConfigurations` on CreateAgentRuntime / UpdateAgentRuntime.
# Regex constraint: /mnt/<exactly-one-subdir>.
KIRO_HOME_DEFAULT = "/mnt/kiro"

# Built-in Kiro tools exposed to the Meta-Agent. Keep this minimal — each
# built-in is an extra side-effect path outside the sanctioned 34-tool MCP
# surface.
# TEMPORARILY disabled while we debug AgentCore spawn issues — see if the
# MCP-only config works first, then add built-ins back.
KIRO_BUILTIN_TOOLS: list[str] = []

# MCP server name registered in the custom agent config. Must match
# MCP_SERVER_NAME in mcp_server.py.
MCP_SERVER_NAME = "agent-studio-tools"

# Name of the custom agent, passed as `--agent <name>` to kiro-cli-chat.
META_AGENT_NAME = "meta-agent"

DEFAULT_MODEL = "claude-opus-4.6"

# File inside the mount that pins the Kiro-assigned session uuid across turns.
_SESSION_UUID_FILENAME = "kiro_session.txt"


def ensure_kiro_home(
    system_prompt_src: str,
    meta_agent_dir: str,
    caller_id: str,
    workspace_id: str,
    model_id: str = DEFAULT_MODEL,
    home_root: str = KIRO_HOME_DEFAULT,
    python_executable: str | None = None,
) -> str | None:
    """Populate the Kiro HOME mount, idempotently.

    Writes/overwrites the custom agent config and the prompt file on every
    call (cheap, and lets prompt bumps take effect without a container
    restart). Leaves existing sessions/cli/ untouched.

    Args:
        system_prompt_src: Absolute path to the source system-prompt markdown
            (meta-agent/prompts/meta-agent.md in the deployment zip).
        meta_agent_dir: Absolute path to the meta-agent package root, so the
            MCP subprocess can put it on sys.path via PYTHONPATH.
        caller_id: Per-invocation user identity, injected into the MCP
            subprocess env so tool ownership checks see the right user.
        workspace_id: Per-invocation workspace identity, same purpose.
        model_id: Kiro model id to pin.
        home_root: Absolute path to use as $HOME for kiro-cli-chat.
        python_executable: Python interpreter to spawn the MCP subprocess
            with. Defaults to the current interpreter.

    Returns:
        The previously-saved kiro session uuid if present (meaning this is
        a follow-up turn and the caller should `session/load` it); None if
        no uuid is saved yet (first turn → caller should `session/new`).
    """
    import sys as _sys
    python_executable = python_executable or _sys.executable

    home_path = Path(home_root)
    agents_dir = home_path / ".kiro" / "agents"
    sessions_dir = home_path / ".kiro" / "sessions" / "cli"
    prompts_dir = home_path / "prompts"

    agents_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir.mkdir(parents=True, exist_ok=True)

    # Copy the system prompt fresh each invocation. Prompt edits ship via
    # deploy-agentcore.sh, which also wipes sessionStorage — but if we ever
    # hot-swap prompts without a runtime update, this keeps the mount in
    # sync with the deployed source.
    prompt_dst = prompts_dir / "meta-agent.md"
    shutil.copyfile(system_prompt_src, prompt_dst)

    # Inline the prompt text. Kiro supports `file://` prompts, but on
    # AgentCore we observed agents with `file://` prompts failing at ACP
    # startup (Bad file descriptor) where an identically-shaped config
    # with an inline string prompt worked. Inlining costs ~20KB per
    # agent-config write; the file is still around for diffing.
    try:
        prompt_inline = prompt_dst.read_text(encoding="utf-8")
    except OSError:
        prompt_inline = "You are Agent Studio — a Meta-Agent that orchestrates AI agents."

    # Custom agent config. Uses stdio MCP transport — Kiro spawns our MCP
    # server as a subprocess with the given command/args/env and talks
    # JSON-RPC over the pipes. HTTP transport would be simpler but
    # AgentCore's sandbox blocks loopback TCP (see mcp_server.py header).
    agent_config = {
        "name": META_AGENT_NAME,
        "description": "Agent Studio Meta-Agent (Kiro-backed)",
        "prompt": prompt_inline,
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": python_executable,
                "args": ["-u", "-m", "kiro_adapter.mcp_stdio_server"],
                "env": {
                    # Put meta-agent/ on sys.path so `kiro_adapter` and
                    # `tools` imports resolve regardless of Kiro's cwd.
                    "PYTHONPATH": meta_agent_dir,
                    # Also need the base dependency layer (boto3, mcp, etc.)
                    # and the AWS envs that tools/*.py read at import time.
                    # Inherit the parent process's full env: Kiro preserves
                    # keys listed in our "env" dict but also merges the
                    # existing env by default, so we repeat the critical
                    # ones to be explicit.
                    "AGENT_STUDIO_REGION": _os_env_get("AGENT_STUDIO_REGION", ""),
                    "AGENT_STUDIO_ACCOUNT_ID": _os_env_get("AGENT_STUDIO_ACCOUNT_ID", ""),
                    "AGENT_STUDIO_S3_BUCKET": _os_env_get("AGENT_STUDIO_S3_BUCKET", ""),
                    "AGENT_STUDIO_MCP_GATEWAY_ID": _os_env_get("AGENT_STUDIO_MCP_GATEWAY_ID", ""),
                    "AGENT_STUDIO_CODE_INTERPRETER_ID": _os_env_get("AGENT_STUDIO_CODE_INTERPRETER_ID", ""),
                    "AGENT_STUDIO_BROWSER_ID": _os_env_get("AGENT_STUDIO_BROWSER_ID", ""),
                    # Scope for tool ownership checks, picked up by
                    # mcp_stdio_server -> apply_scope at startup.
                    "AGENT_STUDIO_CALLER_ID": caller_id,
                    "AGENT_STUDIO_WORKSPACE_ID": workspace_id,
                    # Keep telemetry off in the subprocess — the parent
                    # already emits spans; double instrumentation just
                    # doubles the log volume.
                    "AGENT_OBSERVABILITY_ENABLED": "false",
                    # Forward the runtime PATH so `python3` resolves; we
                    # don't yet know whether Kiro passes PATH through by
                    # default.
                    "PATH": _os_env_get("PATH", "/usr/bin:/bin"),
                },
            }
        },
        # Merge built-ins with the full MCP tool surface. "@<server>" with no
        # /tool segment imports every tool from that server.
        "tools": [*KIRO_BUILTIN_TOOLS, f"@{MCP_SERVER_NAME}"],
        "toolAliases": {},
        # allowedTools governs interactive auto-approval; for the headless
        # ACP path we rely on --trust-all-tools on kiro-cli-chat. Empty here
        # keeps intent honest.
        "allowedTools": [],
        "resources": [],
        "hooks": {},
        "toolsSettings": {},
        # Don't auto-merge any stray .kiro/settings/mcp.json on the runtime.
        "includeMcpJson": False,
        "model": model_id,
    }
    agent_path = agents_dir / f"{META_AGENT_NAME}.json"
    with agent_path.open("w", encoding="utf-8") as f:
        json.dump(agent_config, f, indent=2, ensure_ascii=False)

    return load_kiro_session_uuid(home_root)


def load_kiro_session_uuid(home_root: str = KIRO_HOME_DEFAULT) -> str | None:
    """Read the saved Kiro session uuid, if any.

    Returns None when the pointer file is missing, empty, or unreadable.
    """
    path = Path(home_root) / _SESSION_UUID_FILENAME
    try:
        uuid = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return uuid or None


def save_kiro_session_uuid(uuid: str, home_root: str = KIRO_HOME_DEFAULT) -> None:
    """Persist the Kiro session uuid for subsequent turns to `session/load`."""
    if not uuid:
        return
    path = Path(home_root) / _SESSION_UUID_FILENAME
    path.write_text(uuid, encoding="utf-8")


def clear_kiro_session(home_root: str = KIRO_HOME_DEFAULT) -> None:
    """Forget the saved Kiro session uuid.

    Called when `session/load` fails (e.g. the uuid on disk is stale after a
    runtime update, which wipes Kiro-owned session files but may race with
    our pointer). Next turn falls back to `session/new`.
    """
    path = Path(home_root) / _SESSION_UUID_FILENAME
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


# Absolute path inside the deployment zip where the prompt lives. Callers
# normally pass this to `ensure_kiro_home(system_prompt_src=...)`.
def default_system_prompt_src() -> str:
    """Locate meta-agent/prompts/meta-agent.md relative to this module."""
    return str(Path(__file__).resolve().parent.parent / "prompts" / "meta-agent.md")

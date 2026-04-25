"""Materialize the Kiro HOME tree and manage cross-turn session continuity.

Storage split (bisected the hard way on AgentCore):

  /tmp/kiro-home/   — Kiro's $HOME. Ephemeral, local ext4.
                      Holds .kiro/agents/*.json, .kiro/sessions/cli/*.
                      Must NOT be on NFS / sessionStorage: Kiro crashes
                      with 'Bad file descriptor (os error 9)' when $HOME
                      lives on the AgentCore NFS mount.
  /mnt/kiro/        — AgentCore sessionStorage. Persistent per
                      runtimeSessionId, replicated across microVMs,
                      wiped on agent-runtime version update.
                      Only holds `kiro_session.txt` (the uuid pointer)
                      so session/load can pick up where the previous
                      invocation left off.

Per-invocation flow:

  1. Meta-Agent entrypoint calls `ensure_kiro_home()`:
       - rewrites /tmp/kiro-home/.kiro/agents/meta-agent.json (cheap,
         picks up prompt/tool-list edits without a container restart)
       - leaves any existing /tmp/kiro-home/.kiro/sessions/cli/ alone
       - reads the uuid pointer from /mnt/kiro/kiro_session.txt
  2. ACP client:
       - uuid is None → send `session/new`, save the new uuid to
         /mnt/kiro/kiro_session.txt
       - uuid is present → send `session/load`; on failure clear the
         pointer and fall back to session/new with replayed history

Tool allowlist on the custom agent:

- 4 Kiro built-ins: web_search, web_fetch, subagent, todo_list
  (names come from the live ACP `_kiro.dev/commands/available` probe
  against kiro-cli-chat 2.0.0; NOT the legacy aws/report/thinking/todo/
  delegate aliases in the shipped agent_config.json.example)
- Full Meta-Agent MCP server: "@agent-studio-tools" (all 34 tools,
  wildcard), served over stdio from mcp_stdio_server.

Caveats (from AgentCore docs + bisect):
- An agent-runtime version update wipes every session's sessionStorage
  filesystem. Every deploy-agentcore.sh run is such an update, so
  ongoing conversations lose their saved session uuid and fall back
  to session/new + history replay on their next turn.
- 14 days of inactivity also wipes.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


def _os_env_get(key: str, default: str) -> str:
    """Small helper so the agent config literal stays readable."""
    return os.environ.get(key, default)

# Where kiro-cli-chat treats as $HOME. Intentionally NOT the AgentCore
# sessionStorage mount (/mnt/kiro): Kiro crashes with ConnectionReset /
# 'Bad file descriptor (os error 9)' when HOME is on NFS-backed storage.
# Bisect confirmed HOME=/mnt/kiro is the exclusive trigger — every other
# config difference (prompt size, MCP command, tool list) works fine.
# So we put Kiro HOME on local /tmp and cross-turn continuity comes from
# the persistent uuid file we mirror into sessionStorage below.
KIRO_HOME_DEFAULT = "/tmp/kiro-home"

# Separate path on AgentCore's sessionStorage mount, used only to hold
# small artifacts that legitimately need to survive across microVMs —
# currently just the kiro session uuid pointer so session/load can
# resume a conversation on a new container instance. Anything Kiro
# itself wants to persist stays local; if it gets lost, ensure_session()
# falls back to session/new and re-seeds history from the payload.
KIRO_PERSIST_ROOT = "/mnt/kiro"

# Built-in Kiro tools exposed to the Meta-Agent. Keep this minimal — each
# built-in is an extra side-effect path outside the sanctioned 34-tool MCP
# surface. Names come from the live ACP `_kiro.dev/commands/available`
# probe (web_search / web_fetch / subagent / todo_list), not the legacy
# aliases shipped in agent_config.json.example.
KIRO_BUILTIN_TOOLS = ["web_search", "web_fetch", "subagent", "todo_list"]

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
    persist_root: str = KIRO_PERSIST_ROOT,
    python_executable: str | None = None,
) -> str | None:
    """Populate the Kiro HOME and look up any saved session uuid.

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
            Defaults to /tmp/kiro-home (local, ephemeral). NOT
            /mnt/kiro — Kiro crashes with EBADF when HOME is on NFS.
        persist_root: Where to stash cross-invocation pointers (session
            uuid). Defaults to the AgentCore sessionStorage mount so it
            survives microVM transitions for the same runtimeSessionId.
        python_executable: Python interpreter to spawn the MCP subprocess
            with. Defaults to the current interpreter.

    Returns:
        The previously-saved kiro session uuid if present (meaning this
        is a follow-up turn and the caller should `session/load` it);
        None if no uuid is saved yet (first turn → caller should
        `session/new`).
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

    return load_kiro_session_uuid(persist_root)


def load_kiro_session_uuid(persist_root: str = KIRO_PERSIST_ROOT) -> str | None:
    """Read the saved Kiro session uuid, if any.

    Pointer file lives on the cross-invocation sessionStorage mount so
    the next Runtime microVM (for the same runtimeSessionId) can
    session/load. Returns None when the file is missing, empty, or
    unreadable.
    """
    path = Path(persist_root) / _SESSION_UUID_FILENAME
    try:
        uuid = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return uuid or None


def save_kiro_session_uuid(uuid: str, persist_root: str = KIRO_PERSIST_ROOT) -> None:
    """Persist the Kiro session uuid for subsequent turns to `session/load`."""
    if not uuid:
        return
    path = Path(persist_root)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    (path / _SESSION_UUID_FILENAME).write_text(uuid, encoding="utf-8")


def clear_kiro_session(persist_root: str = KIRO_PERSIST_ROOT) -> None:
    """Forget the saved Kiro session uuid.

    Called when `session/load` fails (e.g. the uuid on disk is stale
    after a runtime update or the Kiro-owned session files got lost
    because HOME is ephemeral). Next turn falls back to `session/new`.
    """
    path = Path(persist_root) / _SESSION_UUID_FILENAME
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

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
# `subagent` has been observed to spawn a Kiro worker that opens its own
# MCP stdio channel and occasionally stalls under fan-out workloads
# (e.g. "list agents, then query each one in parallel"). Disabled until
# we can confirm the root cause — the worker stall leaves the parent
# Kiro waiting for tool results that never come.
# `todo_list` was removed 2026-05-06 after a 12-run baseline found 100%
# correlation between `Creating task list` activity and create_skill /
# create_agent calls stalling (start event emitted, no matching end).
# Success rate with todo_list on: 2/12; without it: target ≥ 10/12.
# The Meta-Agent's 4-step create workflow is already fixed in the prompt
# (Understand → Propose → Confirm → create_agent) and needs no task
# tracker — see meta-agent.md §"Tool Calling Discipline".
KIRO_BUILTIN_TOOLS = ["web_search", "web_fetch"]

# MCP server name registered in the custom agent config. Must match
# MCP_SERVER_NAME in mcp_server.py.
MCP_SERVER_NAME = "agent-studio-tools"

# Name of the custom agent, passed as `--agent <name>` to kiro-cli-chat.
META_AGENT_NAME = "meta-agent"
# Stripped-down sibling agents used by the editor sidebars: no tools, shorter
# system prompts, same model. Both are emitted alongside the full meta-agent
# config so one deploy serves every mode.
#
# - skill-edit drives the `/skills/:id/edit` page's sidebar assistant. It
#   emits `__file_content:PATH` / `__file_edit:PATH` blocks the frontend
#   captures to rewrite skill files in-place.
# - agent-edit drives the `/agents/:id/edit` page's sidebar assistant. It
#   emits `__field_value:FIELD` blocks to rewrite agent fields
#   (system_prompt, tool_definitions, etc.) and `__field_value:skill:…` for
#   bound-skill file edits.
SKILL_EDIT_AGENT_NAME = "skill-edit"
AGENT_EDIT_AGENT_NAME = "agent-edit"

DEFAULT_MODEL = "claude-opus-4.6"

# File inside the mount that pins the Kiro-assigned session uuid across turns.
# Kept per-agent so the three conversation timelines (main chat, skill
# editor, agent editor) don't collide on a stale uuid when the user bounces
# between pages.
_SESSION_UUID_FILENAMES = {
    META_AGENT_NAME: "kiro_session.txt",
    SKILL_EDIT_AGENT_NAME: "kiro_session_skill_edit.txt",
    AGENT_EDIT_AGENT_NAME: "kiro_session_agent_edit.txt",
}
# Back-compat alias for callers that still reference the old constant.
_SESSION_UUID_FILENAME = _SESSION_UUID_FILENAMES[META_AGENT_NAME]


def _enumerate_mcp_tool_refs() -> list[str]:
    """Return explicit `@<server>/<tool>` refs for every Meta-Agent tool.

    Using a wildcard (`@<server>` or `"*"`) relies on Kiro's lazy tool
    resolver; per our own CloudWatch evidence + GitHub issue #7839 the
    resolver silently skips `tools/list` on about half of fresh sessions,
    leaving the Anthropic API `tools[]` array empty and the model falling
    back to text-mock tool calls. Enumeration forces eager registration.

    Read ALL_TOOLS from main.py; that list is the single source of truth
    for what the MCP stdio subprocess exposes, so this stays in lock-step
    automatically. Lazy-import to keep `ensure_kiro_home` callable from
    tests that don't have the full tools tree on sys.path.
    """
    try:
        import main as _meta_main  # type: ignore
        names: list[str] = []
        for fn in _meta_main.ALL_TOOLS:
            n = getattr(fn, "__name__", None) or getattr(fn, "name", None)
            if n:
                names.append(str(n))
        return [f"@{MCP_SERVER_NAME}/{n}" for n in names]
    except Exception:
        # Fallback: let Kiro resolve lazily. This keeps tests passing even
        # when main.py isn't importable (pytest without meta-agent on path).
        return [f"@{MCP_SERVER_NAME}"]


def ensure_kiro_home(
    system_prompt_src: str,
    meta_agent_dir: str,
    caller_id: str,
    workspace_id: str,
    model_id: str = DEFAULT_MODEL,
    home_root: str = KIRO_HOME_DEFAULT,
    persist_root: str = KIRO_PERSIST_ROOT,
    python_executable: str | None = None,
    skill_edit_prompt_src: str | None = None,
    agent_edit_prompt_src: str | None = None,
    agent_name: str = META_AGENT_NAME,
    creator_language: str = "",
) -> str | None:
    """Populate the Kiro HOME and look up any saved session uuid.

    Writes/overwrites all three custom-agent configs on every call:
      - `meta-agent`  — full 34-tool surface for the main chat
      - `skill-edit`  — tool-less, for /skills/:id/edit sidebar
      - `agent-edit`  — tool-less, for /agents/:id/edit sidebar
    Cheap, and lets prompt bumps take effect without a container restart.
    Leaves existing sessions/cli/ untouched.

    Args:
        system_prompt_src: Absolute path to the meta-agent system-prompt
            markdown (meta-agent/prompts/meta-agent.md in the deployment zip).
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
        skill_edit_prompt_src: Absolute path to the skill-edit prompt
            (meta-agent/prompts/skill-edit.md). Defaults to the sibling
            file of system_prompt_src.
        agent_edit_prompt_src: Absolute path to the agent-edit prompt
            (meta-agent/prompts/agent-edit.md). Defaults to the sibling
            file of system_prompt_src.
        agent_name: Which agent's saved session uuid to return. The file
            write step always emits BOTH agents; this only controls which
            uuid pointer the function looks up and hands back.

    Returns:
        The previously-saved kiro session uuid for ``agent_name`` if present
        (meaning this is a follow-up turn and the caller should
        `session/load` it); None if no uuid is saved yet (first turn →
        caller should `session/new`).
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

    # Copy both prompts fresh each invocation. Prompt edits ship via
    # deploy-agentcore.sh, which also wipes sessionStorage — but if we ever
    # hot-swap prompts without a runtime update, this keeps the mount in
    # sync with the deployed source.
    meta_prompt_dst = prompts_dir / "meta-agent.md"
    shutil.copyfile(system_prompt_src, meta_prompt_dst)

    if not skill_edit_prompt_src:
        skill_edit_prompt_src = str(
            Path(system_prompt_src).parent / "skill-edit.md"
        )
    skill_edit_dst = prompts_dir / "skill-edit.md"
    try:
        shutil.copyfile(skill_edit_prompt_src, skill_edit_dst)
    except FileNotFoundError:
        # Let callers fall back to a tiny inline default rather than crash
        # the runtime if the skill-edit prompt was somehow stripped from
        # the deployment zip.
        skill_edit_dst.write_text(
            "You are a skill file editor. Emit `__file_content:PATH` "
            "fenced blocks for file changes; no tools are available.",
            encoding="utf-8",
        )

    if not agent_edit_prompt_src:
        agent_edit_prompt_src = str(
            Path(system_prompt_src).parent / "agent-edit.md"
        )
    agent_edit_dst = prompts_dir / "agent-edit.md"
    try:
        shutil.copyfile(agent_edit_prompt_src, agent_edit_dst)
    except FileNotFoundError:
        agent_edit_dst.write_text(
            "You are the agent editor assistant. Emit `__field_value:FIELD` "
            "fenced blocks for field changes; no tools are available.",
            encoding="utf-8",
        )

    # Inline the prompt text. Kiro supports `file://` prompts, but on
    # AgentCore we observed agents with `file://` prompts failing at ACP
    # startup (Bad file descriptor) where an identically-shaped config
    # with an inline string prompt worked. Inlining costs ~20KB per
    # agent-config write; the file is still around for diffing.
    try:
        meta_prompt_inline = meta_prompt_dst.read_text(encoding="utf-8")
    except OSError:
        meta_prompt_inline = (
            "You are Agent Studio — a Meta-Agent that orchestrates AI agents."
        )
    try:
        skill_edit_prompt_inline = skill_edit_dst.read_text(encoding="utf-8")
    except OSError:
        skill_edit_prompt_inline = (
            "You are a skill file editor. Emit __file_content:PATH fenced "
            "blocks for file changes; no tools are available."
        )
    try:
        agent_edit_prompt_inline = agent_edit_dst.read_text(encoding="utf-8")
    except OSError:
        agent_edit_prompt_inline = (
            "You are the agent editor assistant. Emit __field_value:FIELD "
            "fenced blocks for field changes; no tools are available."
        )

    # Subprocess env. Must be built carefully: Kiro merges this dict over
    # the parent process env, so writing an empty string here *overrides*
    # a populated parent value. That bit us — tools read S3_BUCKET, got
    # "", and every S3 call failed with "Invalid bucket name \"\"". Only
    # forward keys the parent actually has set; let config.py's fallbacks
    # (STS for ACCOUNT_ID, region-based S3 bucket name) kick in otherwise.
    mcp_env: dict[str, str] = {
        # Put meta-agent/ on sys.path so `kiro_adapter` and `tools` imports
        # resolve regardless of Kiro's cwd.
        "PYTHONPATH": meta_agent_dir,
        # Scope for tool ownership checks, picked up by mcp_stdio_server
        # -> apply_scope at startup.
        "AGENT_STUDIO_CALLER_ID": caller_id,
        "AGENT_STUDIO_WORKSPACE_ID": workspace_id,
        # Creator's UI language ("zh" / "en"). create_agent/update_agent
        # use this to choose the BASE_GUIDELINES variant that gets
        # appended to the new agent's system_prompt. Empty string
        # is fine — downstream defaults to English.
        "AGENT_STUDIO_CREATOR_LANGUAGE": creator_language or "",
        # Keep telemetry off in the subprocess — the parent already emits
        # spans; double instrumentation just doubles the log volume.
        "AGENT_OBSERVABILITY_ENABLED": "false",
        # Forward the runtime PATH so `python3` resolves; we don't yet
        # know whether Kiro passes PATH through by default.
        "PATH": _os_env_get("PATH", "/usr/bin:/bin"),
    }
    # Forward AWS-resource envs only when the parent actually set them.
    # An empty "AGENT_STUDIO_S3_BUCKET" wins over config.py's fallback and
    # breaks every tool that touches S3; an unset key lets the fallback run.
    for _k in (
        "AGENT_STUDIO_REGION",
        "AGENT_STUDIO_ACCOUNT_ID",
        "AGENT_STUDIO_S3_BUCKET",
        "AGENT_STUDIO_MCP_GATEWAY_ID",
        "AGENT_STUDIO_MCP_GATEWAY_URL",
        "AGENT_STUDIO_CODE_INTERPRETER_ID",
        "AGENT_STUDIO_BROWSER_ID",
    ):
        _v = os.environ.get(_k, "")
        if _v:
            mcp_env[_k] = _v

    # Custom agent config. Uses stdio MCP transport — Kiro spawns our MCP
    # server as a subprocess with the given command/args/env and talks
    # JSON-RPC over the pipes. HTTP transport would be simpler but
    # AgentCore's sandbox blocks loopback TCP (see mcp_server.py header).
    meta_agent_config = {
        "name": META_AGENT_NAME,
        "description": "Agent Studio Meta-Agent (Kiro-backed)",
        "prompt": meta_prompt_inline,
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": python_executable,
                "args": ["-u", "-m", "kiro_adapter.mcp_stdio_server"],
                "env": mcp_env,
            }
        },
        # Tool surface. Documented options are:
        #   - built-in names (e.g. "web_search", "web_fetch")
        #   - "@<server>" for a whole MCP server (lazy wildcard)
        #   - "@<server>/<tool>" for a single tool
        #   - "*" for every available tool
        # We used the `@<server>` wildcard until 2026-05-06; in ~50% of
        # fresh sessions Kiro never issued `tools/list` to the stdio
        # MCP subprocess, leaving the Anthropic `tools[]` array empty and
        # the model falling back to ChatML `<tool_call>` text mocks.
        # Verified in our CloudWatch logs + Kiro GitHub issue #7839.
        #
        # We then tried `"*"` (#7839's suggested workaround). It halved
        # the fake-XML rate but injected every Kiro built-in (`fs_write`,
        # `shell`, `search`, generic AWS CLI), and the model promptly
        # used `fs_write` to drop a `review-triage-criteria.md` on local
        # disk instead of calling the Agent Studio `create_skill` MCP
        # tool. Net regression.
        #
        # Enumerating `@<server>/<tool>` for every tool forces eager
        # schema injection (no wildcard race) without pulling in generic
        # built-ins. `web_search` + `web_fetch` stay on from
        # KIRO_BUILTIN_TOOLS — those are intentional affordances.
        "tools": [*KIRO_BUILTIN_TOOLS, *_enumerate_mcp_tool_refs()],
        "toolAliases": {},
        # allowedTools governs interactive auto-approval; for the headless
        # ACP path we rely on --trust-all-tools on kiro-cli-chat. Empty here
        # keeps intent honest.
        "allowedTools": [],
        "resources": [],
        "hooks": {},
        # `agent_crew` (alias: delegate / subagent / agentCrew) is a
        # built-in multi-stage planner Kiro exposes by default. On
        # kiro-cli-chat 2.0.0 it panics with
        #   byte index N is not a char boundary
        # when the task description contains CJK characters (Rust str
        # byte slicing in agent_crew.rs). Empty availableAgents makes
        # the tool error out early instead of crashing Kiro.
        "toolsSettings": {
            "agentCrew": {"availableAgents": [], "trustedAgents": []},
        },
        # Don't auto-merge any stray .kiro/settings/mcp.json on the runtime.
        "includeMcpJson": False,
        "model": model_id,
    }
    (agents_dir / f"{META_AGENT_NAME}.json").write_text(
        json.dumps(meta_agent_config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Skill-edit sibling. Same model, same HOME/session-store shape, but
    # zero tools — the frontend provides all file context inline and
    # captures the model's `__file_content:` / `__file_edit:` fenced
    # output directly. No MCP server, no built-in Kiro tools; we don't
    # want web_search or subagent firing either. Without this isolation
    # Kiro reads the meta-agent system prompt ("you have 34 tools"), sees
    # the word "skill" in the user message, and races off to list_skills
    # / read_skill_file instead of just writing the requested file.
    skill_edit_config = {
        "name": SKILL_EDIT_AGENT_NAME,
        "description": "Agent Studio skill-file editor (tool-less)",
        "prompt": skill_edit_prompt_inline,
        "mcpServers": {},
        "tools": [],
        "toolAliases": {},
        "allowedTools": [],
        "resources": [],
        "hooks": {},
        "toolsSettings": {},
        "includeMcpJson": False,
        "model": model_id,
    }
    (agents_dir / f"{SKILL_EDIT_AGENT_NAME}.json").write_text(
        json.dumps(skill_edit_config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Agent-edit sibling. Same tool-less shape as skill-edit, different
    # prompt: the /agents/:id/edit sidebar owns its own fence dialect
    # (`__field_value:FIELD` blocks), and the prompt keeps that intact.
    agent_edit_config = {
        "name": AGENT_EDIT_AGENT_NAME,
        "description": "Agent Studio agent-config editor (tool-less)",
        "prompt": agent_edit_prompt_inline,
        "mcpServers": {},
        "tools": [],
        "toolAliases": {},
        "allowedTools": [],
        "resources": [],
        "hooks": {},
        "toolsSettings": {},
        "includeMcpJson": False,
        "model": model_id,
    }
    (agents_dir / f"{AGENT_EDIT_AGENT_NAME}.json").write_text(
        json.dumps(agent_edit_config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return load_kiro_session_uuid(persist_root, agent_name=agent_name)


def _uuid_path(persist_root: str, agent_name: str) -> Path:
    """Pick the pointer filename for the given agent.

    Each Kiro custom agent gets its own uuid pointer — meta-agent and
    skill-edit conversations are independent timelines, and sharing one
    pointer would make whichever ran most recently clobber the other.
    Unknown agent names fall back to the meta-agent filename so legacy
    callers still work.
    """
    filename = _SESSION_UUID_FILENAMES.get(
        agent_name, _SESSION_UUID_FILENAMES[META_AGENT_NAME]
    )
    return Path(persist_root) / filename


def load_kiro_session_uuid(
    persist_root: str = KIRO_PERSIST_ROOT,
    agent_name: str = META_AGENT_NAME,
) -> str | None:
    """Read the saved Kiro session uuid for the given agent, if any.

    Pointer file lives on the cross-invocation sessionStorage mount so
    the next Runtime microVM (for the same runtimeSessionId) can
    session/load. Returns None when the file is missing, empty, or
    unreadable.
    """
    try:
        uuid = _uuid_path(persist_root, agent_name).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return uuid or None


def save_kiro_session_uuid(
    uuid: str,
    persist_root: str = KIRO_PERSIST_ROOT,
    agent_name: str = META_AGENT_NAME,
) -> None:
    """Persist the Kiro session uuid for subsequent turns to `session/load`."""
    if not uuid:
        return
    root = Path(persist_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    _uuid_path(persist_root, agent_name).write_text(uuid, encoding="utf-8")


def clear_kiro_session(
    persist_root: str = KIRO_PERSIST_ROOT,
    agent_name: str = META_AGENT_NAME,
) -> None:
    """Forget the saved Kiro session uuid for the given agent.

    Called when `session/load` fails (e.g. the uuid on disk is stale
    after a runtime update or the Kiro-owned session files got lost
    because HOME is ephemeral). Next turn falls back to `session/new`.
    """
    try:
        _uuid_path(persist_root, agent_name).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


# Absolute path inside the deployment zip where the prompt lives. Callers
# normally pass this to `ensure_kiro_home(system_prompt_src=...)`.
def default_system_prompt_src() -> str:
    """Locate meta-agent/prompts/meta-agent.md relative to this module."""
    return str(Path(__file__).resolve().parent.parent / "prompts" / "meta-agent.md")


def default_skill_edit_prompt_src() -> str:
    """Locate meta-agent/prompts/skill-edit.md relative to this module."""
    return str(Path(__file__).resolve().parent.parent / "prompts" / "skill-edit.md")


def default_agent_edit_prompt_src() -> str:
    """Locate meta-agent/prompts/agent-edit.md relative to this module."""
    return str(Path(__file__).resolve().parent.parent / "prompts" / "agent-edit.md")

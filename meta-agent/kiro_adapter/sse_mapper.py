"""Map ACP notifications to the existing Agent Studio SSE wire format.

The frontend (frontend/src/lib/agentcore-client.ts) already parses the event
shapes the legacy Strands backend emits:

  <plain text>                              -> appended to assistant message
  {"__keepalive": true}                     -> frontend ignores, just keeps stream alive
  {"__tool": "start", "name": <str>}        -> renders a tool bubble in "running" state
  {"__tool": "result",
   "name": <str>,
   "input": <base64 JSON>,
   "output": <base64 text>}                 -> fills in the bubble's input/output
  {"__tool": "end", "name": <str>}          -> transitions the bubble to "done"

We preserve this protocol so the frontend needs zero changes. Input/output
stay base64-encoded (same reason as main.py:619/628 — the outer JSON can't
reliably hold nested JSON without escaping gymnastics).

ACP surface consumed (probed against kiro-cli-chat 2.0.0):

  session/update -> agent_message_chunk          text stream
  session/update -> tool_call                    per-tool start
  session/update -> tool_call_update             per-tool progress + completion
  _kiro.dev/session/update -> tool_call_chunk    duplicative; dropped
  _kiro.dev/metadata / commands/available        informational; dropped

Duplicate-event handling
------------------------
Kiro emits at least three events per tool invocation (tool_call_chunk,
tool_call, tool_call_update). The frontend SSE protocol only wants one
start / one result / one end per toolCallId, so the mapper keeps a
per-call state table:

  tool_call        -> emit __tool:start once (dedup on toolCallId)
  tool_call_update with status=completed -> emit __tool:result + __tool:end
  everything else for that toolCallId    -> drop

Output sizing
-------------
`rawOutput` can be huge (10 web_search results = ~15KB JSON). The legacy
backend caps tool output at ~5KB before base64 to keep the SSE stream
light; we match it. The cap is applied before base64 so the post-b64
payload stays under ~7KB per event.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from typing import Any

# Matches the caps main.py:621-625 enforces on the legacy path. `load_skill`
# is a special case there; we don't have that here since the tool is run by
# agents, not the Meta-Agent. Keep 5000 as the one-size-fits-all cap.
MAX_TOOL_OUTPUT_CHARS = 5000
TRUNCATION_SUFFIX = "\n... (truncated)"

# Keepalive payload emitted when the caller's pump detects an idle gap on
# the upstream. Format matches main.py:560.
KEEPALIVE_PAYLOAD = json.dumps({"__keepalive": True})


def keepalive() -> str:
    """Return the heartbeat payload; call when upstream has been silent."""
    return KEEPALIVE_PAYLOAD


class ACPToSSEMapper:
    """Stateful per-turn translator from ACP events to Agent Studio SSE.

    One mapper instance per `session/prompt` call. The per-tool state table
    is local to the instance, so a fresh mapper is needed for each turn to
    avoid leaking toolCallId state across turns.
    """

    def __init__(self) -> None:
        # toolCallId -> display name (the title we emitted on `start`).
        # Presence in the dict means `__tool:start` has already been sent
        # for that id; absence means it hasn't.
        self._started: dict[str, str] = {}

    def translate(self, event: dict[str, Any]) -> Iterable[str]:
        """Convert one ACP event into zero or more SSE lines.

        Returns an iterable (possibly empty) of strings ready to ship to
        the frontend. Strings are either raw text (assistant message
        chunks) or JSON control frames matching the shapes documented at
        the top of the module.
        """
        method = event.get("method", "")

        # Only session/update carries turn content. Kiro's _kiro.dev/*
        # notifications are informational and have no place in the SSE
        # wire format.
        if method != "session/update":
            return ()

        params = event.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate", "")

        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if content.get("type") != "text":
                return ()
            text = content.get("text") or ""
            # Empty chunks occasionally appear between segments; skip them so
            # the frontend doesn't append a no-op string.
            return (text,) if text else ()

        if kind == "tool_call":
            tool_id = update.get("toolCallId") or ""
            if not tool_id or tool_id in self._started:
                # Already announced; drop the duplicate.
                return ()
            name = _clean_tool_name(update.get("title") or update.get("kind") or "tool")
            self._started[tool_id] = name
            return (json.dumps({"__tool": "start", "name": name}, ensure_ascii=False),)

        if kind == "tool_call_update":
            tool_id = update.get("toolCallId") or ""
            status = update.get("status") or ""
            if not tool_id:
                return ()
            # Some runs send the first `tool_call` event only as
            # `tool_call_chunk` on the _kiro.dev namespace, and the regular
            # `tool_call` never arrives. Cover that by emitting `start`
            # lazily from the first update if we haven't already.
            if tool_id not in self._started:
                name = _clean_tool_name(update.get("title") or update.get("kind") or "tool")
                self._started[tool_id] = name
                start_frame = json.dumps({"__tool": "start", "name": name}, ensure_ascii=False)
            else:
                name = self._started[tool_id]
                start_frame = None

            if status != "completed":
                # Progress update; don't ship anything else per the wire
                # format. (The frontend has no "in-progress" event to
                # render, only start/result/end.)
                return (start_frame,) if start_frame else ()

            raw_input = update.get("rawInput")
            raw_output = update.get("rawOutput")
            input_b64 = _encode_b64_json(raw_input)
            output_b64 = _encode_b64_any(raw_output)
            result_frame = json.dumps(
                {
                    "__tool": "result",
                    "name": name,
                    "input": input_b64,
                    "output": output_b64,
                },
                ensure_ascii=False,
            )
            end_frame = json.dumps({"__tool": "end", "name": name}, ensure_ascii=False)
            self._started.pop(tool_id, None)
            frames = []
            if start_frame is not None:
                frames.append(start_frame)
            frames.extend([result_frame, end_frame])
            return tuple(frames)

        # Unknown sessionUpdate kind; be conservative and drop.
        return ()


def _encode_b64_json(value: Any) -> str:
    """Base64-encode a JSON-pretty rendition of `value`.

    Matches main.py:614-619: input side is JSON-stringified (pretty with
    indent=2 when dict). Returns empty string for None / empty dict.
    """
    if value is None:
        return ""
    if isinstance(value, dict) and not value:
        return ""
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        rendered = str(value)
    return base64.b64encode(rendered.encode("utf-8")).decode("ascii")


def _unwrap_mcp_envelope(value: Any) -> Any:
    """Peel Kiro's MCP envelope off a tool rawOutput, if present.

    When Kiro calls a tool exposed over the MCP stdio transport, `rawOutput`
    arrives wrapped in an FastMCP-generated structure like:

        {"items": [{"Json": {
            "content": [{"type": "text", "text": "<tool return str>"}],
            "structuredContent": {...},
            "isError": false,
        }}]}

    The original agent code (Strands path) returned the tool's own string
    verbatim. Downstream consumers — the frontend deploy hook's
    extractToolResults, ChatMessage, ToolCallDetails — all parse that string
    as JSON. The envelope breaks them all.

    Unwrap conservatively: only strip the wrapper when we see the exact
    FastMCP shape. Anything else falls through untouched.
    """
    if not isinstance(value, dict):
        return value
    items = value.get("items")
    if not isinstance(items, list) or not items:
        return value
    first = items[0]
    if not isinstance(first, dict):
        return value
    inner = first.get("Json")
    if not isinstance(inner, dict):
        return value
    # Prefer the text chunk — that's what the @tool function literally
    # returned. Fall back to structuredContent.result if content is empty.
    content = inner.get("content")
    if isinstance(content, list):
        for chunk in content:
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                text = chunk.get("text")
                if isinstance(text, str):
                    return text
    sc = inner.get("structuredContent")
    if isinstance(sc, dict) and "result" in sc:
        return sc["result"]
    return value


def _encode_b64_any(value: Any) -> str:
    """Base64-encode a textual rendition of `value`, with size cap.

    Matches main.py:620-628: output is capped at MAX_TOOL_OUTPUT_CHARS
    before base64; over-cap values get a "...(truncated)" suffix.
    Structured values (dict/list) are JSON-serialized; primitives coerce
    via str().
    """
    value = _unwrap_mcp_envelope(value)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        try:
            text = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    if not text:
        return ""
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + TRUNCATION_SUFFIX
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# Kiro's ACP `title` for an MCP-hosted tool is presentation-layer text, not
# the bare function name — e.g. "Running: @agent-studio-tools/update_agent".
# The frontend (useAgentDeploy.extractToolResults, ToolCallDetails, the
# ChatPanel tool-name matcher) has always indexed results by bare function
# name (update_agent, list_skills, ...). Normalize here so the wire format
# stays stable regardless of how Kiro chooses to label things.
_RUNNING_PREFIX = "Running: "


def _clean_tool_name(raw: str) -> str:
    """Strip Kiro's presentation prefixes from a tool title.

    Examples:
        "Running: @agent-studio-tools/update_agent" -> "update_agent"
        "@agent-studio-tools/list_skills"           -> "list_skills"
        "web_search"                                -> "web_search"
    """
    # Deferred import — mcp_server imports stuff (FastMCP) that the
    # sse_mapper tests don't want to pull in. At runtime the symbol is
    # already loaded by main.py before any event flows through here.
    from kiro_adapter.mcp_server import MCP_SERVER_NAME

    mcp_ns_prefix = f"@{MCP_SERVER_NAME}/"
    s = (raw or "").strip()
    if s.startswith(_RUNNING_PREFIX):
        s = s[len(_RUNNING_PREFIX) :].strip()
    if s.startswith(mcp_ns_prefix):
        s = s[len(mcp_ns_prefix) :]
    return s or "tool"

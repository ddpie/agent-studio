"""agent_caller — call another Agent Studio agent as a tool, via the A2A proxy.

The A2A proxy lambda is a streaming Node.js function fronted by CloudFront at
`/a2a/*`. It accepts JSON-RPC 2.0 `message/send` and `message/stream` methods
at `POST /a2a/agents/{agentId}` with `Authorization: Bearer <a2a-api-key>`.

Agents that have been linked to peers via the Meta-Agent `link_agent` tool
get two things injected by the Meta-Agent:

  - Env var `A2A_INVOKE_URL` — the CloudFront base URL (e.g.
    `https://xxx.cloudfront.net`). No trailing slash, no `/a2a` suffix.
  - Env var `AGENTS_TOOL_KEYS_JSON` — a JSON map of
    `{targetAgentId: a2aApiKey}`. One entry per linked peer.

The tool uses the synchronous `message/send` RPC (not streaming) because the
caller agent wants a single concatenated string back.
"""

TOOL_META = {
    "id": "agent_caller",
    "name": "Call Agent",
    "description": "Invoke a linked Agent Studio agent via the A2A protocol and return its response as plain text",
    "category": "agents",
}

TOOL_NAMES = "call_agent"

TOOL_CODE = '''
@tool
def call_agent(agent_id: str, prompt: str, session_id: str = "") -> str:
    """Send a prompt to another Agent Studio agent linked to this one and return its reply.

    Use this when the user's question is better handled by a specialised agent that
    has been linked to you via the Meta-Agent `link_agent` tool. The list of
    currently linked agents is described in your system prompt — do not guess
    agent IDs. If the target id is not in that list, return an explanatory error
    instead of calling.

    Args:
        agent_id: The target agent's runtime id (as described in your system prompt).
        prompt: The message to send to the target agent. Be concrete — this is a fresh conversation for the callee.
        session_id: Optional A2A contextId. Leave empty to start a new session; pass back the same id to continue.

    Returns:
        The callee agent's concatenated text response, or a JSON error string.

    Example:
        call_agent("myDataAnalyzer-abc", "Summarise yesterday's sales numbers")
    """
    import json
    import os
    import urllib.request
    import urllib.error
    import uuid as _uuid

    base_url = os.environ.get("A2A_INVOKE_URL", "").rstrip("/")
    keys_json = os.environ.get("AGENTS_TOOL_KEYS_JSON", "")
    if not base_url:
        return json.dumps({"error": "A2A_INVOKE_URL not set. Ask the user to re-run link_agent for this agent."})
    if not keys_json:
        return json.dumps({"error": "AGENTS_TOOL_KEYS_JSON not set. Ask the user to re-run link_agent for this agent."})
    try:
        keys_map = json.loads(keys_json)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"AGENTS_TOOL_KEYS_JSON is not valid JSON: {e}"})
    api_key = keys_map.get(agent_id)
    if not api_key:
        return json.dumps({
            "error": f"No A2A key configured for agent_id '{agent_id}'.",
            "known_agents": list(keys_map.keys()),
        })

    url = f"{base_url}/a2a/agents/{agent_id}"
    envelope = {
        "jsonrpc": "2.0",
        "id": _uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": _uuid.uuid4().hex,
                "parts": [{"kind": "text", "text": prompt}],
                "contextId": session_id or None,
            },
        },
    }
    body = json.dumps(envelope).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8")
        except Exception:
            pass
        return json.dumps({"error": f"A2A HTTP {e.code}", "detail": err_body[:500]})
    except urllib.error.URLError as e:
        return json.dumps({"error": f"A2A network error: {e.reason}"})
    except Exception as e:
        return json.dumps({"error": f"A2A call failed: {e}"})

    try:
        rpc = json.loads(payload)
    except json.JSONDecodeError:
        # Not JSON — return raw for visibility.
        return payload[:4000]

    if "error" in rpc:
        return json.dumps({"error": "A2A RPC error", "detail": rpc["error"]})

    result = rpc.get("result") or {}
    parts = result.get("parts") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and (p.get("kind") == "text" or p.get("type") == "text")]
    text = "".join(t for t in texts if t)
    if not text:
        # Fall back to the raw result so the caller has something to reason about.
        return json.dumps(result, ensure_ascii=False)[:4000]
    return text
'''

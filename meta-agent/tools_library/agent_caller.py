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

The tool uses `message/stream` (SSE) rather than the simpler `message/send`
(one-shot JSON) specifically to work around CloudFront's 60s idle timeout.
Target agents that take more than 60s to respond (e.g. analyzing a
1000-row JSON upload through several run_command cycles) trip the
timeout and the client sees a network error even though the backend
completed. The A2A proxy emits `: keepalive\\n\\n` comment lines every
15s on the `message/stream` path to keep the connection warm, and this
tool concatenates all incoming text/status events into a single reply
for the caller agent's LLM to reason about.
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
        "method": "message/stream",
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
    # CloudFront OAC → Lambda Function URL (AuthType=AWS_IAM, RESPONSE_STREAM)
    # requires `x-amz-content-sha256` in the request for SigV4 to validate
    # at the origin. Without it the origin returns InvalidSignatureException
    # and the request never reaches the a2a-proxy handler. See also
    # frontend/src/lib/agentcore-client.ts which does the same thing for
    # the /invoke/ path.
    import hashlib as _hashlib
    _body_sha256 = _hashlib.sha256(body).hexdigest()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "text/event-stream",
            "authorization": f"Bearer {api_key}",
            "x-amz-content-sha256": _body_sha256,
        },
    )
    # Stream-parse SSE — ignore `:`-prefixed comment lines (keepalive),
    # concatenate text from each `data:` JSON-RPC envelope. Timeout is
    # per-read rather than total; a valid target agent may legitimately
    # take 3+ min but should never be silent > 30s thanks to the proxy's
    # 15s keepalive tick. 60s gives generous jitter budget.
    texts = []
    last_error = None
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            # urllib reads can block indefinitely; we rely on the proxy's
            # keepalive + CloudFront's 60s idle to bound total time.
            # urllib returns a file-like; readline() gives us one SSE
            # line at a time.
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\\n").rstrip("\\r")
                if not line:
                    continue
                if line.startswith(":"):
                    # SSE comment (keepalive). Ignore.
                    continue
                if not line.startswith("data:"):
                    # Ignore event:/id:/retry: lines — we only consume data.
                    continue
                payload_str = line[5:].lstrip()
                if not payload_str:
                    continue
                try:
                    rpc = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                if "error" in rpc:
                    last_error = rpc["error"]
                    continue
                result = rpc.get("result") or {}
                kind = result.get("kind")
                if kind == "message":
                    for p in result.get("parts") or []:
                        if isinstance(p, dict) and (p.get("kind") == "text" or p.get("type") == "text"):
                            t = p.get("text") or ""
                            if t:
                                texts.append(t)
                elif kind == "status-update":
                    # Informational — tool progress. Skip.
                    pass
                elif kind == "final":
                    # End-of-stream marker from a2a-proxy. Stop reading.
                    break
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

    if last_error and not texts:
        return json.dumps({"error": "A2A RPC error", "detail": last_error})
    text = "".join(texts)
    if not text:
        return json.dumps({"error": "A2A call returned no text", "detail": last_error})
    return text
'''

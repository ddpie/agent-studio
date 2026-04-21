"""Fetch a webpage via AgentCore Browser (CDP)."""

TOOL_META = {
    "id": "fetch_webpage",
    "name": "Fetch Webpage",
    "description": "Fetch a URL via the AgentCore managed Browser and return text content.",
    "category": "web",
}

TOOL_NAMES = "fetch_webpage"

TOOL_CODE = '''
@tool
def fetch_webpage(url: str, max_length: int = 8000) -> str:
    """Fetch a webpage via AgentCore Browser and return the main text.

    Args:
        url: The URL to load (http/https only).
        max_length: Maximum characters to return. Default 8000.

    Returns:
        Page text, truncated. Errors surface as a plain message.
    """
    import os, json, time, boto3, websocket  # websocket-client

    if not url or not url.startswith(("http://", "https://")):
        return "Error: Invalid URL. Must start with http:// or https://"

    browser_id = os.environ.get("AGENT_STUDIO_BROWSER_ID")
    if not browser_id:
        return "Error: AGENT_STUDIO_BROWSER_ID not configured"

    region = os.environ.get("AGENT_STUDIO_REGION", "us-east-1")
    client = boto3.client("bedrock-agentcore", region_name=region)

    # Reuse an existing CDP connection across calls — start_browser_session
    # costs ~500ms-1.5s per call, which dominates latency for fast fetches.
    # Session stays warm for 1h; if the ws drops we transparently reopen.
    session_id = getattr(fetch_webpage, "_session_id", None)
    ws = getattr(fetch_webpage, "_ws", None)

    def _start_new():
        s = client.start_browser_session(
            browserIdentifier=browser_id,
            name="agentstudio-fetch",
            sessionTimeoutSeconds=3600,
            viewPort={"width": 1280, "height": 800},
        )
        w = websocket.create_connection(
            s["streams"]["automationStream"]["streamEndpoint"], timeout=30
        )
        fetch_webpage._session_id = s["sessionId"]
        fetch_webpage._ws = w
        return s["sessionId"], w

    if session_id is None or ws is None:
        try:
            session_id, ws = _start_new()
        except Exception as e:
            return f"Error: start_browser_session failed: {e}"

    def _send(method, params=None, _id=[1]):
        msg_id = _id[0]
        _id[0] += 1
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            raw = ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id:
                return data

    def _run():
        _send("Page.enable")
        _send("Page.navigate", {"url": url})
        time.sleep(3)
        resp = _send("Runtime.evaluate", {
            "expression": "document.body ? document.body.innerText : ''",
            "returnByValue": True,
            "awaitPromise": False,
        })
        return ((resp.get("result") or {}).get("result") or {}).get("value") or ""

    try:
        text = _run()
    except Exception:
        # Session likely expired / ws dropped — rebuild once.
        try:
            try:
                ws.close()
            except Exception:
                pass
            try:
                client.stop_browser_session(browserIdentifier=browser_id, sessionId=session_id)
            except Exception:
                pass
            session_id, ws = _start_new()
            text = _run()
        except Exception as e:
            fetch_webpage._session_id = None
            fetch_webpage._ws = None
            return f"Error: CDP interaction failed: {e}"

    import re
    text = re.sub(r"\\n{3,}", "\\n\\n", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if len(text) > max_length:
        text = text[:max_length] + f"\\n\\n... (truncated at {max_length} chars)"
    return text
'''

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

    try:
        sess = client.start_browser_session(
            browserIdentifier=browser_id,
            name="agentstudio-fetch",
            sessionTimeoutSeconds=180,
            viewPort={"width": 1280, "height": 800},
        )
    except Exception as e:
        return f"Error: start_browser_session failed: {e}"

    session_id = sess["sessionId"]
    cdp_url = sess["streams"]["automationStream"]["streamEndpoint"]

    try:
        ws = websocket.create_connection(cdp_url, timeout=30)
    except Exception as e:
        try:
            client.stop_browser_session(browserIdentifier=browser_id, sessionId=session_id)
        except Exception:
            pass
        return f"Error: CDP connect failed: {e}"

    def _send(method, params=None, _id=[1]):
        msg_id = _id[0]
        _id[0] += 1
        ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        while True:
            raw = ws.recv()
            data = json.loads(raw)
            if data.get("id") == msg_id:
                return data

    try:
        _send("Target.getTargets")
        _send("Page.enable")
        _send("Page.navigate", {"url": url})
        time.sleep(3)
        resp = _send("Runtime.evaluate", {
            "expression": "document.body ? document.body.innerText : ''",
            "returnByValue": True,
            "awaitPromise": False,
        })
        text = ((resp.get("result") or {}).get("result") or {}).get("value") or ""
    except Exception as e:
        text = f"Error: CDP interaction failed: {e}"
    finally:
        try:
            ws.close()
        except Exception:
            pass
        try:
            client.stop_browser_session(browserIdentifier=browser_id, sessionId=session_id)
        except Exception:
            pass

    import re
    text = re.sub(r"\\n{3,}", "\\n\\n", text)
    text = re.sub(r" {2,}", " ", text).strip()
    if len(text) > max_length:
        text = text[:max_length] + f"\\n\\n... (truncated at {max_length} chars)"
    return text
'''

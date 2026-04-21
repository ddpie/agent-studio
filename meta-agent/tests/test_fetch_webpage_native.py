"""Test fetch_webpage routes to Browser CDP with session reuse."""
import json
import sys
import types
from unittest.mock import MagicMock, patch


def _exec_tool_code(monkeypatch, *, set_browser_id: bool = True):
    if set_browser_id:
        monkeypatch.setenv("AGENT_STUDIO_BROWSER_ID", "br-test")
    monkeypatch.setenv("AGENT_STUDIO_REGION", "us-east-1")
    # Stub websocket before TOOL_CODE runs (it's imported inside the function).
    if "websocket" not in sys.modules:
        sys.modules["websocket"] = types.ModuleType("websocket")

    # test_validate_agent.py installs a stub "tools_library" module for its
    # own isolation — pop it so we can import the real sibling package here.
    for _mod in ("tools_library", "tools_library.fetch_webpage", "tools_library.registry"):
        if _mod in sys.modules and not getattr(sys.modules[_mod], "__file__", None):
            sys.modules.pop(_mod, None)

    from tools_library.fetch_webpage import TOOL_CODE
    ns: dict = {"tool": lambda f: f}
    exec(TOOL_CODE, ns)
    return ns["fetch_webpage"]


def _ws_mock(value: str):
    """Build a websocket mock whose recv() echoes the most recent id with value."""
    ws = MagicMock()
    last_id = {"n": 0}

    def _send(payload):
        last_id["n"] = json.loads(payload).get("id", 0)

    ws.send.side_effect = _send
    ws.recv.side_effect = lambda: json.dumps(
        {"id": last_id["n"], "result": {"result": {"value": value}}}
    )
    ws.close.return_value = None
    return ws


def test_fetch_webpage_happy_path(monkeypatch):
    fn = _exec_tool_code(monkeypatch)
    # Ensure module-level function attrs are clean (prior tests may leave them).
    if hasattr(fn, "_session_id"):
        fn._session_id = None
    if hasattr(fn, "_ws"):
        fn._ws = None

    ws_client = _ws_mock("Hello Example Domain")
    data_mock = MagicMock()
    data_mock.start_browser_session.return_value = {
        "sessionId": "sess-1",
        "streams": {"automationStream": {"streamEndpoint": "wss://example/automation"}},
    }
    data_mock.stop_browser_session.return_value = None

    with patch("boto3.client", return_value=data_mock), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws_client)}), \
         patch("time.sleep"):
        out = fn("https://example.com", max_length=100)
    assert "Hello Example Domain" in out


def test_fetch_webpage_reuses_session(monkeypatch):
    """Second call must NOT call start_browser_session again."""
    fn = _exec_tool_code(monkeypatch)
    fn._session_id = None
    fn._ws = None

    ws_client = _ws_mock("Body")
    data_mock = MagicMock()
    data_mock.start_browser_session.return_value = {
        "sessionId": "sess-reuse",
        "streams": {"automationStream": {"streamEndpoint": "wss://example/automation"}},
    }

    with patch("boto3.client", return_value=data_mock), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws_client)}), \
         patch("time.sleep"):
        fn("https://example.com/a")
        fn("https://example.com/b")

    assert data_mock.start_browser_session.call_count == 1, (
        "fetch_webpage should reuse the existing browser session across calls; "
        f"got {data_mock.start_browser_session.call_count} starts"
    )


def test_fetch_webpage_reconnects_on_ws_failure(monkeypatch):
    """If _run() raises (e.g. ws dropped), rebuild session + retry once."""
    fn = _exec_tool_code(monkeypatch)
    # Prime a stale session so the first _run() fails.
    stale_ws = MagicMock()
    stale_ws.send.side_effect = ConnectionError("broken pipe")
    stale_ws.close.return_value = None
    fn._session_id = "stale-sess"
    fn._ws = stale_ws

    fresh_ws = _ws_mock("recovered")
    data_mock = MagicMock()
    data_mock.start_browser_session.return_value = {
        "sessionId": "fresh-sess",
        "streams": {"automationStream": {"streamEndpoint": "wss://example/automation"}},
    }
    data_mock.stop_browser_session.return_value = None

    with patch("boto3.client", return_value=data_mock), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: fresh_ws)}), \
         patch("time.sleep"):
        out = fn("https://example.com")

    assert "recovered" in out
    assert data_mock.start_browser_session.call_count == 1


def test_fetch_webpage_errors_when_no_browser_id(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_BROWSER_ID", raising=False)
    fn = _exec_tool_code(monkeypatch, set_browser_id=False)
    # Fresh attrs to prevent leaking a warm session from another test.
    fn._session_id = None
    fn._ws = None
    out = fn("https://example.com")
    assert "BROWSER_ID" in out or "not configured" in out.lower()

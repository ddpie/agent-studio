"""Test fetch_webpage routes to Browser CDP."""
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


def test_fetch_webpage_happy_path(monkeypatch):
    fn = _exec_tool_code(monkeypatch)
    ws_client = MagicMock()
    # Track the id of the most recent send() so recv() echoes it back —
    # the TOOL_CODE's _send helper matches responses by id.
    _last_sent_id = {"n": 0}

    def _send(payload):
        _last_sent_id["n"] = json.loads(payload).get("id", 0)

    ws_client.send.side_effect = _send
    ws_client.recv.side_effect = lambda: json.dumps(
        {"id": _last_sent_id["n"], "result": {"result": {"value": "Hello Example Domain"}}}
    )
    ws_client.close.return_value = None

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


def test_fetch_webpage_errors_when_no_browser_id(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_BROWSER_ID", raising=False)
    fn = _exec_tool_code(monkeypatch, set_browser_id=False)
    out = fn("https://example.com")
    assert "BROWSER_ID" in out or "not configured" in out.lower()

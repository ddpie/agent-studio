"""Test the browser_use built-in tool (navigate / text / click / fill / eval / screenshot)."""
import json
import sys
import types
from unittest.mock import MagicMock, patch


# Stubs for the template import chain — mirrors test_run_command_native.py.
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "MODEL_ID": "mock-model",
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/test-role",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


def _ws_mock(value_by_method=None, default_value="ok"):
    """WebSocket mock whose response is keyed by the CDP method name."""
    value_by_method = value_by_method or {}
    ws = MagicMock()
    last = {"id": 0, "method": ""}

    def _send(payload):
        data = json.loads(payload)
        last["id"] = data.get("id", 0)
        last["method"] = data.get("method", "")

    def _recv():
        method = last["method"]
        if method == "Page.captureScreenshot":
            # Minimal 1x1 PNG data (not real, just non-empty base64).
            return json.dumps({"id": last["id"], "result": {"data": "iVBORw0KGgo="}})
        v = value_by_method.get(method, default_value)
        return json.dumps({"id": last["id"], "result": {"result": {"value": v}}})

    ws.send.side_effect = _send
    ws.recv.side_effect = _recv
    ws.close.return_value = None
    return ws


def _exec_builtin(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_BROWSER_ID", "br-test")
    monkeypatch.setenv("AGENT_STUDIO_REGION", "us-east-1")
    if "websocket" not in sys.modules:
        sys.modules["websocket"] = types.ModuleType("websocket")

    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE
    ns: dict = {"__name__": "builtin_tools_test"}
    exec(BUILTIN_TOOLS_CODE, ns)
    # Ensure no warm session leaks across tests.
    ns["browser_use"]._session_id = None
    ns["browser_use"]._ws = None
    return ns


def _patched_boto(s3_mock=None):
    """Return a side_effect for boto3.client that routes agentcore + s3."""
    agentcore_mock = MagicMock()
    agentcore_mock.start_browser_session.return_value = {
        "sessionId": "sess-x",
        "streams": {"automationStream": {"streamEndpoint": "wss://example/automation"}},
    }
    agentcore_mock.stop_browser_session.return_value = None

    def _client(name, *args, **kwargs):
        if name == "bedrock-agentcore":
            return agentcore_mock
        if name == "s3":
            return s3_mock or MagicMock()
        return MagicMock()

    return _client, agentcore_mock


def test_browser_use_navigate(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock()
    side, agentcore = _patched_boto()
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        out = ns["browser_use"]("navigate", url="https://example.com", wait_ms=0)
    assert "navigated to https://example.com" in out


def test_browser_use_text(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock({"Runtime.evaluate": "Hello page body"})
    side, _ = _patched_boto()
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        out = ns["browser_use"]("text")
    assert "Hello page body" in out


def test_browser_use_click_ok(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock({"Runtime.evaluate": "ok"})
    side, _ = _patched_boto()
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        out = ns["browser_use"]("click", selector="#submit", wait_ms=0)
    assert "clicked #submit" in out


def test_browser_use_click_not_found(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock({"Runtime.evaluate": "not_found"})
    side, _ = _patched_boto()
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        out = ns["browser_use"]("click", selector=".missing", wait_ms=0)
    parsed = json.loads(out)
    assert "not_found" in parsed.get("error", "")


def test_browser_use_fill(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock({"Runtime.evaluate": "ok"})
    side, _ = _patched_boto()
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        out = ns["browser_use"]("fill", selector="input[name=q]", value="hello")
    assert "filled" in out


def test_browser_use_eval(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock({"Runtime.evaluate": 42})
    side, _ = _patched_boto()
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        out = ns["browser_use"]("eval", expression="1+41")
    parsed = json.loads(out)
    assert parsed.get("value") == 42


def test_browser_use_screenshot_uploads_to_s3(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock()
    s3_mock = MagicMock()
    s3_mock.put_object.return_value = {}
    side, _ = _patched_boto(s3_mock=s3_mock)
    # The module-level _s3 was captured at exec time; patch it directly.
    ns["_s3"] = s3_mock
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        out = ns["browser_use"]("screenshot")
    assert "__S3_DOWNLOAD__" in out
    assert s3_mock.put_object.called
    put_kwargs = s3_mock.put_object.call_args.kwargs
    assert put_kwargs["ContentType"] == "image/png"


def test_browser_use_reuses_session(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    ws = _ws_mock()
    side, agentcore = _patched_boto()
    with patch("boto3.client", side_effect=side), \
         patch.dict(sys.modules, {"websocket": types.SimpleNamespace(create_connection=lambda *a, **kw: ws)}), \
         patch("time.sleep"):
        ns["browser_use"]("navigate", url="https://example.com/a", wait_ms=0)
        ns["browser_use"]("navigate", url="https://example.com/b", wait_ms=0)
    assert agentcore.start_browser_session.call_count == 1


def test_browser_use_rejects_unknown_action(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    out = ns["browser_use"]("teleport")
    parsed = json.loads(out)
    assert "unknown action" in parsed.get("error", "")


def test_browser_use_navigate_requires_https(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    out = ns["browser_use"]("navigate", url="ftp://nope")
    parsed = json.loads(out)
    assert "http" in parsed.get("error", "")


def test_browser_use_missing_browser_id(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_BROWSER_ID", raising=False)
    monkeypatch.setenv("AGENT_STUDIO_REGION", "us-east-1")
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE
    ns: dict = {"__name__": "builtin_tools_test"}
    exec(BUILTIN_TOOLS_CODE, ns)
    ns["browser_use"]._session_id = None
    ns["browser_use"]._ws = None
    out = ns["browser_use"]("navigate", url="https://example.com")
    parsed = json.loads(out)
    assert "BROWSER_ID" in parsed.get("error", "")

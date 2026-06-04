"""Test the browser_use built-in tool (navigate / text / click / fill / eval / screenshot).

Production architecture:
  - browser_use → _run_async → _do_async (in templates/agent_template_v2.py)
  - _do_async calls _get_page_async() to obtain a Playwright Page
  - _get_page_async caches the Page in _browser_state["page"]

These tests inject a pre-cached fake Page into _browser_state to skip the
BrowserClient + Playwright setup entirely. We test the action dispatch and
response shaping, not the AWS plumbing.
"""
import asyncio
import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


def _make_page_mock(*, inner_text="page body content", evaluate_value="ok"):
    """Build a Playwright-Page-shaped AsyncMock with sensible defaults.

    Override any method's return_value or side_effect on the returned object
    in a test for failure-path testing.
    """
    page = MagicMock()
    page.is_closed = MagicMock(return_value=False)
    page.goto = AsyncMock(return_value=None)
    page.inner_text = AsyncMock(return_value=inner_text)
    page.evaluate = AsyncMock(return_value=evaluate_value)
    page.click = AsyncMock(return_value=None)
    page.fill = AsyncMock(return_value=None)
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes")
    return page


def _exec_builtin(monkeypatch, page=None, s3_mock=None):
    """Execute BUILTIN_TOOLS_CODE in a fresh namespace with browser state pre-warmed.

    By preseeding _browser_state["page"], _get_page_async returns immediately
    without touching BrowserClient or playwright.async_api.
    """
    monkeypatch.setenv("AGENT_STUDIO_BROWSER_ID", "br-test")
    monkeypatch.setenv("AGENT_STUDIO_REGION", "us-east-1")

    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE
    ns: dict = {"__name__": "builtin_tools_test"}
    exec(BUILTIN_TOOLS_CODE, ns)

    # Inject fake page so _get_page_async hits the warm-cache branch.
    if page is None:
        page = _make_page_mock()
    ns["_browser_state"]["page"] = page

    # Replace the module-level _s3 client with a controllable mock.
    if s3_mock is not None:
        ns["_s3"] = s3_mock

    return ns


# ── Action dispatch ────────────────────────────────────────────────────────────

def test_browser_use_navigate(monkeypatch):
    page = _make_page_mock()
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("navigate", url="https://example.com", wait_ms=0)
    assert "navigated to https://example.com" in out
    page.goto.assert_awaited_once()
    assert page.goto.await_args.args[0] == "https://example.com"


def test_browser_use_navigate_rejects_non_http():
    """navigate requires http(s) URL — pre-flight check, no page interaction."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        ns = _exec_builtin(monkeypatch)
        out = ns["browser_use"]("navigate", url="ftp://example.com")
        parsed = json.loads(out)
        assert "http(s) url" in parsed["error"]
    finally:
        monkeypatch.undo()


def test_browser_use_text(monkeypatch):
    page = _make_page_mock(inner_text="Hello page body")
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("text")
    assert "Hello page body" in out
    page.inner_text.assert_awaited_once_with("body")


def test_browser_use_text_truncates_at_8000_chars(monkeypatch):
    long_text = "x" * 9000
    page = _make_page_mock(inner_text=long_text)
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("text")
    assert "truncated at 8000 chars" in out
    assert len(out) < 9000


def test_browser_use_click_ok(monkeypatch):
    page = _make_page_mock()
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("click", selector="#submit", wait_ms=0)
    assert "clicked #submit" in out
    page.click.assert_awaited_once()


def test_browser_use_click_not_found(monkeypatch):
    page = _make_page_mock()
    page.click = AsyncMock(side_effect=Exception("Timeout exceeded waiting for selector"))
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("click", selector=".missing", wait_ms=0)
    parsed = json.loads(out)
    # On failure, browser_use's outer except retries once via _reset_browser_async.
    # We don't have a real browser to reset, so the second attempt fails too —
    # the final return is the "browser_use failed" error from the retry path.
    assert "error" in parsed
    assert "click failed" in parsed["error"] or "browser_use failed" in parsed["error"]


def test_browser_use_click_requires_selector(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    out = ns["browser_use"]("click", selector="")
    parsed = json.loads(out)
    assert "click requires selector" in parsed["error"]


def test_browser_use_fill(monkeypatch):
    page = _make_page_mock()
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("fill", selector="#email", value="a@b.com", wait_ms=0)
    assert "filled #email" in out
    page.fill.assert_awaited_once()
    args, _ = page.fill.await_args
    assert args[0] == "#email"
    assert args[1] == "a@b.com"


def test_browser_use_fill_requires_selector(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    out = ns["browser_use"]("fill", selector="", value="x")
    parsed = json.loads(out)
    assert "fill requires selector" in parsed["error"]


def test_browser_use_eval(monkeypatch):
    page = _make_page_mock(evaluate_value=42)
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("eval", expression="1 + 41")
    parsed = json.loads(out)
    assert parsed.get("value") == 42
    page.evaluate.assert_awaited_once_with("1 + 41")


def test_browser_use_eval_requires_expression(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    out = ns["browser_use"]("eval", expression="")
    parsed = json.loads(out)
    assert "eval requires expression" in parsed["error"]


def test_browser_use_screenshot_uploads_to_s3(monkeypatch):
    s3 = MagicMock()
    page = _make_page_mock()
    page.screenshot = AsyncMock(return_value=b"\x89PNGfake-data")
    ns = _exec_builtin(monkeypatch, page=page, s3_mock=s3)
    out = ns["browser_use"]("screenshot")
    # Successful screenshot returns a dict (not a JSON string) with content blocks.
    assert isinstance(out, dict)
    assert out.get("status") == "success"
    text_block = next(c["text"] for c in out["content"] if "text" in c)
    assert "__S3_DOWNLOAD__" in text_block
    s3.put_object.assert_called_once()


def test_browser_use_screenshot_handles_empty_png(monkeypatch):
    page = _make_page_mock()
    page.screenshot = AsyncMock(return_value=b"")
    ns = _exec_builtin(monkeypatch, page=page)
    out = ns["browser_use"]("screenshot")
    parsed = json.loads(out)
    assert "no data" in parsed["error"]


def test_browser_use_unknown_action(monkeypatch):
    ns = _exec_builtin(monkeypatch)
    out = ns["browser_use"]("teleport")
    parsed = json.loads(out)
    assert "unknown action" in parsed["error"]


def test_browser_use_reuses_warm_page(monkeypatch):
    """A pre-cached page is returned from _get_page_async without reconnecting.

    This is the regression guard for the "session reused across calls" claim
    in the docstring (warm for 1h). The fake page returns is_closed=False, so
    _get_page_async takes the early-return branch on every call.
    """
    page = _make_page_mock()
    ns = _exec_builtin(monkeypatch, page=page)
    ns["browser_use"]("navigate", url="https://a.example", wait_ms=0)
    ns["browser_use"]("navigate", url="https://b.example", wait_ms=0)
    ns["browser_use"]("navigate", url="https://c.example", wait_ms=0)
    # Cached page used three times — no new browser session created.
    assert page.goto.await_count == 3
    assert page.is_closed.call_count >= 3


def test_browser_use_invalid_navigate_url_no_page_call(monkeypatch):
    """navigate with bad URL must short-circuit BEFORE touching the page."""
    page = _make_page_mock()
    ns = _exec_builtin(monkeypatch, page=page)
    ns["browser_use"]("navigate", url="javascript:alert(1)")
    page.goto.assert_not_awaited()

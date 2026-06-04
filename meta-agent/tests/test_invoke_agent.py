"""Tests for invoke_agent tool — small RBAC + delegation wrapper."""

import json
import sys
import types
from unittest.mock import MagicMock

import pytest

# ── Module stubs so `from strands import tool` works in-process ───────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "AGENT_ROLE_ARN": "arn:aws:iam::000:role/agent",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "base/agent-deployment.zip",
    "MODEL_ID": "mock-model",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope

    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-test", raising=False)


def test_invoke_agent_passes_through_response(monkeypatch):
    from tools import invoke_agent as mod

    monkeypatch.setattr(
        mod,
        "ensure_agent_in_workspace",
        lambda agent_id, min_role: ({"workspace_id": "ws-test"}, None),
    )
    monkeypatch.setattr(mod, "invoke_runtime", lambda agent_id, prompt: "Hello there!")

    out = mod.invoke_agent("rt-abc", "Hi")
    assert out == "Hello there!"


def test_invoke_agent_rejects_when_caller_below_editor(monkeypatch):
    """ensure_agent_in_workspace error dict must short-circuit invoke_runtime."""
    from tools import invoke_agent as mod

    monkeypatch.setattr(
        mod,
        "ensure_agent_in_workspace",
        lambda agent_id, min_role: (None, {"error": "Permission denied: editor required"}),
    )

    runtime_called = []
    monkeypatch.setattr(
        mod,
        "invoke_runtime",
        lambda *a, **kw: runtime_called.append(1) or "should not happen",
    )

    out = json.loads(mod.invoke_agent("rt-abc", "Hi"))
    assert "error" in out
    assert "Permission denied" in out["error"]
    # Critical: never invoke the runtime if RBAC failed
    assert runtime_called == []


def test_invoke_agent_calls_runtime_with_correct_args(monkeypatch):
    """Verify agent_id + prompt are forwarded verbatim."""
    from tools import invoke_agent as mod

    monkeypatch.setattr(
        mod,
        "ensure_agent_in_workspace",
        lambda agent_id, min_role: ({"workspace_id": "ws-test"}, None),
    )

    captured = {}

    def fake_runtime(agent_id, prompt):
        captured["agent_id"] = agent_id
        captured["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(mod, "invoke_runtime", fake_runtime)

    mod.invoke_agent("rt-xyz", "Some Prompt")
    assert captured == {"agent_id": "rt-xyz", "prompt": "Some Prompt"}


def test_invoke_agent_uses_editor_role_threshold(monkeypatch):
    """Tool must require ROLE_EDITOR (testing only — invocation could be costly)."""
    from tools import invoke_agent as mod
    from tools._scope import ROLE_EDITOR

    captured_min_role = []

    def fake_ensure(agent_id, min_role):
        captured_min_role.append(min_role)
        return ({"workspace_id": "ws-test"}, None)

    monkeypatch.setattr(mod, "ensure_agent_in_workspace", fake_ensure)
    monkeypatch.setattr(mod, "invoke_runtime", lambda *a, **kw: "ok")

    mod.invoke_agent("rt-1", "x")
    assert captured_min_role == [ROLE_EDITOR]

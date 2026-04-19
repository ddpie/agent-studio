"""Unit tests for link_agent helpers and agent_caller tool_library module.

These focus on the pure helper functions (no AWS calls). The full redeploy
path is exercised by the Sprint 3 E2E suite.
"""
import ast
import json
import os
import sys
import types
from unittest.mock import MagicMock


# ── Module stubs so `from strands import tool` works in-process ───────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands
_mock_strands_models = sys.modules.get("strands.models") or types.ModuleType("strands.models")
if not hasattr(_mock_strands_models, "BedrockModel"):
    _mock_strands_models.BedrockModel = MagicMock
sys.modules["strands.models"] = _mock_strands_models


def _install_config_stub():
    """Make `import config` work without AWS env in this test process."""
    mod = sys.modules.get("config") or types.ModuleType("config")
    defaults = {
        "MODEL_ID": "mock-model",
        "REGION": "us-east-1",
        "ACCOUNT_ID": "000000000000",
        "S3_BUCKET": "test-bucket",
        "AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/test-role",
        "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
        "AGENTS_TABLE": "agent-studio-agents",
        "TOOLS_TABLE": "agent-studio-tools",
        "PERMISSION_TIER_ROLES": {"readonly": "arn:aws:iam::000:role/x"},
        "DEFAULT_PERMISSION_TIER": "readonly",
        "MCP_GATEWAY_URL": "",
    }
    for k, v in defaults.items():
        if not hasattr(mod, k):
            setattr(mod, k, v)
    sys.modules["config"] = mod


_install_config_stub()


def test_agent_caller_tool_code_parses():
    """TOOL_CODE must be valid Python so assemble_tools() produces a working tools.py."""
    from tools_library import agent_caller

    assert agent_caller.TOOL_META["id"] == "agent_caller"
    assert agent_caller.TOOL_NAMES == "call_agent"
    ast.parse(agent_caller.TOOL_CODE)
    assert "def call_agent" in agent_caller.TOOL_CODE
    assert "A2A_INVOKE_URL" in agent_caller.TOOL_CODE
    assert "AGENTS_TOOL_KEYS_JSON" in agent_caller.TOOL_CODE
    assert "/a2a/agents/" in agent_caller.TOOL_CODE


def test_agent_caller_registered_in_registry():
    from tools_library.registry import build_tool_catalog

    catalog = build_tool_catalog()
    assert "call_agent" in catalog
    assert catalog["call_agent"]["id"] == "agent_caller"


def test_link_section_roundtrip():
    from tools.link_agent import (
        _build_link_section,
        _strip_link_section,
        _LINK_MARKER_START,
        _LINK_MARKER_END,
    )

    base = "## Role\nYou are a helpful agent.\n\n## Tools\n- use `foo`.\n"

    linked = [
        {"agent_id": "calc-1", "display_name": "Calculator", "description": "Does arithmetic."},
        {"agent_id": "search-1", "display_name": "WebSearcher", "description": "Searches the web."},
    ]
    with_section = base.rstrip() + "\n" + _build_link_section(linked)
    assert _LINK_MARKER_START in with_section
    assert _LINK_MARKER_END in with_section
    assert "`calc-1`" in with_section
    assert "`search-1`" in with_section

    # Stripping returns (roughly) the original prompt.
    stripped = _strip_link_section(with_section)
    assert _LINK_MARKER_START not in stripped
    assert _LINK_MARKER_END not in stripped
    assert "## Role" in stripped
    assert "## Tools" in stripped


def test_link_section_empty_when_no_peers():
    from tools.link_agent import _build_link_section

    assert _build_link_section([]) == ""


def test_public_base_url_prefers_explicit_override(monkeypatch):
    from tools import link_agent as mod

    monkeypatch.setenv("AGENT_STUDIO_A2A_INVOKE_URL", "https://example.cloudfront.net")
    monkeypatch.delenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", raising=False)
    assert mod._public_base_url() == "https://example.cloudfront.net"

    monkeypatch.delenv("AGENT_STUDIO_A2A_INVOKE_URL", raising=False)
    monkeypatch.setenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "d123.cloudfront.net")
    assert mod._public_base_url() == "https://d123.cloudfront.net"

    monkeypatch.setenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "https://d123.cloudfront.net/")
    assert mod._public_base_url() == "https://d123.cloudfront.net"

    monkeypatch.delenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", raising=False)
    assert mod._public_base_url() == ""


def test_mint_and_revoke_key_shape(monkeypatch):
    """Ensure the DDB item has the same shape as lambda/crud/a2a_keys.py::create_key."""
    captured = {}

    class _FakeTable:
        def put_item(self, Item):
            captured["item"] = Item

        def update_item(self, **kwargs):
            captured["update"] = kwargs

    class _FakeResource:
        def Table(self, name):
            captured.setdefault("tables", []).append(name)
            return _FakeTable()

    import boto3 as _real_boto3

    def _fake_resource(name, region_name=None):
        assert name == "dynamodb"
        return _FakeResource()

    monkeypatch.setattr(_real_boto3, "resource", _fake_resource)

    from tools.link_agent import _mint_a2a_key

    key_id, plaintext = _mint_a2a_key(
        user_id="u-1",
        agent_id="tgt-1",
        workspace_id="ws-1",
    )
    assert plaintext.startswith("as_")
    assert len(plaintext) == 35  # "as_" + 32 chars
    item = captured["item"]
    assert item["agentId"] == "tgt-1"
    assert item["userId"] == "u-1"
    assert item["workspaceId"] == "ws-1"
    assert item["userAgentKey"] == "u-1#tgt-1"
    assert item["keyPrefix"] == plaintext[:8]
    assert item["revoked"] is False
    assert len(item["apiKeyHash"]) == 64  # sha256 hex
    assert item["keyId"] == key_id

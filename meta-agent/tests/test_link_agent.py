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


def test_has_min_role_gate():
    from tools.link_agent import _has_min_role

    assert _has_min_role(None, "editor") is False
    assert _has_min_role({}, "editor") is False
    assert _has_min_role({"role": "viewer"}, "editor") is False
    assert _has_min_role({"role": "editor"}, "editor") is True
    assert _has_min_role({"role": "admin"}, "editor") is True
    assert _has_min_role({"role": "owner"}, "editor") is True
    # Unknown role does not satisfy any min threshold
    assert _has_min_role({"role": "guest"}, "editor") is False


class _FakeWorkspaceTable:
    """Fake DDB table that returns per-user membership records."""

    def __init__(self, members_by_key):
        # members_by_key: {(workspace_id, user_id): role_or_None}
        self._members = members_by_key
        self.put_items = []
        self.updates = []

    def get_item(self, Key, ConsistentRead=False):
        ws = Key.get("workspaceId")
        sk = Key.get("sk", "")
        if sk.startswith("MEMBER#"):
            user = sk[len("MEMBER#"):]
            role = self._members.get((ws, user))
            if role is None:
                return {}
            return {"Item": {"workspaceId": ws, "sk": sk, "role": role}}
        # Agent lookup in AGENTS_TABLE comes through a different table via our
        # monkeypatched boto3.resource — this class is only used for the
        # workspaces table.
        return {}

    def put_item(self, Item):
        self.put_items.append(Item)

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class _FakeAgentsTable:
    def __init__(self, agents_by_id):
        self._agents = agents_by_id
        self.updates = []

    def get_item(self, Key, ConsistentRead=False):
        agent_id = Key.get("agentId")
        item = self._agents.get(agent_id)
        return {"Item": item} if item else {}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


def _install_link_agent_ddb(monkeypatch, *, members, agents):
    """Route DDB table lookups to in-memory fakes inside tools.link_agent."""
    ws_table = _FakeWorkspaceTable(members)
    agents_table = _FakeAgentsTable(agents)

    class _FakeResource:
        def Table(self, name):
            if name == "agent-studio-workspaces":
                return ws_table
            if name == "agent-studio-agents":
                return agents_table
            # Unused tables (a2a keys) — return a black-hole stub.
            return _FakeWorkspaceTable({})

    import boto3 as _real_boto3

    def _fake_resource(name, region_name=None):
        assert name == "dynamodb"
        return _FakeResource()

    monkeypatch.setattr(_real_boto3, "resource", _fake_resource)
    return ws_table, agents_table


def _prime_caller(monkeypatch, caller_id):
    """Set the module-level _caller_id that link_agent reads via tools.create_agent."""
    import tools.create_agent as _ca

    monkeypatch.setattr(_ca, "_caller_id", caller_id, raising=False)


def _agents_fixture():
    return {
        "src-1": {"agentId": "src-1", "workspace_id": "ws-1", "name": "Source"},
        "tgt-1": {"agentId": "tgt-1", "workspace_id": "ws-1", "name": "Target"},
    }


def test_link_agent_rejects_non_member(monkeypatch):
    from tools import link_agent as mod

    _prime_caller(monkeypatch, "user-nomember")
    _install_link_agent_ddb(monkeypatch, members={}, agents=_agents_fixture())
    monkeypatch.setenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "d123.cloudfront.net")

    result = json.loads(mod.link_agent("src-1", "tgt-1"))
    assert "Permission denied" in result.get("error", "")
    assert result.get("caller_role") == "none"


def test_link_agent_rejects_viewer(monkeypatch):
    from tools import link_agent as mod

    _prime_caller(monkeypatch, "user-viewer")
    _install_link_agent_ddb(
        monkeypatch,
        members={("ws-1", "user-viewer"): "viewer"},
        agents=_agents_fixture(),
    )
    monkeypatch.setenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "d123.cloudfront.net")

    result = json.loads(mod.link_agent("src-1", "tgt-1"))
    assert "Permission denied" in result.get("error", "")
    assert result.get("caller_role") == "viewer"


def _stub_link_side_effects(monkeypatch):
    """Short-circuit everything after the permission gate."""
    from tools import link_agent as mod

    monkeypatch.setattr(mod, "_mint_a2a_key", lambda **kw: ("key-id-1", "as_fakeplaintext"))
    monkeypatch.setattr(
        mod,
        "_update_linked_keys_secret",
        lambda *a, **kw: ('{"tgt-1": "as_fakeplaintext"}', {"tgt-1": "as_fakeplaintext"}),
    )
    monkeypatch.setattr(mod, "_load_metadata", lambda _agent_id: {"system_prompt": ""})
    monkeypatch.setattr(mod, "_save_metadata", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "_redeploy_source", lambda *a, **kw: "READY")
    # _get_builtin_code may be hit when refreshing tool_definitions
    monkeypatch.setattr(mod, "_get_builtin_code", lambda name: "")


def test_link_agent_accepts_editor(monkeypatch):
    from tools import link_agent as mod

    _prime_caller(monkeypatch, "user-editor")
    _install_link_agent_ddb(
        monkeypatch,
        members={("ws-1", "user-editor"): "editor"},
        agents=_agents_fixture(),
    )
    monkeypatch.setenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "d123.cloudfront.net")
    _stub_link_side_effects(monkeypatch)

    result = json.loads(mod.link_agent("src-1", "tgt-1"))
    assert "error" not in result, result
    assert result.get("status") == "READY"
    assert result.get("source_agent_id") == "src-1"
    assert result.get("target_agent_id") == "tgt-1"
    assert result.get("key_id") == "key-id-1"


def test_link_agent_accepts_admin(monkeypatch):
    from tools import link_agent as mod

    _prime_caller(monkeypatch, "user-admin")
    _install_link_agent_ddb(
        monkeypatch,
        members={("ws-1", "user-admin"): "admin"},
        agents=_agents_fixture(),
    )
    monkeypatch.setenv("AGENT_STUDIO_CLOUDFRONT_DOMAIN", "d123.cloudfront.net")
    _stub_link_side_effects(monkeypatch)

    result = json.loads(mod.link_agent("src-1", "tgt-1"))
    assert "error" not in result, result
    assert result.get("status") == "READY"


def test_unlink_agent_rejects_viewer(monkeypatch):
    from tools import link_agent as mod

    _prime_caller(monkeypatch, "user-viewer")
    _install_link_agent_ddb(
        monkeypatch,
        members={("ws-1", "user-viewer"): "viewer"},
        agents=_agents_fixture(),
    )

    result = json.loads(mod.unlink_agent("src-1", "tgt-1"))
    assert "Permission denied" in result.get("error", "")
    assert result.get("caller_role") == "viewer"


def test_unlink_agent_rejects_non_member(monkeypatch):
    from tools import link_agent as mod

    _prime_caller(monkeypatch, "user-outsider")
    _install_link_agent_ddb(monkeypatch, members={}, agents=_agents_fixture())

    result = json.loads(mod.unlink_agent("src-1", "tgt-1"))
    assert "Permission denied" in result.get("error", "")


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

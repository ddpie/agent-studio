"""Tests for agent_template_v2 endpoint resolution helper.

The actual helper lives inside MAIN_PY_MCP_TEMPLATE (baked into generated
Agent code). We test the logic by extracting it with exec() and exercising
it against synthetic endpoint dicts.
"""
from __future__ import annotations

import sys
import types


# Mock strands / config BEFORE importing template module
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "REGION": "us-east-1", "S3_BUCKET": "test-bucket",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


def _extract_resolver() -> callable:
    """Compile the baked resolver function out of the template string."""
    from templates.agent_template_v2 import MAIN_PY_MCP_TEMPLATE
    import urllib.parse

    # Find the function definition inside the template. It uses {REGION} which
    # the template-rendering step replaces; for the test we substitute
    # directly.
    src = MAIN_PY_MCP_TEMPLATE
    # Extract from "def _resolve_runtime_endpoint_from_config" until the next
    # top-level def or end of MCP endpoint section.
    start = src.index("def _resolve_runtime_endpoint_from_config")
    end = src.index("\ndef _build_mcp_clients", start)
    func_src = src[start:end]
    # Template uses bare `REGION`; provide it.
    ns = {"urllib": urllib.parse.__dict__["__package__"] and __import__("urllib.parse") or None,
          "REGION": "us-east-1"}
    # urllib.parse imports inside the template — provide the module directly
    ns["urllib"] = __import__("urllib")
    exec(func_src, ns)
    return ns["_resolve_runtime_endpoint_from_config"]


class TestEndpointResolution:
    def test_runtime_endpoint_present(self):
        resolver = _extract_resolver()
        ep = {
            "type": "runtime",
            "runtime_endpoint": "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/foo/invocations?qualifier=DEFAULT",
        }
        assert resolver(ep) == ep["runtime_endpoint"]

    def test_runtime_arn_builds_endpoint(self):
        resolver = _extract_resolver()
        ep = {
            "type": "runtime",
            "runtime_arn": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/asmcp_abc123_cloudwatch",
        }
        url = resolver(ep)
        assert url is not None
        assert url.startswith("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/")
        assert url.endswith("/invocations?qualifier=DEFAULT")
        # The ARN should be URL-encoded
        assert "asmcp_abc123_cloudwatch" in url or "asmcp%5Fabc123%5Fcloudwatch" in url

    def test_missing_both_returns_none(self):
        resolver = _extract_resolver()
        ep = {"type": "runtime", "target_name": "mcp_cloudwatch"}
        assert resolver(ep) is None

    def test_empty_endpoint_returns_none(self):
        resolver = _extract_resolver()
        ep = {"type": "runtime", "runtime_endpoint": ""}
        # Empty string is falsy; falls through to runtime_arn check
        assert resolver(ep) is None


class TestTemplateIntegrity:
    """Sanity checks that the template still compiles / has expected shape."""

    def test_mcp_template_has_expected_structure(self):
        from templates.agent_template_v2 import MAIN_PY_MCP_TEMPLATE
        # Must contain the new resolver
        assert "_resolve_runtime_endpoint_from_config" in MAIN_PY_MCP_TEMPLATE
        # Must not call list_agent_runtimes for resolution
        # (the comment about removing it is still present; strip comments
        # before checking)
        import re
        # Remove Python # line comments
        code_only = re.sub(r"#[^\n]*", "", MAIN_PY_MCP_TEMPLATE)
        # list_agent_runtimes should not appear in code statements
        assert "list_agent_runtimes(" not in code_only, (
            "list_agent_runtimes() call should be removed; baked endpoints only"
        )

    def test_non_mcp_template_unaffected(self):
        from templates.agent_template_v2 import MAIN_PY_TEMPLATE
        # Non-MCP template should not reference any MCP resolution code
        assert "_resolve_runtime_endpoint_from_config" not in MAIN_PY_TEMPLATE
        assert "_build_mcp_clients" not in MAIN_PY_TEMPLATE

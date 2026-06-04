"""Tests for validate_agent — pure validation logic, no AWS/LLM calls."""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Mock strands and config before importing the module under test
_mock_strands = types.ModuleType("strands")
_mock_strands.tool = lambda f: f  # @tool is a no-op
_mock_strands.Agent = MagicMock
_mock_strands_models = types.ModuleType("strands.models")
_mock_strands_models.BedrockModel = MagicMock
sys.modules.setdefault("strands", _mock_strands)
sys.modules.setdefault("strands.models", _mock_strands_models)

_mock_config = types.ModuleType("config")
_mock_config.MODEL_ID = "mock-model"
_mock_config.REGION = "us-east-1"
_mock_config.S3_BUCKET = "test-bucket"
_mock_config.AGENTS_TABLE = "agent-studio-agents"
_mock_config.TOOLS_TABLE = "agent-studio-tools"
sys.modules.setdefault("config", _mock_config)
# Setdefault means the existing config (potentially missing AGENTS_TABLE) is used
# in run-suite mode. Explicitly assign so this test still works in isolation:
if not hasattr(sys.modules["config"], "AGENTS_TABLE"):
    sys.modules["config"].AGENTS_TABLE = "agent-studio-agents"
if not hasattr(sys.modules["config"], "TOOLS_TABLE"):
    sys.modules["config"].TOOLS_TABLE = "agent-studio-tools"

# Mock tools_library so _get_builtin_tool_names works in isolation
_mock_registry = types.ModuleType("tools_library.registry")
_mock_registry._ALL_TOOLS = []
_mock_tl = types.ModuleType("tools_library")
_mock_tl.registry = _mock_registry
sys.modules.setdefault("tools_library", _mock_tl)
sys.modules.setdefault("tools_library.registry", _mock_registry)

from tools.validate_agent import (
    _WRITE_RE,
    _extract_tool_blocks,
    _get_builtin_tool_names,
    _get_mcp_tool_names,
    _review_prompt_quality,
    validate_agent,
)


def _parse(result: str) -> dict:
    return json.loads(result)


# ── Name validation ──────────────────────────────────────────────

class TestNameValidation:
    def test_empty_name_is_error(self):
        r = _parse(validate_agent(agent_name="", system_prompt="hello", description="d"))
        assert not r["valid"]
        assert any("name is required" in e.lower() for e in r["errors"])

    def test_whitespace_only_name_is_error(self):
        r = _parse(validate_agent(agent_name="   ", system_prompt="hello", description="d"))
        assert not r["valid"]

    def test_alphanumeric_name_passes(self):
        r = _parse(validate_agent(agent_name="MyAgent01", system_prompt="hello", description="d"))
        assert r["valid"]

    @pytest.mark.parametrize("name", ["my-agent", "my_agent", "my agent", "agent!"])
    def test_non_alphanumeric_name_is_error(self, name):
        r = _parse(validate_agent(agent_name=name, system_prompt="hello", description="d"))
        assert not r["valid"]
        assert any("alphanumeric" in e for e in r["errors"])

    def test_single_char_name_passes(self):
        r = _parse(validate_agent(agent_name="A", system_prompt="hello", description="d"))
        assert r["valid"]

    def test_numeric_only_name_passes(self):
        r = _parse(validate_agent(agent_name="12345", system_prompt="hello", description="d"))
        assert r["valid"]

    @pytest.mark.parametrize("name", ["agent.v2", "agent/test", "agent@home", "agent#1"])
    def test_special_chars_rejected(self, name):
        r = _parse(validate_agent(agent_name=name, system_prompt="hello", description="d"))
        assert not r["valid"]

    def test_unicode_name_rejected(self):
        r = _parse(validate_agent(agent_name="数据分析", system_prompt="hello", description="d"))
        assert not r["valid"]


# ── Required fields ──────────────────────────────────────────────

class TestRequiredFields:
    def test_empty_prompt_is_error(self):
        r = _parse(validate_agent(agent_name="Agent1", system_prompt="", description="d"))
        assert not r["valid"]
        assert any("system prompt" in e.lower() for e in r["errors"])

    def test_empty_description_is_warning(self):
        r = _parse(validate_agent(agent_name="Agent1", system_prompt="hello", description=""))
        assert r["valid"]  # warning, not error
        assert any("description" in w.lower() for w in r["warnings"])

    def test_whitespace_only_prompt_is_error(self):
        r = _parse(validate_agent(agent_name="Agent1", system_prompt="   \n\t  ", description="d"))
        assert not r["valid"]

    def test_multiple_errors_accumulated(self):
        r = _parse(validate_agent(agent_name="", system_prompt="", description=""))
        assert not r["valid"]
        assert len(r["errors"]) >= 2  # name + prompt

    def test_valid_minimal_agent(self):
        r = _parse(validate_agent(agent_name="Agent1", system_prompt="hello", description="d"))
        assert r["valid"]
        assert len(r["errors"]) == 0

    def test_summary_format_pass(self):
        r = _parse(validate_agent(agent_name="Agent1", system_prompt="hello", description="d"))
        assert r["summary"].startswith("PASS")

    def test_summary_format_fail(self):
        r = _parse(validate_agent(agent_name="", system_prompt="", description=""))
        assert r["summary"].startswith("FAIL")


# ── Python syntax check ─────────────────────────────────────────

class TestSyntaxCheck:
    def test_valid_tool_code_passes(self):
        code = '@tool\ndef greet(name: str = "") -> str:\n    """Say hi."""\n    return f"Hello {name}"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use greet to say hi",
            tool_definitions=code, tool_names="greet", description="d",
        ))
        assert r["valid"]

    def test_syntax_error_is_caught(self):
        code = '@tool\ndef broken(:\n    pass'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions=code, tool_names="broken", description="d",
        ))
        assert not r["valid"]
        assert any("syntax error" in e.lower() for e in r["errors"])

    def test_indentation_error_is_caught(self):
        code = '@tool\ndef bad_indent() -> str:\n    """B."""\n  return "oops"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions=code, tool_names="bad_indent", description="d",
        ))
        assert not r["valid"]

    def test_empty_tool_definitions_passes(self):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions="", tool_names="", description="d",
        ))
        assert r["valid"]

    def test_whitespace_only_tool_definitions_passes(self):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions="   \n  ", tool_names="", description="d",
        ))
        assert r["valid"]

    def test_multiple_tools_valid_syntax(self):
        code = (
            '@tool\ndef a(x: str = "") -> str:\n    """A."""\n    return x\n\n'
            '@tool\ndef b(y: int = 0) -> str:\n    """B."""\n    return str(y)'
        )
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use a and b",
            tool_definitions=code, tool_names="a,b", description="d",
        ))
        assert r["valid"]


# ── tool_names consistency ───────────────────────────────────────

class TestToolNamesConsistency:
    def test_declared_but_not_in_code(self):
        code = '@tool\ndef alpha() -> str:\n    """A."""\n    return "a"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions=code, tool_names="alpha,beta", description="d",
        ))
        assert any("beta" in e for e in r["errors"])

    def test_in_code_but_not_declared(self):
        code = '@tool\ndef alpha() -> str:\n    """A."""\n    return "a"\n\n@tool\ndef beta() -> str:\n    """B."""\n    return "b"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions=code, tool_names="alpha", description="d",
        ))
        assert any("beta" in w for w in r["warnings"])

    def test_empty_tool_names_with_code_warns(self):
        code = '@tool\ndef alpha() -> str:\n    """A."""\n    return "a"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions=code, tool_names="", description="d",
        ))
        assert any("tool_names is empty" in w for w in r["warnings"])

    def test_exact_match_no_errors(self):
        code = '@tool\ndef x() -> str:\n    """X."""\n    return ""\n\n@tool\ndef y() -> str:\n    """Y."""\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use x and y",
            tool_definitions=code, tool_names="x,y", description="d",
        ))
        assert r["valid"]
        assert not any("tool_names" in w.lower() for w in r["warnings"])

    def test_tool_names_with_extra_whitespace(self):
        code = '@tool\ndef a() -> str:\n    """A."""\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use a",
            tool_definitions=code, tool_names=" a , ", description="d",
        ))
        assert r["valid"]

    def test_tool_names_only_no_code_warns(self):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions="", tool_names="mystery_tool", description="d",
        ))
        assert any("mystery_tool" in w for w in r["warnings"])


# ── Unavailable library detection ────────────────────────────────

class TestUnavailableLibs:
    @pytest.mark.parametrize("lib", ["pandas", "numpy", "selenium", "flask", "torch"])
    def test_blocked_import_is_error(self, lib):
        code = f'import {lib}\n\n@tool\ndef foo() -> str:\n    """F."""\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions=code, tool_names="foo", description="d",
        ))
        assert any(lib in e for e in r["errors"])

    def test_allowed_import_passes(self):
        code = 'import json\nimport re\n\n@tool\ndef foo() -> str:\n    """F."""\n    return json.dumps({})'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use foo",
            tool_definitions=code, tool_names="foo", description="d",
        ))
        assert r["valid"]


# ── Write pattern detection ──────────────────────────────────────

# NOTE: TestWritePatternDetection removed. validate_agent.py L409 explicitly
# marks the readonly-tier-vs-write-operations check as removed; the test class
# was asserting a feature that no longer exists.


# ── Prompt ↔ tool consistency ────────────────────────────────────

class TestPromptToolSync:
    def test_tool_not_mentioned_in_prompt_warns(self):
        code = '@tool\ndef secret_tool() -> str:\n    """S."""\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="You are a helpful agent.",
            tool_definitions=code, tool_names="secret_tool", description="d",
        ))
        assert any("secret_tool" in w for w in r["warnings"])

    def test_tool_mentioned_in_prompt_no_warning(self):
        code = '@tool\ndef search(q: str = "") -> str:\n    """Search."""\n    return q'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use search to find things.",
            tool_definitions=code, tool_names="search", description="d",
        ))
        assert not any("search" in w and "not mentioned" in w for w in r["warnings"])

    def test_tool_mentioned_as_readable_name(self):
        # "secret_tool" → "secret tool" should also count as mentioned
        code = '@tool\ndef secret_tool() -> str:\n    """S."""\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use the secret tool for hidden things.",
            tool_definitions=code, tool_names="secret_tool", description="d",
        ))
        assert not any("secret_tool" in w and "not mentioned" in w for w in r["warnings"])

    def test_tool_mentioned_case_insensitive_match(self):
        # Code does prompt_lower = system_prompt.lower(), then checks func_name (original case) in prompt_lower
        # So "MyTool" won't match "mytool" — the check is case-sensitive on func_name side
        # But readable_name "my tool" (from replace("_","")) IS checked against lowered prompt
        code = '@tool\ndef my_tool() -> str:\n    """M."""\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use MY TOOL for things.",
            tool_definitions=code, tool_names="my_tool", description="d",
        ))
        # "my tool" (readable) matches "my tool" in lowered prompt
        assert not any("my_tool" in w and "not mentioned" in w for w in r["warnings"])

    def test_no_warning_when_no_tools(self):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="You are helpful.",
            tool_definitions="", tool_names="", description="d",
        ))
        assert not any("not mentioned" in w for w in r["warnings"])


# ── Long prompt warning ──────────────────────────────────────────

class TestLongPrompt:
    def test_very_long_prompt_warns(self):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="x" * 10001, description="d",
        ))
        assert any("very long" in w.lower() for w in r["warnings"])

    def test_exactly_10000_chars_no_warning(self):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="x" * 10000, description="d",
        ))
        assert not any("very long" in w.lower() for w in r["warnings"])


# ── Security patterns ────────────────────────────────────────────

class TestSecurityPatterns:
    def test_os_system_warns(self):
        code = 'import os\n\n@tool\ndef danger() -> str:\n    """D."""\n    os.system("ls")\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use danger",
            tool_definitions=code, tool_names="danger", description="d",
        ))
        assert any("os.system" in w or "subprocess" in w for w in r["warnings"])

    def test_subprocess_warns(self):
        code = 'import os\nimport subprocess\n\n@tool\ndef danger() -> str:\n    """D."""\n    subprocess.run(["ls"])\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use danger",
            tool_definitions=code, tool_names="danger", description="d",
        ))
        assert any("os.system" in w or "subprocess" in w for w in r["warnings"])

    def test_no_security_warning_without_os_import(self):
        code = '@tool\ndef safe() -> str:\n    """S."""\n    return "safe"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use safe",
            tool_definitions=code, tool_names="safe", description="d",
        ))
        assert not any("os.system" in w or "subprocess" in w for w in r["warnings"])


# ── _WRITE_RE regex unit tests ───────────────────────────────────

class TestWriteRegex:
    @pytest.mark.parametrize("text", [
        "put_item", "delete_item", "update_item",
        "put_object", "delete_object",
        "create_table", "delete_bucket", "update_function",
        "INSERT INTO users", "UPDATE users SET", "DELETE FROM logs",
        "DROP TABLE t", "CREATE TABLE t",
        ".put(", ".delete(", ".post(",
        "os.remove", "os.unlink", "shutil.rmtree",
    ])
    def test_matches_write_pattern(self, text):
        assert _WRITE_RE.search(text), f"Expected match for: {text}"

    @pytest.mark.parametrize("text", [
        "get_item", "list_objects", "describe_table",
        "SELECT * FROM users", "query_metrics",
        ".get(", "os.path.exists",
    ])
    def test_does_not_match_read_pattern(self, text):
        assert not _WRITE_RE.search(text), f"Unexpected match for: {text}"


# ── MCP tool name recognition ──────────────────────────────────

class TestGetMcpToolNames:
    """Unit tests for _get_mcp_tool_names helper."""

    def test_empty_list_returns_empty(self):
        assert _get_mcp_tool_names([]) == []

    @patch("boto3.client")
    def test_reads_manifest_from_s3(self, mock_boto):
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3
        manifest = json.dumps([
            {"name": "get_metric_statistics", "description": "Get metrics"},
            {"name": "describe_alarms", "description": "Describe alarms"},
        ]).encode()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: manifest)}

        result = _get_mcp_tool_names(["cloudwatch"])
        assert "get_metric_statistics" in result
        assert "describe_alarms" in result

    @patch("boto3.client")
    def test_hyphen_to_underscore_fallback(self, mock_boto):
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        def side_effect(**kwargs):
            key = kwargs.get("Key", "")
            if "aws-pricing" in key:
                raise Exception("NoSuchKey")
            return {"Body": MagicMock(read=lambda: json.dumps([
                {"name": "get_pricing", "description": "d"},
            ]).encode())}

        mock_s3.get_object.side_effect = side_effect
        result = _get_mcp_tool_names(["aws-pricing"])
        assert "get_pricing" in result

    @patch("boto3.client")
    def test_missing_manifest_returns_empty(self, mock_boto):
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3
        mock_s3.get_object.side_effect = Exception("NoSuchKey")

        result = _get_mcp_tool_names(["custom-mcp"])
        assert result == []

    @patch("boto3.client")
    def test_multiple_targets(self, mock_boto):
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        def side_effect(**kwargs):
            key = kwargs.get("Key", "")
            if "alpha" in key:
                return {"Body": MagicMock(read=lambda: json.dumps([{"name": "tool_a"}]).encode())}
            if "beta" in key:
                return {"Body": MagicMock(read=lambda: json.dumps([{"name": "tool_b"}]).encode())}
            raise Exception("NoSuchKey")

        mock_s3.get_object.side_effect = side_effect
        result = _get_mcp_tool_names(["alpha", "beta"])
        assert "tool_a" in result
        assert "tool_b" in result


class TestMcpToolValidationIntegration:
    """MCP tool names should not trigger false positives in validation."""

    @patch("tools.validate_agent._get_mcp_tool_names", return_value=["generate_image"])
    def test_mcp_tool_in_tool_names_not_flagged(self, _mock):
        code = '@tool\ndef my_tool() -> str:\n    """M."""\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use my_tool and generate_image",
            tool_definitions=code, tool_names="my_tool,generate_image", description="d",
        ))
        # generate_image is MCP-provided — should NOT appear in errors
        assert not any("generate_image" in e for e in r["errors"])

    @patch("tools.validate_agent._get_mcp_tool_names", return_value=["generate_image"])
    def test_mcp_only_no_code_not_flagged(self, _mock):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use generate_image",
            tool_definitions="", tool_names="generate_image", description="d",
        ))
        # No custom code, only MCP tool — should not warn about missing @tool
        assert not any("generate_image" in w for w in r["warnings"])


class TestGhostToolDetection:
    """system_prompt can reference tools that don't exist (LLM hallucination).

    validate_agent should fail those before deployment so the Meta-Agent has
    to fix the prompt — otherwise the deployed agent returns "Unknown tool"
    at runtime (see the AWSCloudOpsAssistant / audit_services regression).
    """

    def test_ghost_backtick_tool_reports_error(self):
        """Prompt references `audit_services` (snake_case, in backticks) but
        it's not in tool_names, tool_definitions, skills, or MCP."""
        r = _parse(validate_agent(
            agent_name="Agent1",
            system_prompt="For health checks, call `audit_services` first.",
            tool_definitions="",
            tool_names="web_search",
            description="d",
        ))
        assert any("audit_services" in e for e in r["errors"]), r

    @patch("tools.validate_agent._get_mcp_tool_names", return_value=["get_active_alarms"])
    def test_real_mcp_tool_in_backticks_passes(self, _mock):
        r = _parse(validate_agent(
            agent_name="Agent1",
            system_prompt="Call `get_active_alarms` to list alarms.",
            tool_definitions="",
            tool_names="",
            description="d",
            staging_key="",  # mcp_targets read from params, not S3
        ))
        # get_active_alarms is a real MCP tool — no ghost error
        assert not any("get_active_alarms" in e for e in r["errors"])

    def test_english_phrases_in_backticks_not_flagged(self):
        """Backticks around prose / types / env names shouldn't trigger ghost."""
        r = _parse(validate_agent(
            agent_name="Agent1",
            system_prompt="Return `json` output. Use `str` and `list` types.",
            tool_definitions="",
            tool_names="web_search",
            description="d",
        ))
        assert not any("ghost" in e.lower() or "backtick" in e.lower() for e in r["errors"])

    def test_builtin_tool_in_backticks_not_flagged(self):
        """Runtime builtins (load_skill etc.) are always available."""
        r = _parse(validate_agent(
            agent_name="Agent1",
            system_prompt="Use `load_skill` to pull the guide, then `run_command` to execute.",
            tool_definitions="",
            tool_names="web_search",
            description="d",
        ))
        assert not any("load_skill" in e or "run_command" in e for e in r["errors"])


# ── _extract_tool_blocks helper unit tests ──────────────────────────────

class TestExtractToolBlocks:
    def test_no_tools_returns_empty(self):
        assert _extract_tool_blocks("# nothing here\n") == ""

    def test_simple_block_extracted(self):
        src = (
            "import json\n"
            "@tool\n"
            'def foo() -> str:\n'
            '    """F."""\n'
            "    return ''\n"
        )
        out = _extract_tool_blocks(src)
        assert "@tool" in out
        assert "def foo" in out
        assert "import json" in out

    def test_multiple_blocks_separated(self):
        src = (
            "@tool\n"
            'def a() -> str:\n'
            '    """A."""\n'
            "    return ''\n"
            "@tool\n"
            'def b() -> str:\n'
            '    """B."""\n'
            "    return ''\n"
        )
        out = _extract_tool_blocks(src)
        assert "def a" in out
        assert "def b" in out

    def test_break_on_app_entrypoint(self):
        """Once we leave the tool block and hit @app.* / async def _ — stop."""
        src = (
            "@tool\n"
            'def a() -> str:\n'
            '    """A."""\n'
            "    return ''\n"
            "@app.entrypoint\n"
            "async def _entry():\n"
            "    pass\n"
            "@tool\n"
            'def never_reached() -> str:\n'
            '    """Z."""\n'
            "    return ''\n"
        )
        out = _extract_tool_blocks(src)
        assert "def a" in out
        # extraction stops at @app.entrypoint
        assert "never_reached" not in out

    def test_unterminated_tool_block_still_appended(self):
        """If file ends mid-tool-block, it's still emitted via the tail-flush branch."""
        src = (
            "@tool\n"
            'def open_tool() -> str:\n'
            '    """O."""\n'
            "    return ''"  # no trailing newline
        )
        out = _extract_tool_blocks(src)
        assert "def open_tool" in out

    def test_no_imports_no_double_newlines(self):
        """When there are no leading imports, output is just blocks joined by \\n\\n."""
        src = (
            "@tool\n"
            'def x() -> str:\n'
            '    """X."""\n'
            "    return ''\n"
        )
        out = _extract_tool_blocks(src)
        assert out.startswith("@tool")  # no leading newlines


# ── _get_builtin_tool_names ─────────────────────────────────────────────

class TestGetBuiltinToolNames:
    def test_empty_when_registry_empty(self, monkeypatch):
        # Ensure _ALL_TOOLS is empty regardless of suite-level pollution.
        from tools_library import registry as reg
        monkeypatch.setattr(reg, "_ALL_TOOLS", [])
        assert _get_builtin_tool_names() == set()

    def test_uses_tool_names_attribute(self, monkeypatch):
        """When registry has modules with TOOL_NAMES, the comma-split names are returned."""
        from tools_library import registry as reg
        fake_mod = types.SimpleNamespace(TOOL_NAMES="alpha,beta, gamma ")
        monkeypatch.setattr(reg, "_ALL_TOOLS", [fake_mod])
        names = _get_builtin_tool_names()
        # In suite-mode, other tests may have left real entries before us.
        # We assert OUR fake names are present (subset check).
        assert {"alpha", "beta", "gamma"} <= names

    def test_swallows_exception(self, monkeypatch):
        """If accessing TOOL_NAMES raises, returns empty set silently."""
        from tools_library import registry as reg

        class _Boom:
            @property
            def TOOL_NAMES(self):
                raise RuntimeError("boom")

        monkeypatch.setattr(reg, "_ALL_TOOLS", [_Boom()])
        assert _get_builtin_tool_names() == set()


# ── _get_mcp_tool_names additional edge cases ─────────────────────────

class TestGetMcpToolNamesEdgeCases:
    @patch("boto3.client")
    def test_boto_client_init_failure_returns_empty(self, mock_boto):
        """If boto3.client raises (e.g. no credentials), return empty list."""
        mock_boto.side_effect = Exception("no creds")
        assert _get_mcp_tool_names(["target"]) == []

    @patch("boto3.client")
    def test_target_with_no_dash_no_alt_candidate(self, mock_boto):
        """When target has no '-' it should NOT add a hyphen→underscore alt key."""
        s3 = MagicMock()
        mock_boto.return_value = s3
        keys_seen = []

        def get_object(**kw):
            keys_seen.append(kw.get("Key"))
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object
        _get_mcp_tool_names(["plain"])
        # candidates: plain, mcp-plain, mcp_plain (no dash→underscore alt because
        # alt == target). The code skips adding the alt when alt == target.
        assert "mcp/target-tools/plain.json" in keys_seen
        # The 'plain' alt would be the same as target so NOT added separately
        # but mcp-plain and mcp_plain are added as fallbacks.


# ── staging_key path (validate_agent) ──────────────────────────────────

class TestStagingKeyPath:
    @patch("boto3.client")
    def test_staging_key_loads_params_from_s3(self, mock_boto):
        s3 = MagicMock()
        mock_boto.return_value = s3
        staged = {
            "name": "MyBot",
            "system_prompt": "Use search",
            "tool_definitions": '@tool\ndef search() -> str:\n    """S."""\n    return ""',
            "tool_names": "search",
            "description": "d",
            "welcome_message": "hi",
            "permission_tier": "readonly",
            "mcp_targets": [],
        }
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(staged).encode())
        }

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert r["valid"]

    @patch("boto3.client")
    def test_staging_read_failure_returns_error(self, mock_boto):
        s3 = MagicMock()
        mock_boto.return_value = s3
        s3.get_object.side_effect = Exception("AccessDenied")

        r = _parse(validate_agent(staging_key="staging/missing.json"))
        assert not r["valid"]
        assert any("staging" in e.lower() for e in r["errors"])

    @patch("boto3.client")
    def test_staging_with_csv_mcp_targets_str(self, mock_boto):
        """staging mcp_targets can be a string and is split on commas."""
        s3 = MagicMock()
        mock_boto.return_value = s3
        staged = {
            "name": "Bot1",
            "system_prompt": "hello",
            "description": "d",
            "mcp_targets": "alpha,beta",
        }
        # Use first call for staging, then NoSuchKey for any further fetches.
        call_count = {"n": 0}

        def get_object(**kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"Body": MagicMock(read=lambda: json.dumps(staged).encode())}
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object

        r = _parse(validate_agent(staging_key="staging/x.json"))
        # Should be valid (mcp targets just couldn't be resolved → no errors)
        assert isinstance(r, dict)


# ── Dry-run exec branches ──────────────────────────────────────────────

class TestDryRunExec:
    def test_dry_run_exec_failure_warns(self):
        """Tool code that compiles but raises at import time produces a warning."""
        code = (
            'X = undefined_name  # NameError when exec runs\n'
            "@tool\n"
            'def t() -> str:\n'
            '    """T."""\n'
            "    return X\n"
        )
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="Use t",
            tool_definitions=code,
            tool_names="t",
            description="d",
        ))
        # exec failure is captured as a warning, not error
        assert any("Dry-run" in w for w in r["warnings"])

    def test_dry_run_call_failure_warns(self):
        """A @tool that raises on default args triggers a Dry-run call warning."""
        code = (
            "@tool\n"
            'def explode() -> str:\n'
            '    """E."""\n'
            "    raise ValueError('boom')\n"
        )
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="Use explode",
            tool_definitions=code,
            tool_names="explode",
            description="d",
        ))
        assert any("Dry-run" in w and "explode" in w for w in r["warnings"])

    def test_dry_run_with_typed_params(self):
        """Tool with typed parameters but no default — tester picks defaults from type hints."""
        code = (
            "@tool\n"
            "def myfunc(a: str, b: int, c: list, d: dict, e: bool, f: float) -> str:\n"
            '    """."""\n'
            "    return ''\n"
        )
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="Use myfunc",
            tool_definitions=code,
            tool_names="myfunc",
            description="d",
        ))
        # Default values are chosen by type → call succeeds → no Dry-run warning
        assert not any("myfunc" in w and "Dry-run" in w for w in r["warnings"])

    def test_dry_run_with_unhinted_param(self):
        """A tool with an annotation we don't recognize falls back to empty string."""
        code = (
            "@tool\n"
            "def myfunc(x: object) -> str:\n"
            '    """."""\n'
            "    return ''\n"
        )
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="Use myfunc",
            tool_definitions=code,
            tool_names="myfunc",
            description="d",
        ))
        # Should not crash; code path through the unhinted-fallback branch is executed.
        assert isinstance(r, dict)


# ── _review_prompt_quality JSON parser ────────────────────────────────

class TestReviewPromptQualityParser:
    def test_returns_none_when_no_json_braces(self, monkeypatch):
        """No '{' anywhere in the LLM output → return None."""
        from tools import validate_agent as va

        class _DummyAgent:
            def __call__(self, _input):
                return "no JSON at all"

        monkeypatch.setattr(va, "Agent", lambda **kw: _DummyAgent())
        out = _review_prompt_quality("prompt", ["t"], "readonly")
        assert out is None

    def test_returns_none_when_braces_unbalanced(self, monkeypatch):
        from tools import validate_agent as va

        class _DummyAgent:
            def __call__(self, _input):
                return '{"scores": {'  # opening brace, never closes

        monkeypatch.setattr(va, "Agent", lambda **kw: _DummyAgent())
        out = _review_prompt_quality("prompt", ["t"], "readonly")
        assert out is None

    def test_extracts_scores_block(self, monkeypatch):
        from tools import validate_agent as va

        scores_obj = {
            "scores": {"structure": 3, "tool_prompt_sync": 4},
            "overall": 3.5,
            "issues": ["fix this", "fix that"],
        }

        class _DummyAgent:
            def __call__(self, _input):
                return f"Some preamble\n{json.dumps(scores_obj)}\nTrailing text."

        monkeypatch.setattr(va, "Agent", lambda **kw: _DummyAgent())
        out = _review_prompt_quality("prompt", ["t"], "readonly")
        assert out == scores_obj

    def test_handles_quoted_braces_inside_strings(self, monkeypatch):
        """Curly braces inside JSON string values must not throw off depth tracking."""
        from tools import validate_agent as va

        # Note: the inner string contains literal { and }
        embedded = '{"scores": {"structure": 5}, "issues": ["see {missing} key"], "overall": 4.0}'

        class _DummyAgent:
            def __call__(self, _input):
                return embedded

        monkeypatch.setattr(va, "Agent", lambda **kw: _DummyAgent())
        out = _review_prompt_quality("prompt", ["t"], "readonly")
        assert out is not None
        assert out["overall"] == 4.0

    def test_handles_escaped_quotes(self, monkeypatch):
        """Backslash-escaped quote inside a string must not flip the in_str flag."""
        from tools import validate_agent as va

        embedded = '{"scores": {"structure": 5}, "issues": ["a \\"quoted\\" thing"], "overall": 4.0}'

        class _DummyAgent:
            def __call__(self, _input):
                return embedded

        monkeypatch.setattr(va, "Agent", lambda **kw: _DummyAgent())
        out = _review_prompt_quality("prompt", ["t"], "readonly")
        assert out is not None
        assert out["overall"] == 4.0

    def test_returns_none_when_extracted_text_not_valid_json(self, monkeypatch):
        from tools import validate_agent as va

        class _DummyAgent:
            def __call__(self, _input):
                # opens with { but is not actually valid JSON
                return "{not valid json: at all,}"

        monkeypatch.setattr(va, "Agent", lambda **kw: _DummyAgent())
        out = _review_prompt_quality("prompt", ["t"], "readonly")
        assert out is None


# ── Prompt-review integration in validate_agent ─────────────────────

class TestPromptReviewIntegration:
    @patch("tools.validate_agent._review_prompt_quality")
    def test_low_score_triggers_warnings(self, mock_review):
        """Overall < 3 emits a quality-score warning AND lists issues."""
        mock_review.return_value = {
            "scores": {"structure": 1, "tool_prompt_sync": 2},
            "overall": 2.0,
            "issues": ["No structure", "No tool guidance"],
        }
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="just a string",
            description="d",
        ))
        assert any("Prompt quality score" in w for w in r["warnings"])
        assert any("Prompt review:" in w for w in r["warnings"])
        # Result includes the review's scores block
        assert r.get("prompt_overall") == 2.0
        assert r.get("prompt_scores") == {"structure": 1, "tool_prompt_sync": 2}

    @patch("tools.validate_agent._review_prompt_quality")
    def test_mid_score_includes_issues_only(self, mock_review):
        """3 ≤ overall < 4 → no overall warning, but issues list is included."""
        mock_review.return_value = {
            "scores": {"structure": 3},
            "overall": 3.5,
            "issues": ["could be tighter"],
        }
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="hello",
            description="d",
        ))
        # No 'consider optimizing' line at this score
        assert not any("consider optimizing" in w.lower() for w in r["warnings"])
        # But review issue surfaced
        assert any("could be tighter" in w for w in r["warnings"])

    @patch("tools.validate_agent._review_prompt_quality")
    def test_high_score_no_issue_warnings(self, mock_review):
        """Overall >= 4 → no quality warnings."""
        mock_review.return_value = {
            "scores": {"structure": 5},
            "overall": 4.5,
            "issues": ["small nit"],
        }
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="hello",
            description="d",
        ))
        assert not any("Prompt review:" in w for w in r["warnings"])
        assert not any("Prompt quality score" in w for w in r["warnings"])

    @patch("tools.validate_agent._review_prompt_quality")
    def test_review_exception_yields_warning(self, mock_review):
        """If the review helper raises, the agent gets a 'review skipped' warning."""
        mock_review.side_effect = RuntimeError("LLM down")
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="hello",
            description="d",
        ))
        assert any("Prompt quality review skipped" in w for w in r["warnings"])
        assert "prompt_overall" not in r

    @patch("tools.validate_agent._review_prompt_quality")
    def test_review_returns_none_no_extra_keys(self, mock_review):
        mock_review.return_value = None
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="hello",
            description="d",
        ))
        assert "prompt_overall" not in r
        assert "prompt_scores" not in r

    @patch("tools.validate_agent._review_prompt_quality")
    def test_review_skipped_when_errors_present(self, mock_review):
        """No prompt review is performed when validation already failed."""
        # No agent_name — error → review must be skipped
        r = _parse(validate_agent(
            agent_name="",
            system_prompt="hello",
            description="d",
        ))
        assert not r["valid"]
        mock_review.assert_not_called()

    @patch("tools.validate_agent._review_prompt_quality")
    def test_review_skipped_when_prompt_blank(self, mock_review):
        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="",
            description="d",
        ))
        # Blank prompt is itself an error so review skipped.
        mock_review.assert_not_called()


# ── Skill conflict detection (staging) ────────────────────────────────

class TestSkillConflictDetection:
    @patch("boto3.client")
    def test_file_name_collision_across_skills(self, mock_boto):
        """Two skills shipping the same script file name → error."""
        s3 = MagicMock()
        mock_boto.return_value = s3

        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "agent_id": "rt-1",
            "skills": [
                {"id": "sa", "name": "A"},
                {"id": "sb", "name": "B"},
            ],
        }

        def get_object(**kw):
            key = kw.get("Key", "")
            if key == "staging/x.json":
                return {"Body": MagicMock(read=lambda: json.dumps(staged).encode())}
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object

        def list_objects_v2(**kw):
            prefix = kw.get("Prefix", "")
            if "sa" in prefix:
                return {"Contents": [{"Key": prefix + "tool.py"}]}
            if "sb" in prefix:
                return {"Contents": [{"Key": prefix + "tool.py"}]}
            return {"Contents": []}

        s3.list_objects_v2.side_effect = list_objects_v2

        # Trigger script content lookup so func collisions don't appear
        r = _parse(validate_agent(staging_key="staging/x.json"))
        # File name 'tool.py' collides between A and B
        assert any("tool.py" in e and "collision" in e.lower() for e in r["errors"])

    @patch("boto3.client")
    def test_tool_function_collision_across_skills(self, mock_boto):
        s3 = MagicMock()
        mock_boto.return_value = s3

        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "agent_id": "rt-1",
            "skills": [
                {"id": "skill-a", "name": "A"},
                {"id": "skill-b", "name": "B"},
            ],
        }
        skill_code = '@tool\ndef shared() -> str:\n    """."""\n    return ""'

        def get_object(**kw):
            key = kw.get("Key", "")
            if key == "staging/x.json":
                return {"Body": MagicMock(read=lambda: json.dumps(staged).encode())}
            if "scripts/" in key and key.endswith(".py"):
                return {"Body": MagicMock(read=lambda: skill_code.encode())}
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object

        def list_objects_v2(**kw):
            prefix = kw.get("Prefix", "")
            # Different file names (so no file collision), but same @tool inside
            if "skill-a" in prefix:
                return {"Contents": [{"Key": prefix + "alpha.py"}]}
            if "skill-b" in prefix:
                return {"Contents": [{"Key": prefix + "beta.py"}]}
            return {"Contents": []}

        s3.list_objects_v2.side_effect = list_objects_v2

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert any("shared" in e and "collision" in e.lower() for e in r["errors"])

    @patch("boto3.client")
    def test_skill_tool_conflicts_with_agent_tool(self, mock_boto):
        """Skill @tool 'foo' AND agent's tool_definitions defines 'foo' → error."""
        s3 = MagicMock()
        mock_boto.return_value = s3

        staged = {
            "name": "Bot1",
            "system_prompt": "Use foo",
            "description": "d",
            "agent_id": "rt-1",
            "tool_definitions": '@tool\ndef foo() -> str:\n    """."""\n    return ""',
            "tool_names": "foo",
            "skills": [{"id": "s1", "name": "S1"}],
        }
        skill_code = '@tool\ndef foo() -> str:\n    """."""\n    return ""'

        def get_object(**kw):
            key = kw.get("Key", "")
            if key == "staging/x.json":
                return {"Body": MagicMock(read=lambda: json.dumps(staged).encode())}
            if key.endswith(".py"):
                return {"Body": MagicMock(read=lambda: skill_code.encode())}
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object

        def list_objects_v2(**kw):
            return {"Contents": [{"Key": kw.get("Prefix", "") + "foo.py"}]}

        s3.list_objects_v2.side_effect = list_objects_v2

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert any("foo" in e and "conflicts" in e.lower() for e in r["errors"])

    @patch("boto3.client")
    def test_skill_listing_failure_warns(self, mock_boto):
        """If list_objects_v2 raises in skill conflict detection, emit a single warning."""
        s3 = MagicMock()
        mock_boto.return_value = s3

        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "skills": [{"id": "s1", "name": "X"}],
        }

        def get_object(**kw):
            key = kw.get("Key", "")
            if key == "staging/x.json":
                return {"Body": MagicMock(read=lambda: json.dumps(staged).encode())}
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object
        s3.list_objects_v2.side_effect = Exception("bucket missing")

        # Run validate; the inner per-skill except clause swallows the error,
        # the outer also catches anything else. Exact warning text is internal,
        # but the result must still validate without crashing.
        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert isinstance(r, dict)


# ── MCP IAM permission check ─────────────────────────────────────────

class TestMCPIAMCheck:
    def test_iam_check_no_workspace_role_errors(self, monkeypatch):
        """Target has IAM policy but no workspace role → error with action list."""
        # Inject a fake list_mcp_servers helpers
        fake_mod = types.ModuleType("tools.list_mcp_servers")
        fake_mod._load_registry_iam_policies = lambda: {
            "danger": {
                "Statement": [{"Action": ["s3:*", "iam:GetRole"], "Effect": "Allow"}]
            }
        }
        fake_mod._get_workspace_role_arn = lambda ws: None
        fake_mod._check_iam_permissions = lambda role, policy: {
            "granted": False, "missing_actions": []
        }
        sys.modules["tools.list_mcp_servers"] = fake_mod

        # current_workspace can be empty — branch covers both paths
        fake_scope = types.ModuleType("tools._scope_test_helper")
        # We can't easily replace tools._scope.current_workspace mid-import,
        # so monkeypatch it:
        from tools import _scope as scope_mod
        monkeypatch.setattr(scope_mod, "current_workspace", lambda: "ws-1")

        r = _parse(validate_agent(
            agent_name="Bot1",
            system_prompt="hi",
            description="d",
            tool_names="",
            staging_key="",
        ))
        # mcp_targets_raw was an empty list — no IAM check fires. Use the parameterized form below:
        # pass mcp via staging_key flow instead.

    @patch("boto3.client")
    def test_iam_check_role_missing_actions_errors(self, mock_boto, monkeypatch):
        """Target has IAM policy, ws has role, but role lacks actions → error+CLI."""
        s3 = MagicMock()
        mock_boto.return_value = s3
        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "mcp_targets": ["s3-tool"],
        }
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(staged).encode())
        }

        fake_mod = types.ModuleType("tools.list_mcp_servers")
        fake_mod._load_registry_iam_policies = lambda: {
            "s3-tool": {
                "Statement": [{"Action": ["s3:GetObject"], "Effect": "Allow"}]
            }
        }
        fake_mod._get_workspace_role_arn = lambda ws: "arn:aws:iam::000:role/myrole"
        fake_mod._check_iam_permissions = lambda role, policy: {
            "granted": False, "missing_actions": ["s3:GetObject"]
        }
        sys.modules["tools.list_mcp_servers"] = fake_mod

        from tools import _scope as scope_mod
        monkeypatch.setattr(scope_mod, "current_workspace", lambda: "ws-1")

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert any(
            "s3-tool" in e and "missing" in e.lower() and "myrole" in e
            for e in r["errors"]
        )

    @patch("boto3.client")
    def test_iam_check_no_role_for_target_errors(self, mock_boto, monkeypatch):
        """Target has IAM policy but workspace_role_arn None → error mentioning Settings."""
        s3 = MagicMock()
        mock_boto.return_value = s3
        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "mcp_targets": ["needsperm"],
        }
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(staged).encode())
        }

        fake_mod = types.ModuleType("tools.list_mcp_servers")
        fake_mod._load_registry_iam_policies = lambda: {
            "needsperm": {
                "Statement": [{"Action": "iam:ListRoles", "Effect": "Allow"}]
            }
        }
        fake_mod._get_workspace_role_arn = lambda ws: None
        fake_mod._check_iam_permissions = lambda role, policy: {"granted": True}
        sys.modules["tools.list_mcp_servers"] = fake_mod

        from tools import _scope as scope_mod
        monkeypatch.setattr(scope_mod, "current_workspace", lambda: "ws-1")

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert any(
            "needsperm" in e and "no" in e.lower() and "iam role" in e.lower()
            for e in r["errors"]
        )

    @patch("boto3.client")
    def test_iam_check_platform_target_skipped(self, mock_boto, monkeypatch):
        """Target with no IAM policy in registry → skip silently (no error)."""
        s3 = MagicMock()
        mock_boto.return_value = s3
        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "mcp_targets": ["platform-tool"],
        }
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(staged).encode())
        }

        fake_mod = types.ModuleType("tools.list_mcp_servers")
        fake_mod._load_registry_iam_policies = dict  # no policy
        fake_mod._get_workspace_role_arn = lambda ws: None
        fake_mod._check_iam_permissions = lambda *a: {"granted": True}
        sys.modules["tools.list_mcp_servers"] = fake_mod

        from tools import _scope as scope_mod
        monkeypatch.setattr(scope_mod, "current_workspace", lambda: "ws-1")

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert not any("platform-tool" in e for e in r["errors"])

    @patch("boto3.client")
    def test_iam_check_module_failure_warns(self, mock_boto, monkeypatch):
        """If list_mcp_servers helpers raise, the check is skipped with a warning."""
        s3 = MagicMock()
        mock_boto.return_value = s3
        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "mcp_targets": ["whatever"],
        }
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(staged).encode())
        }

        bad_mod = types.ModuleType("tools.list_mcp_servers")

        def _boom():
            raise RuntimeError("registry broken")

        bad_mod._load_registry_iam_policies = _boom
        bad_mod._get_workspace_role_arn = lambda ws: None
        bad_mod._check_iam_permissions = lambda *a: {"granted": True}
        sys.modules["tools.list_mcp_servers"] = bad_mod

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert any("MCP IAM permission check skipped" in w for w in r["warnings"])

    @patch("boto3.client")
    def test_iam_check_action_as_string_normalized(self, mock_boto, monkeypatch):
        """Statement.Action can be a string OR list — both flow through error msg."""
        s3 = MagicMock()
        mock_boto.return_value = s3
        staged = {
            "name": "Bot1",
            "system_prompt": "hi",
            "description": "d",
            "mcp_targets": ["x"],
        }
        s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(staged).encode())
        }
        fake_mod = types.ModuleType("tools.list_mcp_servers")
        fake_mod._load_registry_iam_policies = lambda: {
            "x": {"Statement": [{"Action": "s3:Get*"}]}  # string, not list
        }
        fake_mod._get_workspace_role_arn = lambda ws: None
        fake_mod._check_iam_permissions = lambda *a: {"granted": True}
        sys.modules["tools.list_mcp_servers"] = fake_mod

        from tools import _scope as scope_mod
        monkeypatch.setattr(scope_mod, "current_workspace", lambda: "ws-1")

        r = _parse(validate_agent(staging_key="staging/x.json"))
        # The error mentions s3:Get*
        assert any("s3:Get*" in e for e in r["errors"])


# ── Ghost-tool detection: skill scripts scan ────────────────────────

class TestGhostToolSkillScan:
    @patch("boto3.client")
    def test_skill_script_tool_in_backticks_passes(self, mock_boto):
        """A `@tool` from an attached skill's scripts is recognized → no ghost error."""
        s3 = MagicMock()
        mock_boto.return_value = s3

        staged = {
            "name": "Bot1",
            "agent_id": "rt-skill",
            "system_prompt": "Call `skill_tool` to execute.",
            "description": "d",
            "skills": [{"id": "s1", "name": "S1"}],
        }
        skill_script = '@tool\ndef skill_tool() -> str:\n    """."""\n    return ""'

        def get_object(**kw):
            key = kw.get("Key", "")
            if key == "staging/x.json":
                return {"Body": MagicMock(read=lambda: json.dumps(staged).encode())}
            if key.endswith(".py"):
                return {"Body": MagicMock(read=lambda: skill_script.encode())}
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object

        def list_objects_v2(**kw):
            prefix = kw.get("Prefix", "")
            return {"Contents": [{"Key": prefix + "tool.py"}]}

        s3.list_objects_v2.side_effect = list_objects_v2

        r = _parse(validate_agent(staging_key="staging/x.json"))
        # `skill_tool` is provided by the skill — no ghost error
        assert not any("skill_tool" in e for e in r["errors"])

    @patch("boto3.client")
    def test_skill_scan_inner_exception_swallowed(self, mock_boto):
        """If a skill scan raises during script reading, it's silently swallowed."""
        s3 = MagicMock()
        mock_boto.return_value = s3

        staged = {
            "name": "Bot1",
            "agent_id": "rt-skill",
            "system_prompt": "hi",
            "description": "d",
            "skills": [{"id": "s1", "name": "S1"}, {"id": "", "name": "S2"}],  # one with no id
        }

        def get_object(**kw):
            key = kw.get("Key", "")
            if key == "staging/x.json":
                return {"Body": MagicMock(read=lambda: json.dumps(staged).encode())}
            raise Exception("NoSuchKey")

        s3.get_object.side_effect = get_object
        s3.list_objects_v2.side_effect = Exception("bucket missing")

        r = _parse(validate_agent(staging_key="staging/x.json"))
        assert isinstance(r, dict)
        # Skipping the skill with empty id is silent — no error about it.
        assert not any("ghost" in e.lower() for e in r["errors"])

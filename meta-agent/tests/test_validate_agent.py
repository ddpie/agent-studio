"""Tests for validate_agent — pure validation logic, no AWS/LLM calls."""

import json
import sys
import types
from unittest.mock import patch, MagicMock
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
sys.modules.setdefault("config", _mock_config)

# Mock tools_library so _get_builtin_tool_names works in isolation
_mock_registry = types.ModuleType("tools_library.registry")
_mock_registry._ALL_TOOLS = []
_mock_tl = types.ModuleType("tools_library")
_mock_tl.registry = _mock_registry
sys.modules.setdefault("tools_library", _mock_tl)
sys.modules.setdefault("tools_library.registry", _mock_registry)

from tools.validate_agent import validate_agent, _WRITE_RE, _UNAVAILABLE_LIBS, _get_mcp_tool_names


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

class TestWritePatternDetection:
    @pytest.mark.parametrize("snippet,keyword", [
        ("table.put_item(Item={})", "put_item"),
        ("s3.delete_object(Bucket='b', Key='k')", "delete_object"),
        ("cursor.execute('INSERT INTO t VALUES (1)')", "INSERT INTO"),
        ("cursor.execute('DELETE FROM t WHERE id=1')", "DELETE FROM"),
        ("requests.post('https://example.com')", ".post("),
    ])
    def test_write_pattern_detected_for_readonly(self, snippet, keyword):
        code = f'@tool\ndef risky() -> str:\n    """R."""\n    {snippet}\n    return "done"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="hello",
            tool_definitions=code, tool_names="risky",
            permission_tier="readonly", description="d",
        ))
        assert any("write operation" in w.lower() for w in r["warnings"])

    def test_write_pattern_ok_for_data_access(self):
        code = '@tool\ndef writer() -> str:\n    """W."""\n    table.put_item(Item={})\n    return "done"'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use writer",
            tool_definitions=code, tool_names="writer",
            permission_tier="data-access", description="d",
        ))
        # data-access tier should NOT warn about write ops
        assert not any("write operation" in w.lower() for w in r["warnings"])

    def test_write_pattern_detected_for_basic_tier(self):
        code = '@tool\ndef w() -> str:\n    """W."""\n    table.put_item(Item={})\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use w",
            tool_definitions=code, tool_names="w",
            permission_tier="basic", description="d",
        ))
        assert any("write operation" in w.lower() for w in r["warnings"])

    def test_no_write_warning_without_permission_tier(self):
        code = '@tool\ndef w() -> str:\n    """W."""\n    table.put_item(Item={})\n    return ""'
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="Use w",
            tool_definitions=code, tool_names="w",
            permission_tier="", description="d",
        ))
        # No permission tier set — no write warning
        assert not any("write operation" in w.lower() for w in r["warnings"])


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

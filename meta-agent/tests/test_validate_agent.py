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

from tools.validate_agent import validate_agent, _WRITE_RE, _UNAVAILABLE_LIBS


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


# ── Long prompt warning ──────────────────────────────────────────

class TestLongPrompt:
    def test_very_long_prompt_warns(self):
        r = _parse(validate_agent(
            agent_name="Agent1", system_prompt="x" * 10001, description="d",
        ))
        assert any("very long" in w.lower() for w in r["warnings"])


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

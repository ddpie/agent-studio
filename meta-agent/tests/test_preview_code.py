"""Tests for preview_assembled_code tool."""
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

_mock_strands_models = sys.modules.get("strands.models") or types.ModuleType("strands.models")
if not hasattr(_mock_strands_models, "BedrockModel"):
    _mock_strands_models.BedrockModel = MagicMock
sys.modules["strands.models"] = _mock_strands_models

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
    monkeypatch.setattr(_scope, "_creator_language", "en", raising=False)


def _stub_validate(monkeypatch, valid=True, errors=None, warnings=None):
    from tools import preview_code as mod
    monkeypatch.setattr(mod, "validate_agent_files", lambda *a, **kw: {
        "valid": valid,
        "errors": errors or [],
        "warnings": warnings or [],
    })


def _patch_boto3(monkeypatch, mod, s3_mock):
    """Make every `boto3.client('s3', ...)` call inside the tool return s3_mock."""
    fake_boto3 = MagicMock()
    fake_boto3.client = lambda svc, **_: s3_mock
    # The tool does `import boto3` twice (top of file and again inside) so
    # patch sys.modules so both bind to the same mock.
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)


def test_preview_with_direct_args_returns_file_sizes(monkeypatch):
    from tools import preview_code as mod

    _stub_validate(monkeypatch)
    s3 = MagicMock()
    s3.put_object.return_value = {}
    _patch_boto3(monkeypatch, mod, s3)

    out = json.loads(mod.preview_assembled_code(
        system_prompt="You are a helper.",
        tool_definitions='@tool\ndef noop() -> str:\n    """Nothing."""\n    return ""',
        tool_names="noop",
    ))

    assert out["valid"] is True
    assert "preview_key" in out
    assert out["preview_key"].startswith("agents/_preview/preview-")
    files = out["files"]
    assert files["main.py"] > 0
    assert files["tools.py"] > 0
    assert files["prompt.txt"] > 0
    assert files["config.json"] > 0
    assert out["errors"] == []
    assert out["warnings"] == []
    # File written exactly once
    assert s3.put_object.call_count == 1


def test_preview_uses_mcp_template_when_gateway_url(monkeypatch):
    """Gateway URL flips main_py to MCP variant; config.json gains gateway_url."""
    from tools import preview_code as mod

    captured = {}

    def fake_validate(main_py, tools_py, prompt_txt, config_json):
        captured["main_py"] = main_py
        captured["config_json"] = config_json
        return {"valid": True, "errors": [], "warnings": []}

    monkeypatch.setattr(mod, "validate_agent_files", fake_validate)
    s3 = MagicMock()
    _patch_boto3(monkeypatch, mod, s3)

    mod.preview_assembled_code(
        system_prompt="hi",
        tool_definitions="",
        tool_names="",
        gateway_url="https://gw.example.com",
    )

    assert captured["main_py"] == mod.MAIN_PY_MCP_TEMPLATE
    cfg = json.loads(captured["config_json"])
    assert cfg["gateway_url"] == "https://gw.example.com"
    # MODEL_ID is whatever the loaded config has — could be "mock-model"
    # in isolation or the real default when the real config got imported
    # first by another test. Both are valid, just verify a non-empty str.
    assert isinstance(cfg["model_id"], str) and cfg["model_id"]


def test_preview_falls_back_to_default_prompt_when_blank(monkeypatch):
    """Empty system_prompt → default 'You are a helpful assistant.'"""
    from tools import preview_code as mod

    captured = {}

    def fake_validate(main_py, tools_py, prompt_txt, config_json):
        captured["prompt"] = prompt_txt
        return {"valid": True, "errors": [], "warnings": []}

    monkeypatch.setattr(mod, "validate_agent_files", fake_validate)
    s3 = MagicMock()
    _patch_boto3(monkeypatch, mod, s3)

    mod.preview_assembled_code(system_prompt="")
    assert "You are a helpful assistant." in captured["prompt"]


def test_preview_reads_staging_when_provided(monkeypatch):
    from tools import preview_code as mod

    staging = {
        "system_prompt": "Staged prompt",
        "tool_definitions": '@tool\ndef hi() -> str:\n    """h."""\n    return "h"',
        "tool_names": "hi",
        "gateway_url": "",
    }

    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(staging).encode("utf-8"))
    }
    s3.put_object.return_value = {}
    _patch_boto3(monkeypatch, mod, s3)

    captured = {}

    def fake_validate(main_py, tools_py, prompt_txt, config_json):
        captured["prompt"] = prompt_txt
        captured["tools_py"] = tools_py
        return {"valid": True, "errors": [], "warnings": []}

    monkeypatch.setattr(mod, "validate_agent_files", fake_validate)

    out = json.loads(mod.preview_assembled_code(
        staging_key="staging/abc.json",
    ))

    # Preview key derives the basename ("abc") from the staging key
    assert out["preview_key"].startswith("agents/_preview/abc-")
    assert "Staged prompt" in captured["prompt"]
    assert "def hi" in captured["tools_py"]


def test_preview_returns_error_on_staging_read_failure(monkeypatch):
    from tools import preview_code as mod

    s3 = MagicMock()
    s3.get_object.side_effect = Exception("NoSuchKey: missing.json")
    _patch_boto3(monkeypatch, mod, s3)

    out = json.loads(mod.preview_assembled_code(staging_key="staging/missing.json"))
    assert "error" in out
    assert "Failed to read staging config" in out["error"]


def test_preview_propagates_validation_errors(monkeypatch):
    from tools import preview_code as mod

    _stub_validate(monkeypatch, valid=False, errors=["SyntaxError: bad"], warnings=["w1"])
    s3 = MagicMock()
    _patch_boto3(monkeypatch, mod, s3)

    out = json.loads(mod.preview_assembled_code(
        system_prompt="hi", tool_definitions="def broken(:", tool_names="",
    ))
    assert out["valid"] is False
    assert out["errors"] == ["SyntaxError: bad"]
    assert out["warnings"] == ["w1"]


def test_preview_appends_guidelines_when_missing(monkeypatch):
    """The base guidelines block is appended unless already present in prompt."""
    from tools import preview_code as mod

    monkeypatch.setattr(mod, "get_base_guidelines", lambda lang: "@@GUIDELINES@@")

    captured = {}

    def fake_validate(main_py, tools_py, prompt_txt, config_json):
        captured["prompt"] = prompt_txt
        return {"valid": True, "errors": [], "warnings": []}

    monkeypatch.setattr(mod, "validate_agent_files", fake_validate)
    s3 = MagicMock()
    _patch_boto3(monkeypatch, mod, s3)

    # Case 1: missing guidelines → appended
    mod.preview_assembled_code(system_prompt="Hello.")
    assert "Hello." in captured["prompt"]
    assert "@@GUIDELINES@@" in captured["prompt"]

    # Case 2: guidelines already in prompt → not duplicated
    mod.preview_assembled_code(system_prompt="Hello. @@GUIDELINES@@")
    # Should appear exactly once
    assert captured["prompt"].count("@@GUIDELINES@@") == 1

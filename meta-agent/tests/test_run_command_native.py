"""Test run_command routes to invoke_code_interpreter."""
import json
import sys
import types
from unittest.mock import MagicMock, patch

# Stub strands module for the BUILTIN_TOOLS_CODE string to import.
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

# Ensure mocked config for template import chain.
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


def _exec_builtin(monkeypatch):
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE
    # Provide a minimal namespace where the BUILTIN_TOOLS_CODE string executes.
    ns: dict = {"__name__": "builtin_tools_test"}
    exec(BUILTIN_TOOLS_CODE, ns)
    return ns


def test_run_command_python_via_code_interpreter(monkeypatch):
    """run_command('print(1+1)', 'python') should call invoke_code_interpreter."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")

    data_mock = MagicMock()
    data_mock.start_code_interpreter_session.return_value = {"sessionId": "sess-1"}
    data_mock.invoke_code_interpreter.return_value = {
        "stream": [{"result": {"structuredContent": {"stdout": "2\n", "stderr": "", "exitCode": 0}, "content": []}}]
    }
    with patch("boto3.client", return_value=data_mock):
        ns = _exec_builtin(monkeypatch)
        ns["run_command"].__wrapped__ = getattr(ns["run_command"], "__wrapped__", ns["run_command"])
        result = ns["run_command"]("print(1+1)", "python")
    assert "2" in result
    assert data_mock.start_code_interpreter_session.called
    assert data_mock.invoke_code_interpreter.called


def test_run_command_returns_error_on_nonzero_exit(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    data_mock = MagicMock()
    data_mock.start_code_interpreter_session.return_value = {"sessionId": "sess-1"}
    data_mock.invoke_code_interpreter.return_value = {
        "stream": [{"result": {"structuredContent": {"stdout": "", "stderr": "boom", "exitCode": 1}, "content": []}}]
    }
    with patch("boto3.client", return_value=data_mock):
        ns = _exec_builtin(monkeypatch)
        result = ns["run_command"]("1/0", "python")
    parsed = json.loads(result) if result.startswith("{") else {"output": result}
    assert parsed.get("error") or "Exit code 1" in result


def test_run_command_returns_error_when_ci_id_missing(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_CODE_INTERPRETER_ID", raising=False)
    ns = _exec_builtin(monkeypatch)
    result = ns["run_command"]("print(1)", "python")
    assert "CODE_INTERPRETER_ID" in result or "not configured" in result.lower()

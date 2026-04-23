"""Test check_capabilities() builtin — the preflight probe."""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch


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


def _exec_builtin():
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE
    ns: dict = {"__name__": "builtin_tools_test"}
    exec(BUILTIN_TOOLS_CODE, ns)
    return ns


def test_check_capabilities_reports_missing_ci_id(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_STUDIO_CODE_INTERPRETER_ID", raising=False)
    ns = _exec_builtin()
    ns["_CACHE_ROOT"] = tmp_path  # empty
    ns["_CACHE_READY"] = True

    out = ns["check_capabilities"]()
    data = json.loads(out)
    assert data["code_interpreter"]["ready"] is False
    assert "not configured" in data["code_interpreter"]["error"].lower()
    assert data["skills"] == []


def test_check_capabilities_reports_no_session(monkeypatch, tmp_path):
    """CI id is configured but run_command has not been called yet."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    ns = _exec_builtin()
    ns["_CACHE_ROOT"] = tmp_path
    ns["_CACHE_READY"] = True
    # No _session_id attached to run_command → ci_fs.session_id() returns None

    out = ns["check_capabilities"]()
    data = json.loads(out)
    assert data["code_interpreter"]["ready"] is False
    assert "no active session" in data["code_interpreter"]["error"].lower()


def test_check_capabilities_lists_skills_from_cache(monkeypatch, tmp_path):
    """Populate the on-disk skill cache and make sure it shows up as loadable."""
    monkeypatch.delenv("AGENT_STUDIO_CODE_INTERPRETER_ID", raising=False)

    index = [
        {"id": "skill-1", "name": "ppt-generator", "description": "makes ppt"},
        {"id": "skill-2", "name": "gone", "description": "deleted", "deleted": True},
    ]
    (tmp_path / "index.json").write_text(json.dumps(index))
    skill_dir = tmp_path / "skill-1"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "render.py").write_text("print('hi')")

    ns = _exec_builtin()
    ns["_CACHE_ROOT"] = tmp_path
    ns["_CACHE_READY"] = True

    out = ns["check_capabilities"]()
    data = json.loads(out)
    names = {s["name"]: s for s in data["skills"]}
    assert "ppt-generator" in names
    assert names["ppt-generator"]["loadable"] is True
    assert names["ppt-generator"]["file_count"] == 2
    assert "gone" not in names  # deleted skills filtered out


def test_check_capabilities_probes_ci_when_session_exists(monkeypatch, tmp_path):
    """With a live CI session, the probe should roundtrip sys.version."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")

    probe_out = json.dumps({"python": "3.10.12", "cwd": "/opt/amazon/genesis1p-tools/var"})
    ci = MagicMock()
    ci.invoke_code_interpreter.return_value = {
        "stream": [{"result": {"structuredContent": {"stdout": probe_out, "stderr": "", "exitCode": 0}, "content": []}}]
    }

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin()
        ns["_CACHE_ROOT"] = tmp_path
        ns["_CACHE_READY"] = True
        ns["run_command"]._session_id = "sess-active"
        out = ns["check_capabilities"]()

    data = json.loads(out)
    assert data["code_interpreter"]["ready"] is True
    assert data["code_interpreter"]["python_version"] == "3.10.12"
    assert data["code_interpreter"]["session_id"] == "sess-active"

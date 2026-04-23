"""Test run_skill_script — skill staging + execution flow."""
import json
import sys
import types
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


def _seed_skill_cache(tmp_path, skill_id="s-1", name="ppt-generator"):
    index = [{"id": skill_id, "name": name, "description": "test"}]
    (tmp_path / "index.json").write_text(json.dumps(index))
    skill_dir = tmp_path / skill_id
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "render.py").write_text("print('rendered')")


def test_run_skill_script_rejects_unknown_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_skill_cache(tmp_path)

    # Mock boto3 — start_session returns an id, subsequent invokes succeed.
    ci = MagicMock()
    ci.start_code_interpreter_session.return_value = {"sessionId": "sess-1"}
    ci.invoke_code_interpreter.return_value = {
        "stream": [{"result": {"structuredContent": {"stdout": "", "stderr": "", "exitCode": 0}, "content": []}}]
    }

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin()
        ns["_CACHE_ROOT"] = tmp_path
        ns["_CACHE_READY"] = True
        result = ns["run_skill_script"]("no-such-skill", "scripts/x.py")

    # Strands-native error shape
    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "no-such-skill" in result["content"][0]["text"]


def test_run_skill_script_stages_then_executes(monkeypatch, tmp_path):
    """Happy path: skill staged, script executed from its remote dir."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_skill_cache(tmp_path)

    calls = []
    ci = MagicMock()
    ci.start_code_interpreter_session.return_value = {"sessionId": "sess-1"}

    def fake_invoke(**kwargs):
        calls.append(kwargs)
        if kwargs.get("name") == "writeFiles":
            return {"stream": [{"result": {"content": []}}]}
        return {"stream": [{"result": {"structuredContent": {"stdout": "rendered\n", "stderr": "", "exitCode": 0}, "content": []}}]}

    ci.invoke_code_interpreter.side_effect = fake_invoke

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin()
        ns["_CACHE_ROOT"] = tmp_path
        ns["_CACHE_READY"] = True
        result = ns["run_skill_script"]("ppt-generator", "scripts/render.py")

    # Expect: (1) boot run_command, (2) writeFiles to stage, (3) run the script
    names = [c["name"] for c in calls]
    assert "writeFiles" in names, f"skill never staged, got: {names}"
    assert names.count("executeCode") >= 2, f"expected boot + script exec, got: {names}"

    # Final invocation should run the script from its staged directory
    last_exec = [c for c in calls if c["name"] == "executeCode"][-1]
    code = last_exec["arguments"]["code"]
    assert "skills/ppt-generator" in code
    assert "scripts/render.py" in code
    assert "rendered" in result  # stdout surfaces through


def test_run_skill_script_caches_staging_per_session(monkeypatch, tmp_path):
    """Second call on same session must NOT re-stage."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_skill_cache(tmp_path)

    calls = []
    ci = MagicMock()
    ci.start_code_interpreter_session.return_value = {"sessionId": "sess-1"}

    def fake_invoke(**kwargs):
        calls.append(kwargs["name"])
        if kwargs["name"] == "writeFiles":
            return {"stream": [{"result": {"content": []}}]}
        return {"stream": [{"result": {"structuredContent": {"stdout": "ok\n", "stderr": "", "exitCode": 0}, "content": []}}]}

    ci.invoke_code_interpreter.side_effect = fake_invoke

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin()
        ns["_CACHE_ROOT"] = tmp_path
        ns["_CACHE_READY"] = True
        ns["run_skill_script"]("ppt-generator", "scripts/render.py")
        staged_once = calls.count("writeFiles")
        ns["run_skill_script"]("ppt-generator", "scripts/render.py")
        staged_twice = calls.count("writeFiles")

    assert staged_once == staged_twice, "skill should stage once per session"


def test_run_skill_script_rejects_blank_args():
    ns = _exec_builtin()
    result = ns["run_skill_script"]("", "scripts/x.py")
    assert isinstance(result, dict) and result["status"] == "error"
    result = ns["run_skill_script"]("name", "")
    assert isinstance(result, dict) and result["status"] == "error"

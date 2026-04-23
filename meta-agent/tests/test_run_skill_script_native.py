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


def _exec_builtin(agent_id: str = "test-agent"):
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE
    ns: dict = {"__name__": "builtin_tools_test"}
    exec(BUILTIN_TOOLS_CODE, ns)
    ns["_AGENT_ID"] = agent_id
    return ns


def _seed_local_skill(tmp_path, skill_id="s-1", name="ppt-generator"):
    """Pre-populate local cache so _ensure_skill_materialized is a no-op."""
    skill_dir = tmp_path / skill_id
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# skill")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "render.py").write_text("print('rendered')")


def _install_manifest(ns, skills):
    """Bypass the S3 manifest read by pre-seeding _SKILLS_MANIFEST."""
    ns["_SKILLS_MANIFEST"] = skills


def test_run_skill_script_rejects_unknown_skill(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_local_skill(tmp_path)

    ci = MagicMock()
    ci.start_code_interpreter_session.return_value = {"sessionId": "sess-1"}
    ci.invoke_code_interpreter.return_value = {
        "stream": [{"result": {"structuredContent": {"stdout": "", "stderr": "", "exitCode": 0}, "content": []}}]
    }

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin()
        ns["_CACHE_ROOT"] = tmp_path
        _install_manifest(ns, [{"id": "s-1", "name": "ppt-generator"}])
        result = ns["run_skill_script"]("no-such-skill", "scripts/x.py")

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "no-such-skill" in result["content"][0]["text"]


def test_run_skill_script_stages_then_executes(monkeypatch, tmp_path):
    """Happy path: skill staged into CI, script executed from its remote dir."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_local_skill(tmp_path)

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
        _install_manifest(ns, [{"id": "s-1", "name": "ppt-generator"}])
        # Pre-mark skill as already cached in local so staging skips S3
        ns["_CACHED_SKILLS"].add("s-1")
        result = ns["run_skill_script"]("ppt-generator", "scripts/render.py")

    names = [c["name"] for c in calls]
    assert "writeFiles" in names, f"skill never staged into CI, got: {names}"
    assert names.count("executeCode") >= 2, f"expected boot + script exec, got: {names}"

    last_exec = [c for c in calls if c["name"] == "executeCode"][-1]
    code = last_exec["arguments"]["code"]
    assert "skills/ppt-generator" in code
    assert "scripts/render.py" in code
    assert "rendered" in result


def test_run_skill_script_caches_staging_per_session(monkeypatch, tmp_path):
    """Second call on same session must NOT re-stage."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_local_skill(tmp_path)

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
        _install_manifest(ns, [{"id": "s-1", "name": "ppt-generator"}])
        ns["_CACHED_SKILLS"].add("s-1")
        ns["run_skill_script"]("ppt-generator", "scripts/render.py")
        staged_once = calls.count("writeFiles")
        ns["run_skill_script"]("ppt-generator", "scripts/render.py")
        staged_twice = calls.count("writeFiles")

    assert staged_once == staged_twice, "skill should stage once per session"


def test_run_skill_script_uses_absolute_path_resistant_to_cwd_drift(monkeypatch, tmp_path):
    """Repeat invocations must use absolute paths — not relative ones that
    accumulate when the CI session's cwd drifts (the classic persistent
    Jupyter-kernel bug that produces paths like
    ``.../skills/foo/skills/foo/...``)."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_local_skill(tmp_path)

    calls = []
    ci = MagicMock()
    ci.start_code_interpreter_session.return_value = {"sessionId": "sess-drift"}
    SANDBOX_ROOT = "/opt/amazon/genesis1p-tools/var"

    def fake_invoke(**kwargs):
        calls.append(kwargs)
        name = kwargs["name"]
        if name == "writeFiles":
            return {"stream": [{"result": {"content": []}}]}
        # anchor probe returns the sandbox root
        code = kwargs.get("arguments", {}).get("code", "")
        if "__CI_ANCHOR__" in code:
            return {"stream": [{"result": {"structuredContent": {
                "stdout": f"__CI_ANCHOR__{SANDBOX_ROOT}__CI_ANCHOR_END__",
                "stderr": "", "exitCode": 0,
            }, "content": []}}]}
        return {"stream": [{"result": {"structuredContent": {"stdout": "ok\n", "stderr": "", "exitCode": 0}, "content": []}}]}

    ci.invoke_code_interpreter.side_effect = fake_invoke

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin()
        ns["_CACHE_ROOT"] = tmp_path
        _install_manifest(ns, [{"id": "s-1", "name": "ppt-generator"}])
        ns["_CACHED_SKILLS"].add("s-1")
        ns["run_skill_script"]("ppt-generator", "scripts/render.py")
        ns["run_skill_script"]("ppt-generator", "scripts/render.py")

    # Both exec calls should use the SAME absolute path (no drift/doubling)
    exec_codes = [c["arguments"]["code"] for c in calls if c["name"] == "executeCode" and "runpy.run_path" in c.get("arguments", {}).get("code", "")]
    expected_abs = f"{SANDBOX_ROOT}/skills/ppt-generator"
    for code in exec_codes:
        assert expected_abs in code, f"expected {expected_abs!r} in code, got:\n{code}"
        # Critical: no doubled prefix
        assert f"{expected_abs}/skills/ppt-generator" not in code


def test_run_skill_script_passes_argv_and_handles_packages(monkeypatch, tmp_path):
    """argv must reach the script, and package-aware code path must fire
    for scripts inside a Python package (relative imports need run_module)."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    _seed_local_skill(tmp_path)

    calls = []
    ci = MagicMock()
    ci.start_code_interpreter_session.return_value = {"sessionId": "sess-pkg"}

    def fake_invoke(**kwargs):
        calls.append(kwargs)
        name = kwargs["name"]
        if name == "writeFiles":
            return {"stream": [{"result": {"content": []}}]}
        if "__CI_ANCHOR__" in kwargs.get("arguments", {}).get("code", ""):
            return {"stream": [{"result": {"structuredContent": {
                "stdout": "__CI_ANCHOR__/sandbox__CI_ANCHOR_END__",
                "stderr": "", "exitCode": 0,
            }, "content": []}}]}
        return {"stream": [{"result": {"structuredContent": {"stdout": "ok\n", "stderr": "", "exitCode": 0}, "content": []}}]}

    ci.invoke_code_interpreter.side_effect = fake_invoke

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin()
        ns["_CACHE_ROOT"] = tmp_path
        _install_manifest(ns, [{"id": "s-1", "name": "ppt-generator"}])
        ns["_CACHED_SKILLS"].add("s-1")
        ns["run_skill_script"](
            "ppt-generator",
            "scripts/render.py",
            args="--output out.pptx --theme dark_warm",
        )

    exec_calls = [c for c in calls if c["name"] == "executeCode" and "runpy" in c.get("arguments", {}).get("code", "")]
    assert exec_calls, "no runpy exec call recorded"
    code = exec_calls[-1]["arguments"]["code"]
    # argv must be wired into the script
    assert "--output" in code
    assert "out.pptx" in code
    assert "dark_warm" in code
    # Package detection walking happens
    assert "run_module" in code
    assert "run_path" in code  # fallback present
    # pytest-style identifier check
    assert "isidentifier" in code


def test_run_skill_script_rejects_blank_args():
    ns = _exec_builtin()
    result = ns["run_skill_script"]("", "scripts/x.py")
    assert isinstance(result, dict) and result["status"] == "error"
    result = ns["run_skill_script"]("name", "")
    assert isinstance(result, dict) and result["status"] == "error"

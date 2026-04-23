"""Test check_capabilities() builtin — the preflight probe."""
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
    """Exec BUILTIN_TOOLS_CODE with the AGENT_ID pre-seeded.

    The real module reads _AGENT_ID from env at exec time, so we have to
    inject it either via env var or by mutating the namespace after exec.
    We mutate after-exec so the envirement of other tests isn't polluted.
    """
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE
    ns: dict = {"__name__": "builtin_tools_test"}
    exec(BUILTIN_TOOLS_CODE, ns)
    ns["_AGENT_ID"] = agent_id
    return ns


def _mock_s3_manifest(agent_id: str, skills: list):
    """Build an S3 client mock that returns the given manifest."""
    s3 = MagicMock()

    def _get_object(Bucket, Key):
        if Key == f"agents/{agent_id}/metadata.json":
            return {"Body": MagicMock(read=lambda: json.dumps({"skills": skills}).encode())}
        raise KeyError(Key)

    s3.get_object.side_effect = _get_object
    paginator = MagicMock()
    paginator.paginate.return_value = iter([{"Contents": []}])
    s3.get_paginator.return_value = paginator
    return s3


def test_check_capabilities_reports_missing_ci_id(monkeypatch):
    monkeypatch.delenv("AGENT_STUDIO_CODE_INTERPRETER_ID", raising=False)
    s3 = _mock_s3_manifest("test-agent", [])
    ns = _exec_builtin("test-agent")
    ns["_s3"] = s3

    out = ns["check_capabilities"]()
    data = json.loads(out)
    assert data["code_interpreter"]["ready"] is False
    assert "not configured" in data["code_interpreter"]["error"].lower()
    assert data["skills"] == []
    assert data["agent_id"] == "test-agent"


def test_check_capabilities_reports_no_session(monkeypatch):
    """CI id is configured but run_command has not been called yet."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")
    s3 = _mock_s3_manifest("test-agent", [])
    ns = _exec_builtin("test-agent")
    ns["_s3"] = s3

    out = ns["check_capabilities"]()
    data = json.loads(out)
    assert data["code_interpreter"]["ready"] is False
    assert "no active session" in data["code_interpreter"]["error"].lower()


def test_check_capabilities_lists_skills_from_manifest(monkeypatch):
    """Skills come from agents/{id}/metadata.json, not the global index."""
    monkeypatch.delenv("AGENT_STUDIO_CODE_INTERPRETER_ID", raising=False)

    manifest = [
        {"id": "214bb180", "name": "s3-data-pipeline", "description": "desc1"},
        {"id": "7ab37a90", "name": "ppt-generator", "description": "desc2"},
    ]

    s3 = MagicMock()

    def _get_object(Bucket, Key):
        if Key == "agents/test-agent/metadata.json":
            return {"Body": MagicMock(read=lambda: json.dumps({"skills": manifest}).encode())}
        raise KeyError(Key)

    s3.get_object.side_effect = _get_object

    def _paginate(Bucket, Prefix):
        # Simulate per-skill object listings
        if Prefix == "agents/test-agent/skills/214bb180/":
            return iter([{"Contents": [
                {"Key": "agents/test-agent/skills/214bb180/SKILL.md"},
                {"Key": "agents/test-agent/skills/214bb180/scripts/clean.py"},
            ]}])
        if Prefix == "agents/test-agent/skills/7ab37a90/":
            return iter([{"Contents": [
                {"Key": "agents/test-agent/skills/7ab37a90/SKILL.md"},
                {"Key": "agents/test-agent/skills/7ab37a90/ppt-master-assets/a.md"},
                {"Key": "agents/test-agent/skills/7ab37a90/ppt-master-assets/b.md"},
            ]}])
        return iter([{"Contents": []}])

    paginator = MagicMock()
    paginator.paginate.side_effect = _paginate
    s3.get_paginator.return_value = paginator

    ns = _exec_builtin("test-agent")
    ns["_s3"] = s3

    out = ns["check_capabilities"]()
    data = json.loads(out)
    by_name = {s["name"]: s for s in data["skills"]}
    assert set(by_name.keys()) == {"s3-data-pipeline", "ppt-generator"}
    assert by_name["s3-data-pipeline"]["file_count"] == 2
    assert by_name["s3-data-pipeline"]["loadable"] is True
    assert by_name["ppt-generator"]["file_count"] == 3
    assert by_name["ppt-generator"]["materialized"] is False  # nothing cached yet


def test_check_capabilities_classifies_skill_types(monkeypatch):
    """The 'type' field must distinguish prompt / scripted / assets-only."""
    monkeypatch.delenv("AGENT_STUDIO_CODE_INTERPRETER_ID", raising=False)

    manifest = [
        {"id": "p-01", "name": "prompt-only", "description": "just instructions"},
        {"id": "s-01", "name": "scripted", "description": "runs code"},
        {"id": "a-01", "name": "assets-only", "description": "data + templates"},
    ]

    s3 = MagicMock()

    def _get_object(Bucket, Key):
        if Key == "agents/test-agent/metadata.json":
            return {"Body": MagicMock(read=lambda: json.dumps({"skills": manifest}).encode())}
        raise KeyError(Key)

    s3.get_object.side_effect = _get_object

    layouts = {
        "agents/test-agent/skills/p-01/": [
            {"Key": "agents/test-agent/skills/p-01/SKILL.md"},
        ],
        "agents/test-agent/skills/s-01/": [
            {"Key": "agents/test-agent/skills/s-01/SKILL.md"},
            {"Key": "agents/test-agent/skills/s-01/scripts/clean.py"},
        ],
        "agents/test-agent/skills/a-01/": [
            {"Key": "agents/test-agent/skills/a-01/SKILL.md"},
            {"Key": "agents/test-agent/skills/a-01/templates/report.md"},
            {"Key": "agents/test-agent/skills/a-01/assets/logo.svg"},
        ],
    }

    def _paginate(Bucket, Prefix):
        return iter([{"Contents": layouts.get(Prefix, [])}])

    paginator = MagicMock()
    paginator.paginate.side_effect = _paginate
    s3.get_paginator.return_value = paginator

    ns = _exec_builtin("test-agent")
    ns["_s3"] = s3

    out = ns["check_capabilities"]()
    data = json.loads(out)
    by_name = {s["name"]: s for s in data["skills"]}
    assert by_name["prompt-only"]["type"] == "prompt"
    assert by_name["scripted"]["type"] == "scripted"
    assert by_name["assets-only"]["type"] == "assets-only"


def test_check_capabilities_handles_missing_agent_id(monkeypatch):
    """With no AGENT_ID, skill listings should surface the config gap."""
    monkeypatch.delenv("AGENT_STUDIO_CODE_INTERPRETER_ID", raising=False)
    ns = _exec_builtin(agent_id="")
    # Even with no agent_id, the manifest lookup returns [] gracefully
    ns["_SKILLS_MANIFEST"] = [{"id": "abc", "name": "orphan", "description": "d"}]

    out = ns["check_capabilities"]()
    data = json.loads(out)
    assert data["agent_id"] is None
    orphan = data["skills"][0]
    assert orphan["error"] and "agent_id unknown" in orphan["error"]


def test_check_capabilities_probes_ci_when_session_exists(monkeypatch):
    """With a live CI session, the probe should roundtrip sys.version."""
    monkeypatch.setenv("AGENT_STUDIO_CODE_INTERPRETER_ID", "ci-test")

    probe_out = json.dumps({"python": "3.10.12", "cwd": "/opt/amazon/genesis1p-tools/var"})
    ci = MagicMock()
    ci.invoke_code_interpreter.return_value = {
        "stream": [{"result": {"structuredContent": {"stdout": probe_out, "stderr": "", "exitCode": 0}, "content": []}}]
    }

    with patch("boto3.client", return_value=ci):
        ns = _exec_builtin("test-agent")
        ns["_s3"] = _mock_s3_manifest("test-agent", [])
        ns["run_command"]._session_id = "sess-active"
        out = ns["check_capabilities"]()

    data = json.loads(out)
    assert data["code_interpreter"]["ready"] is True
    assert data["code_interpreter"]["python_version"] == "3.10.12"
    assert data["code_interpreter"]["session_id"] == "sess-active"

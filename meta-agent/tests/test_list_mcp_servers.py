"""Tests for list_mcp_servers — registry-driven MCP target listing."""
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ── Module stubs ───────────────────────────────────────────────────────────
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
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-1", raising=False)


# ── Helper unit tests ─────────────────────────────────────────────────────

def test_iam_policies_from_registry_extracts_policies():
    from tools.list_mcp_servers import _iam_policies_from_registry
    registry = {
        "remote_targets": [{"name": "tool_a", "iam_policy": {"X": 1}}],
        "runtime_targets": [{"name": "tool_b"}],  # no policy
    }
    out = _iam_policies_from_registry(registry)
    assert out["tool_a"] == {"X": 1}
    assert out["tool_b"] is None


def test_runtime_name_candidates():
    from tools.list_mcp_servers import _runtime_name_candidates
    out = _runtime_name_candidates("my-tool")
    assert "my_tool" in out
    assert "mcp_my_tool" in out


def test_enrich_with_permissions_no_policy_granted():
    """Targets without iam_policy declarations are always granted."""
    from tools.list_mcp_servers import _enrich_with_permissions
    out = _enrich_with_permissions("platform_tool", {"platform_tool": None}, "arn:role")
    assert out == {"granted": True}


def test_enrich_with_permissions_no_role_means_denied():
    """Workspace without custom role can't grant declared iam_policy."""
    from tools.list_mcp_servers import _enrich_with_permissions
    policy = {"Statement": [{"Effect": "Allow", "Action": "s3:GetObject"}]}
    out = _enrich_with_permissions("tool", {"tool": policy}, None)
    assert out["granted"] is False
    assert out["reason"] == "no_workspace_role"
    assert out["iam_policy"] == policy


def test_check_iam_permissions_no_required_actions():
    """Empty/Deny-only policy returns granted=True."""
    from tools.list_mcp_servers import _check_iam_permissions
    out = _check_iam_permissions("arn:role", {"Statement": []})
    assert out == {"granted": True, "missing_actions": []}


def test_check_iam_permissions_simulation_failure_returns_denied():
    """If iam:SimulatePrincipalPolicy fails, returns granted=False with error."""
    from tools.list_mcp_servers import _check_iam_permissions

    fake_iam = MagicMock()
    fake_iam.simulate_principal_policy.side_effect = Exception("AccessDenied")

    with patch("boto3.client", return_value=fake_iam):
        policy = {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"]}]}
        out = _check_iam_permissions("arn:role", policy)

    assert out["granted"] is False
    assert "simulation_error" in out
    assert "AccessDenied" in out["simulation_error"]


def test_check_iam_permissions_all_allowed():
    """When simulator allows everything, granted=True."""
    from tools.list_mcp_servers import _check_iam_permissions

    fake_iam = MagicMock()
    fake_iam.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "s3:GetObject", "EvalDecision": "allowed"},
        ],
    }

    with patch("boto3.client", return_value=fake_iam):
        policy = {"Statement": [{"Effect": "Allow", "Action": "s3:GetObject"}]}
        out = _check_iam_permissions("arn:role", policy)

    assert out["granted"] is True
    assert out["missing_actions"] == []


def test_check_iam_permissions_some_denied():
    from tools.list_mcp_servers import _check_iam_permissions

    fake_iam = MagicMock()
    fake_iam.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "s3:GetObject", "EvalDecision": "allowed"},
            {"EvalActionName": "s3:DeleteObject", "EvalDecision": "implicitDeny"},
        ],
    }

    with patch("boto3.client", return_value=fake_iam):
        policy = {"Statement": [{
            "Effect": "Allow", "Action": ["s3:GetObject", "s3:DeleteObject"],
        }]}
        out = _check_iam_permissions("arn:role", policy)

    assert out["granted"] is False
    assert "s3:DeleteObject" in out["missing_actions"]


def test_load_registry_returns_default_on_failure(monkeypatch):
    """On any exception, _load_registry returns empty target lists."""
    from tools import list_mcp_servers as mod

    fake_s3 = MagicMock()
    fake_s3.get_object.side_effect = Exception("NoSuchKey")

    with patch("boto3.client", return_value=fake_s3):
        out = mod._load_registry()

    assert out == {"remote_targets": [], "runtime_targets": []}


def test_load_registry_parses_yaml(monkeypatch):
    from tools import list_mcp_servers as mod

    yaml_content = yaml.dump({
        "remote_targets": [{"name": "remote1", "enabled": True}],
        "runtime_targets": [{"name": "rt1", "enabled": True}],
    }).encode("utf-8")

    fake_s3 = MagicMock()
    fake_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: yaml_content),
    }

    with patch("boto3.client", return_value=fake_s3):
        out = mod._load_registry()

    assert out["remote_targets"][0]["name"] == "remote1"
    assert out["runtime_targets"][0]["name"] == "rt1"


def test_get_workspace_role_arn_returns_none_when_unset():
    from tools.list_mcp_servers import _get_workspace_role_arn
    assert _get_workspace_role_arn("") is None


def test_get_workspace_role_arn_reads_from_ddb():
    from tools.list_mcp_servers import _get_workspace_role_arn

    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": {"roleArn": "arn:aws:iam::1:role/x"}}
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.resource", return_value=fake_resource):
        out = _get_workspace_role_arn("ws-1")

    assert out == "arn:aws:iam::1:role/x"


def test_get_workspace_role_arn_returns_none_on_ddb_error():
    from tools.list_mcp_servers import _get_workspace_role_arn

    fake_resource = MagicMock()
    fake_resource.Table.side_effect = Exception("ddb")
    with patch("boto3.resource", return_value=fake_resource):
        out = _get_workspace_role_arn("ws-1")

    assert out is None


def test_list_deployed_runtimes_paginates():
    """Multi-page list_agent_runtimes is fully drained."""
    from tools.list_mcp_servers import _list_deployed_runtimes

    fake_control = MagicMock()
    # First call: 2 items + nextToken; second call: 1 item, no nextToken
    fake_control.list_agent_runtimes.side_effect = [
        {"agentRuntimes": [
            {"agentRuntimeName": "rt1"},
            {"agentRuntimeName": "rt2"},
        ], "nextToken": "t1"},
        {"agentRuntimes": [{"agentRuntimeName": "rt3"}]},
    ]

    with patch("boto3.client", return_value=fake_control):
        out = _list_deployed_runtimes()

    assert set(out.keys()) == {"rt1", "rt2", "rt3"}
    assert fake_control.list_agent_runtimes.call_count == 2


def test_list_deployed_runtimes_handles_failure():
    from tools.list_mcp_servers import _list_deployed_runtimes

    fake_control = MagicMock()
    fake_control.list_agent_runtimes.side_effect = Exception("explode")
    with patch("boto3.client", return_value=fake_control):
        out = _list_deployed_runtimes()
    assert out == {}


def test_build_targets_skips_disabled_and_deprecated():
    from tools.list_mcp_servers import _build_targets_from_registry

    registry = {
        "remote_targets": [
            {"name": "active_remote", "enabled": True, "description": "ok"},
            {"name": "disabled_remote", "enabled": False},
            {"name": "deprecated_remote", "enabled": True, "deprecated": True},
        ],
        "runtime_targets": [
            {"name": "active_rt", "enabled": True, "category": "data"},
        ],
    }
    deployed = {"active_rt": {"status": "READY"}}

    out = _build_targets_from_registry(registry, deployed, {}, None)
    names = [t["name"] for t in out]
    assert "active_remote" in names
    assert "disabled_remote" not in names
    assert "deprecated_remote" not in names
    assert "active_rt" in names

    rt_entry = next(t for t in out if t["name"] == "active_rt")
    assert rt_entry["status"] == "READY"
    assert rt_entry["type"] == "runtime"


def test_build_targets_runtime_status_unavailable_when_not_deployed():
    from tools.list_mcp_servers import _build_targets_from_registry
    registry = {
        "remote_targets": [],
        "runtime_targets": [{"name": "ghost", "enabled": True}],
    }
    out = _build_targets_from_registry(registry, {}, {}, None)
    assert out[0]["status"] == "unavailable"


# ── @tool integration tests ───────────────────────────────────────────────

def test_list_mcp_servers_returns_categorized_payload(monkeypatch):
    from tools import list_mcp_servers as mod

    monkeypatch.setattr(mod, "_load_registry", lambda: {
        "remote_targets": [{"name": "remote1", "enabled": True, "category": "data"}],
        "runtime_targets": [{"name": "rt1", "enabled": True, "category": "ops"}],
    })
    monkeypatch.setattr(mod, "_list_deployed_runtimes",
                         lambda: {"rt1": {"status": "READY"}})
    monkeypatch.setattr(mod, "_get_workspace_role_arn", lambda ws: None)

    out = json.loads(mod.list_mcp_servers())

    assert out["total"] == 2
    assert out["ready"] == 2  # remote=READY, rt1=READY
    assert "data" in out["categories"]
    assert "ops" in out["categories"]


def test_list_mcp_servers_handles_failure(monkeypatch):
    """Top-level exception path returns {error: ...}."""
    from tools import list_mcp_servers as mod

    def raise_(): raise RuntimeError("boom")
    monkeypatch.setattr(mod, "_load_registry", raise_)

    out = json.loads(mod.list_mcp_servers())
    assert "error" in out
    assert "boom" in out["error"]


def test_list_mcp_servers_denied_count(monkeypatch):
    """Targets with iam_policy but no workspace role are counted denied."""
    from tools import list_mcp_servers as mod

    monkeypatch.setattr(mod, "_load_registry", lambda: {
        "remote_targets": [{
            "name": "remote_with_policy",
            "enabled": True,
            "category": "data",
            "iam_policy": {"Statement": [{"Effect": "Allow", "Action": "x:Y"}]},
        }],
        "runtime_targets": [],
    })
    monkeypatch.setattr(mod, "_list_deployed_runtimes", lambda: {})
    monkeypatch.setattr(mod, "_get_workspace_role_arn", lambda ws: None)

    out = json.loads(mod.list_mcp_servers())

    assert out["denied"] == 1
    target = out["categories"]["data"][0]
    assert target["granted"] is False
    assert target["reason"] == "no_workspace_role"

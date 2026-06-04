"""Tests for check_workspace_permissions tool + _simulate_actions cache."""

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

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope

    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-test", raising=False)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty permission cache."""
    from tools import check_workspace_permissions as mod

    mod._permission_cache.clear()
    yield
    mod._permission_cache.clear()


def test_check_returns_no_role_when_workspace_has_none(monkeypatch):
    from tools import check_workspace_permissions as mod

    monkeypatch.setattr(mod, "_get_workspace_role_arn", lambda ws: None)

    out = json.loads(mod.check_workspace_permissions("ws-1", "iam:GetRole"))
    assert out == {"has_role": False, "role_arn": None}


def test_check_returns_empty_results_for_blank_actions(monkeypatch):
    from tools import check_workspace_permissions as mod

    monkeypatch.setattr(
        mod,
        "_get_workspace_role_arn",
        lambda ws: "arn:aws:iam::1:role/r",
    )
    out = json.loads(mod.check_workspace_permissions("ws-1", ""))
    assert out["has_role"] is True
    assert out["role_arn"] == "arn:aws:iam::1:role/r"
    assert out["results"] == []


def test_check_returns_simulation_results(monkeypatch):
    from tools import check_workspace_permissions as mod

    monkeypatch.setattr(
        mod,
        "_get_workspace_role_arn",
        lambda ws: "arn:aws:iam::1:role/r",
    )

    fake_iam = MagicMock()
    fake_iam.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "iam:GetRole", "EvalDecision": "allowed"},
            {"EvalActionName": "iam:DeleteRole", "EvalDecision": "implicitDeny"},
        ]
    }
    monkeypatch.setattr(
        mod.boto3,
        "client",
        lambda svc, **_: fake_iam,
    )

    out = json.loads(
        mod.check_workspace_permissions(
            "ws-1",
            "iam:GetRole, iam:DeleteRole",
        )
    )
    assert out["has_role"] is True
    assert out["results"] == [
        {"action": "iam:GetRole", "allowed": True},
        {"action": "iam:DeleteRole", "allowed": False},
    ]


def test_simulate_actions_paginates_at_25(monkeypatch):
    """SimulatePrincipalPolicy has a 25-action limit; we batch."""
    from tools import check_workspace_permissions as mod

    fake_iam = MagicMock()
    calls = []

    def fake_sim(**kwargs):
        calls.append(kwargs)
        # Each batch: return all-allowed
        return {
            "EvaluationResults": [
                {"EvalActionName": a, "EvalDecision": "allowed"} for a in kwargs["ActionNames"]
            ]
        }

    fake_iam.simulate_principal_policy.side_effect = fake_sim
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_iam)

    actions = [f"svc:Action{i}" for i in range(60)]
    results = mod._simulate_actions("arn:aws:iam::1:role/r", actions)

    # 60 actions → 3 batches (25, 25, 10)
    assert len(calls) == 3
    assert len(calls[0]["ActionNames"]) == 25
    assert len(calls[1]["ActionNames"]) == 25
    assert len(calls[2]["ActionNames"]) == 10
    # Result preserves input ordering and length
    assert len(results) == 60
    assert results[0] == {"action": "svc:Action0", "allowed": True}


def test_simulate_actions_uses_cache(monkeypatch):
    """Repeated lookups in the TTL window skip IAM."""
    from tools import check_workspace_permissions as mod

    fake_iam = MagicMock()
    fake_iam.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "s3:GetObject", "EvalDecision": "allowed"},
        ]
    }
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_iam)

    role = "arn:aws:iam::1:role/r"
    r1 = mod._simulate_actions(role, ["s3:GetObject"])
    assert r1[0]["allowed"] is True
    assert fake_iam.simulate_principal_policy.call_count == 1

    # Second call should hit the cache, not IAM
    r2 = mod._simulate_actions(role, ["s3:GetObject"])
    assert r2[0]["allowed"] is True
    assert fake_iam.simulate_principal_policy.call_count == 1


def test_check_handles_iam_failure(monkeypatch):
    """Boto exceptions are wrapped into the JSON error shape — never raise."""
    from tools import check_workspace_permissions as mod

    monkeypatch.setattr(
        mod,
        "_get_workspace_role_arn",
        lambda ws: "arn:aws:iam::1:role/r",
    )

    fake_iam = MagicMock()
    fake_iam.simulate_principal_policy.side_effect = Exception("AccessDenied")
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_iam)

    out = json.loads(mod.check_workspace_permissions("ws-1", "iam:GetRole"))
    assert "error" in out
    assert "AccessDenied" in out["error"]


def test_check_filters_blank_actions(monkeypatch):
    """Comma-separated input with blank entries shouldn't reach IAM."""
    from tools import check_workspace_permissions as mod

    monkeypatch.setattr(
        mod,
        "_get_workspace_role_arn",
        lambda ws: "arn:aws:iam::1:role/r",
    )

    fake_iam = MagicMock()
    fake_iam.simulate_principal_policy.return_value = {
        "EvaluationResults": [
            {"EvalActionName": "iam:GetRole", "EvalDecision": "allowed"},
        ]
    }
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_iam)

    # "iam:GetRole,,  ,," — only one real action
    out = json.loads(mod.check_workspace_permissions("ws-1", "iam:GetRole,,  ,,"))
    assert out["has_role"] is True
    assert len(out["results"]) == 1
    # Only one action sent to IAM
    args = fake_iam.simulate_principal_policy.call_args.kwargs
    assert args["ActionNames"] == ["iam:GetRole"]

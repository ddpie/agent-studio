"""Tests for create_schedule tool — EventBridge Scheduler integration."""
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
    "ACCOUNT_ID": "123456789012",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
    "SCHEDULER_TARGET_ROLE_ARN": "arn:aws:iam::000:role/sched",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-test", raising=False)


def test_create_schedule_happy_path(monkeypatch):
    from tools import create_schedule as mod

    # Bypass the workspace + role gate, return a record carrying ws_id
    monkeypatch.setattr(
        mod, "ensure_agent_in_workspace",
        lambda agent_id, min_role: ({"workspace_id": "ws-test"}, None),
    )

    fake_scheduler = MagicMock()
    fake_scheduler.create_schedule.return_value = {
        "ScheduleArn": "arn:aws:scheduler:us-east-1:123:schedule/default/my-sched",
    }
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_scheduler)

    out = json.loads(mod.create_schedule(
        schedule_name="my-sched",
        agent_id="rt-abc",
        cron_expression="cron(0 9 * * ? *)",
        prompt="Do morning report",
    ))

    assert out["status"] == "created"
    assert out["schedule_name"] == "my-sched"
    assert out["target_agent"] == "rt-abc"
    assert out["cron"] == "cron(0 9 * * ? *)"
    assert out["schedule_arn"].endswith("/my-sched")

    # Verify scheduler kwargs shape
    kwargs = fake_scheduler.create_schedule.call_args.kwargs
    assert kwargs["Name"] == "my-sched"
    assert kwargs["ScheduleExpression"] == "cron(0 9 * * ? *)"
    assert kwargs["FlexibleTimeWindow"] == {"Mode": "OFF"}

    target = kwargs["Target"]
    # Lambda middleman ARN, not the AgentCore runtime
    assert target["Arn"].endswith("function:agent-studio-schedule-runner")
    assert target["RoleArn"] == "arn:aws:iam::000:role/sched"

    # Input is JSON containing AgentRuntimeArn + Payload
    target_input = json.loads(target["Input"])
    assert target_input["AgentRuntimeArn"].endswith("runtime/rt-abc")
    assert target_input["RuntimeSessionId"].startswith("sched-my-sched-")
    inner = json.loads(target_input["Payload"])
    assert inner["prompt"] == "Do morning report"
    assert inner["__schedule_name"] == "my-sched"
    assert inner["workspace_id"] == "ws-test"


def test_create_schedule_rejects_when_caller_below_editor(monkeypatch):
    """ensure_agent_in_workspace returns an error dict — propagate it as JSON."""
    from tools import create_schedule as mod

    monkeypatch.setattr(
        mod, "ensure_agent_in_workspace",
        lambda agent_id, min_role: (None, {"error": "Permission denied: editor role required"}),
    )

    # If the gate is honored, scheduler.create_schedule must NEVER fire
    fake_scheduler = MagicMock()
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_scheduler)

    out = json.loads(mod.create_schedule(
        "name", "rt-abc", "cron(0 * * * ? *)", "p",
    ))
    assert "error" in out
    assert "Permission denied" in out["error"]
    fake_scheduler.create_schedule.assert_not_called()


def test_create_schedule_includes_substitution_token(monkeypatch):
    """Session id must include <aws.scheduler.scheduled-time> for unique sessions."""
    from tools import create_schedule as mod

    monkeypatch.setattr(
        mod, "ensure_agent_in_workspace",
        lambda agent_id, min_role: ({"workspace_id": "ws-1"}, None),
    )

    fake_scheduler = MagicMock()
    fake_scheduler.create_schedule.return_value = {"ScheduleArn": "arn:x"}
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_scheduler)

    mod.create_schedule("daily", "rt-1", "cron(0 9 * * ? *)", "hi")

    target_input = json.loads(
        fake_scheduler.create_schedule.call_args.kwargs["Target"]["Input"]
    )
    assert "<aws.scheduler.scheduled-time>" in target_input["RuntimeSessionId"]


def test_create_schedule_handles_missing_workspace_in_record(monkeypatch):
    """Record without workspace_id should not crash — falls through to empty string."""
    from tools import create_schedule as mod

    monkeypatch.setattr(
        mod, "ensure_agent_in_workspace",
        lambda agent_id, min_role: ({}, None),  # record without workspace_id
    )

    fake_scheduler = MagicMock()
    fake_scheduler.create_schedule.return_value = {"ScheduleArn": "arn:y"}
    monkeypatch.setattr(mod.boto3, "client", lambda svc, **_: fake_scheduler)

    out = json.loads(mod.create_schedule("n", "rt-1", "cron(0 9 * * ? *)", "p"))
    assert out["status"] == "created"
    payload = json.loads(
        fake_scheduler.create_schedule.call_args.kwargs["Target"]["Input"]
    )
    inner = json.loads(payload["Payload"])
    assert inner["workspace_id"] == ""

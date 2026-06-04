"""Tests for crud/evaluations.py — create + read."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "arn:aws:iam::000000000000:role/test-evaluator")
    monkeypatch.setenv("SPANS_LOG_GROUP", "aws/spans")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)


@pytest.fixture
def ws_event():
    return {
        "httpMethod": "POST",
        "path": "/api/workspaces",
        "resource": "/api/workspaces",
        "pathParameters": None,
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": json.dumps({"name": "Test WS"}),
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def _make_fake_ws_table():
    """Build a fake workspaces DynamoDB table that won't hang the
    name-duplicate scan loop in crud.workspaces._workspace_name_exists."""
    fake_table = MagicMock()
    fake_table.name = "test-workspaces"
    fake_table.meta.client.transact_write_items.return_value = {}
    # _workspace_name_exists scans with `while True` until LastEvaluatedKey is
    # missing. A bare MagicMock().scan() returns a MagicMock whose .get() is
    # also MagicMock (truthy), so the loop never exits. Force a real dict.
    fake_table.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
    return fake_table


# NOTE: tests for "POST /workspaces auto-fires create_eval_config_for_workspace"
# were removed. crud/workspaces.py:create_workspace no longer wires this in
# automatically — eval config creation is now explicit via
# POST /api/workspaces/<wsId>/evaluations/enable. The old behavior was a
# silent fan-out that ran on every workspace create.


def test_create_eval_config_idempotent():
    """ConflictException on create is handled gracefully; backfill runs."""
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.evaluations as _ev
    importlib.reload(_ev)
    from botocore.exceptions import ClientError
    from crud.evaluations import create_eval_config_for_agent

    with patch("crud.evaluations._get_control") as mock_c, \
         patch("crud.evaluations._ensure_runtime_log_group") as mock_backfill:
        client = MagicMock()
        client.create_online_evaluation_config.side_effect = ClientError(
            {"Error": {"Code": "ConflictException", "Message": "already exists"}},
            "CreateOnlineEvaluationConfig",
        )
        mock_c.return_value = client
        name = create_eval_config_for_agent("abc-123", "myAgent-XYZ")
    assert name.startswith("agentstudio_")
    assert "myAgent" in name
    mock_backfill.assert_called_once()


def test_create_eval_config_no_role_returns_empty(monkeypatch):
    """If EVALUATOR_ROLE_ARN is empty, skip creation and return ''."""
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    # Also reload evaluations to pick up the empty value
    import crud.evaluations as _ev
    importlib.reload(_ev)
    name = _ev.create_eval_config_for_workspace(workspace_id="abc-123")
    assert name == ""


# ---------------------------------------------------------------------------
# Read endpoint: GET /agents/{agentId}/evaluations
# ---------------------------------------------------------------------------


def _agent_eval_event(workspace_id: str, agent_id: str = "agt-test"):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{workspace_id}/agents/{agent_id}/evaluations",
        "resource": "/api/workspaces/{wsId}/agents/{agentId}/evaluations",
        "pathParameters": {"wsId": workspace_id, "agentId": agent_id},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_get_agent_evaluations_returns_scores(mock_jwt, user_id, workspace_id):
    """GET /agents/{id}/evaluations returns parsed evaluator rows."""
    from crud.handler import app
    fake_logs = MagicMock()
    fake_logs.describe_log_groups.return_value = {
        "logGroups": [
            {"logGroupName": "/aws/bedrock-agentcore/evaluations/results/agentstudio_ws_xyz-ABC"},
        ],
    }
    fake_logs.start_query.return_value = {"queryId": "q-1"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [
                {"field": "@timestamp", "value": "2026-04-19 00:01:00.000"},
                {"field": "evaluator", "value": "Builtin.Correctness"},
                {"field": "score", "value": "0.85"},
                {"field": "sessionId", "value": "sess-abc"},
            ],
            [
                {"field": "@timestamp", "value": "2026-04-19 00:01:00.100"},
                {"field": "evaluator", "value": "Builtin.Helpfulness"},
                {"field": "score", "value": "0.9"},
                {"field": "sessionId", "value": "sess-abc"},
            ],
        ],
    }

    fake_cfg = {
        "onlineEvaluationConfigId": "cfg-test-ABC",
        "onlineEvaluationConfigName": "agentstudio_test",
    }

    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value=fake_cfg), \
         patch("crud.evaluations._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert "evaluations" in data
    assert len(data["evaluations"]) == 2
    assert data["evaluations"][0]["score"] == 0.85
    assert data["evaluations"][0]["evaluator"] == "Builtin.Correctness"


def test_get_agent_evaluations_empty_when_no_results(mock_jwt, user_id, workspace_id):
    """Empty Logs Insights results → {evaluations: []}, not 500."""
    from crud.handler import app
    fake_logs = MagicMock()
    fake_logs.describe_log_groups.return_value = {
        "logGroups": [
            {"logGroupName": "/aws/bedrock-agentcore/evaluations/results/agentstudio_ws_xyz-ABC"},
        ],
    }
    fake_logs.start_query.return_value = {"queryId": "q-2"}
    fake_logs.get_query_results.return_value = {"status": "Complete", "results": []}

    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["evaluations"] == []


def test_get_agent_evaluations_no_log_groups_returns_empty(mock_jwt, user_id, workspace_id):
    """When no eval output log groups exist yet, short-circuit to []."""
    from crud.handler import app
    fake_logs = MagicMock()
    fake_logs.describe_log_groups.return_value = {"logGroups": []}

    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["evaluations"] == []
    fake_logs.start_query.assert_not_called()


def test_get_agent_evaluations_forbidden_when_agent_not_in_workspace(mock_jwt, user_id, workspace_id):
    """Agent's workspace_id != path wsId → 403."""
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other-ws"}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403

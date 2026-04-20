"""Tests for crud/traces.py — OTEL span tree assembly."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("SPANS_LOG_GROUP", "aws/spans")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)


def _base_event(workspace_id: str, path_suffix: str, path_params: dict, resource: str):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{workspace_id}{path_suffix}",
        "resource": resource,
        "pathParameters": path_params,
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_list_traces_returns_session_summaries(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q-1"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [
                {"field": "sessionId", "value": "sess-a"},
                {"field": "traceId", "value": "trace-a"},
                {"field": "firstEvent", "value": "2026-04-19 00:00:00.000"},
                {"field": "spanCount", "value": "12"},
            ],
            [
                {"field": "sessionId", "value": "sess-b"},
                {"field": "traceId", "value": "trace-b"},
                {"field": "firstEvent", "value": "2026-04-19 00:02:00.000"},
                {"field": "spanCount", "value": "5"},
            ],
        ],
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with patch("crud.traces.auth_check") as auth, \
         patch("crud.traces._get_agent_item") as ga, \
         patch("crud.traces._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert len(data["sessions"]) == 2
    assert data["sessions"][0]["sessionId"] == "sess-a"
    assert data["sessions"][0]["traceId"] == "trace-a"
    assert data["sessions"][0]["spanCount"] == 12
    # firstEvent must carry a tz suffix so JS parses as UTC, not local.
    assert data["sessions"][0]["firstEvent"] == "2026-04-19T00:00:00.000Z"


def test_get_session_trace_assembles_tree(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q-2"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [
                {"field": "spanId", "value": "sp-root"},
                {"field": "name", "value": "InvokeAgent"},
                {"field": "startTimeUnixNano", "value": "1776100000000000000"},
                {"field": "endTimeUnixNano", "value": "1776100001500000000"},
                {"field": "status", "value": "OK"},
            ],
            [
                {"field": "spanId", "value": "sp-child1"},
                {"field": "parentSpanId", "value": "sp-root"},
                {"field": "name", "value": "tool:run_command"},
                {"field": "startTimeUnixNano", "value": "1776100000100000000"},
                {"field": "endTimeUnixNano", "value": "1776100000900000000"},
                {"field": "status", "value": "OK"},
            ],
        ],
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-a",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-a"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth, \
         patch("crud.traces._get_agent_item") as ga, \
         patch("crud.traces._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    # Single-tree response (reverted from multi-turn).
    assert data["root"]["spanId"] == "sp-root"
    assert len(data["root"]["children"]) == 1
    assert data["root"]["children"][0]["name"] == "tool:run_command"
    assert data["root"]["durationMs"] == 1500
    assert data["root"]["children"][0]["durationMs"] == 800
    assert data["totalSpans"] == 2


def test_list_traces_forbidden_when_agent_in_other_workspace(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with patch("crud.traces.auth_check") as auth, \
         patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other-ws"}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_session_trace_rejects_bad_session_id(mock_jwt, user_id, workspace_id):
    """sessionId outside the safe charset must 400 — guards against query
    injection into the interpolated Logs Insights string."""
    from crud.handler import app
    bad_id = "bad$id"
    event = _base_event(
        workspace_id,
        f"/agents/agt-test/traces/{bad_id}",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": bad_id},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth, \
         patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400

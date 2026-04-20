"""Tests for lambda/crud/runs.py."""
import json
import time
from unittest.mock import MagicMock, patch

import pytest


def _base_event(ws_id, path, path_params, resource):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{ws_id}{path}",
        "pathParameters": path_params,
        "resource": f"/api/workspaces/{{wsId}}{resource}",
        "headers": {"Authorization": "Bearer tok", "x-origin-verify": ""},
        "queryStringParameters": None,
        "body": None,
        "requestContext": {},
    }


def test_list_runs_returns_items(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    now_iso = "2026-04-20T08:00:00Z"
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [
            {
                "agentId": "agt-test",
                "runId": "01JWXYZ",
                "workspaceId": workspace_id,
                "trigger": "schedule",
                "scheduleId": "agent-studio-agt-test-daily",
                "sessionId": "sched-daily-2026-04-20T08:00:00Z",
                "status": "completed",
                "input": "analyze metrics",
                "model": "claude-sonnet",
                "totalTokens": 5000,
                "durationMs": 3000,
                "artifactRefs": ["outputs/abc_report.pdf"],
                "startedAt": now_iso,
                "completedAt": now_iso,
            },
        ],
    }

    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/agents/{agentId}/runs",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert len(data["runs"]) == 1
    assert data["runs"][0]["runId"] == "01JWXYZ"
    assert data["runs"][0]["artifactCount"] == 1
    assert "artifactRefs" not in data["runs"][0]


def test_list_runs_marks_stale_as_timeout(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    stale_iso = "2026-04-19T00:00:00Z"
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [
            {
                "agentId": "agt-test",
                "runId": "01JOLD",
                "workspaceId": workspace_id,
                "trigger": "schedule",
                "status": "running",
                "input": "stuck run",
                "startedAt": stale_iso,
                "artifactRefs": [],
            },
        ],
    }

    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/agents/{agentId}/runs",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())

    data = json.loads(resp["body"])
    assert data["runs"][0]["status"] == "timeout"


def test_get_run_returns_presigned_url(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    fake_table = MagicMock()
    fake_table.get_item.return_value = {
        "Item": {
            "agentId": "agt-test",
            "runId": "01JWXYZ",
            "workspaceId": workspace_id,
            "trigger": "schedule",
            "status": "completed",
            "input": "analyze",
            "outputRef": "runs/agt-test/01JWXYZ/output.json",
            "artifactRefs": ["outputs/abc_report.pdf"],
            "startedAt": "2026-04-20T08:00:00Z",
            "completedAt": "2026-04-20T08:00:08Z",
            "promptTokens": 1000,
            "completionTokens": 2000,
            "totalTokens": 3000,
            "durationMs": 8000,
            "model": "claude-sonnet",
        },
    }

    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01JWXYZ",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01JWXYZ"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table), \
         patch("crud.runs._generate_presigned_url", return_value="https://presigned.example.com/output.json"):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["outputUrl"] == "https://presigned.example.com/output.json"
    assert data["artifactRefs"][0]["key"] == "outputs/abc_report.pdf"


def test_list_runs_forbidden_cross_workspace(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/agents/{agentId}/runs",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other-ws"}
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 403

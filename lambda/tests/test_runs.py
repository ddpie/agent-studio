"""Tests for lambda/crud/runs.py."""
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


def _base_event(ws_id, path, path_params, resource, query=None):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{ws_id}{path}",
        "pathParameters": path_params,
        "resource": f"/api/workspaces/{{wsId}}{resource}",
        "headers": {"Authorization": "Bearer tok", "x-origin-verify": ""},
        "queryStringParameters": query,
        "body": None,
        "requestContext": {},
    }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestExtractFilename:
    def test_strips_uuid_prefix(self):
        from crud.runs import _extract_filename
        assert _extract_filename("outputs/agt/abc123_report.pdf") == "report.pdf"

    def test_no_underscore_returns_basename(self):
        from crud.runs import _extract_filename
        assert _extract_filename("outputs/agt/file.txt") == "file.txt"

    def test_root_filename(self):
        from crud.runs import _extract_filename
        assert _extract_filename("file.txt") == "file.txt"


class TestIsStaleRunning:
    def test_not_running_is_not_stale(self):
        from crud.runs import _is_stale_running
        assert _is_stale_running({"status": "completed"}) is False

    def test_running_no_started_at_is_stale(self):
        from crud.runs import _is_stale_running
        assert _is_stale_running({"status": "running", "startedAt": ""}) is True

    def test_running_recent_not_stale(self):
        from crud.runs import _is_stale_running
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        assert _is_stale_running({"status": "running", "startedAt": now_iso}) is False

    def test_running_old_is_stale(self):
        from crud.runs import _is_stale_running
        # 2020 — well past 10-minute threshold
        assert _is_stale_running({"status": "running", "startedAt": "2020-01-01T00:00:00Z"}) is True

    def test_invalid_started_at_is_stale(self):
        from crud.runs import _is_stale_running
        assert _is_stale_running({"status": "running", "startedAt": "not-a-date"}) is True


class TestFormatListItem:
    def test_basic(self):
        from crud.runs import _format_list_item
        item = {
            "runId": "r1",
            "trigger": "schedule",
            "scheduleId": "s1",
            "status": "completed",
            "input": "x",
            "model": "m",
            "totalTokens": 100,
            "durationMs": 50,
            "artifactRefs": ["k1", "k2"],
            "startedAt": "2026-04-01T00:00:00Z",
            "completedAt": "2026-04-01T00:00:01Z",
        }
        out = _format_list_item(item)
        assert out["runId"] == "r1"
        assert out["artifactCount"] == 2
        # artifactRefs absent from list output
        assert "artifactRefs" not in out

    def test_marks_stale_running_as_timeout(self):
        from crud.runs import _format_list_item
        item = {"runId": "r2", "status": "running", "startedAt": "2020-01-01T00:00:00Z"}
        out = _format_list_item(item)
        assert out["status"] == "timeout"


class TestLazyInit:
    def test_get_runs_table_caches(self):
        import crud.runs as r
        r._runs_table = None
        with patch("crud.runs.boto3.resource") as mk:
            tbl = MagicMock()
            mk.return_value.Table.return_value = tbl
            t1 = r._get_runs_table()
            t2 = r._get_runs_table()
            assert t1 is t2
            mk.assert_called_once()
        r._runs_table = None

    def test_get_agents_table_caches(self):
        import crud.runs as r
        r._agents_table = None
        with patch("crud.runs.boto3.resource") as mk:
            tbl = MagicMock()
            mk.return_value.Table.return_value = tbl
            t1 = r._get_agents_table()
            t2 = r._get_agents_table()
            assert t1 is t2
        r._agents_table = None

    def test_get_s3_caches(self):
        import crud.runs as r
        r._s3 = None
        with patch("crud.runs.boto3.client") as mk:
            mk.return_value = MagicMock()
            c1 = r._get_s3()
            c2 = r._get_s3()
            assert c1 is c2
            mk.assert_called_once()
        r._s3 = None

    def test_generate_presigned_url(self):
        import crud.runs as r
        fake_s3 = MagicMock()
        fake_s3.generate_presigned_url.return_value = "https://signed/x"
        with patch("crud.runs._get_s3", return_value=fake_s3):
            url = r._generate_presigned_url("a/b.txt", expires_in=300)
        assert url == "https://signed/x"

    def test_get_agent_item(self):
        import crud.runs as r
        fake_table = MagicMock()
        fake_table.get_item.return_value = {"Item": {"agentId": "x"}}
        with patch("crud.runs._get_agents_table", return_value=fake_table):
            item = r._get_agent_item("x")
        assert item == {"agentId": "x"}


# ---------------------------------------------------------------------------
# GET /agents/{agentId}/runs   (list)
# ---------------------------------------------------------------------------


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


def test_list_runs_with_schedule_id_filter(mock_jwt, user_id, workspace_id):
    """schedule_id query param adds FilterExpression."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {"Items": []}

    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/agents/{agentId}/runs",
        query={"scheduleId": "sched-1", "limit": "20"},
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    call_kwargs = fake_table.query.call_args.kwargs
    assert call_kwargs["FilterExpression"] == "scheduleId = :sid"
    assert call_kwargs["ExpressionAttributeValues"][":sid"] == "sched-1"


def test_list_runs_with_next_token(mock_jwt, user_id, workspace_id):
    """nextToken query param decodes to ExclusiveStartKey."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [],
        "LastEvaluatedKey": {"agentId": "x", "runId": "y"},
    }
    next_token = json.dumps({"agentId": "agt-test", "runId": "01JLAST"})
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/agents/{agentId}/runs",
        query={"nextToken": next_token},
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert "nextToken" in data
    call_kwargs = fake_table.query.call_args.kwargs
    assert call_kwargs["ExclusiveStartKey"] == {"agentId": "agt-test", "runId": "01JLAST"}


def test_list_runs_invalid_next_token(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/agents/{agentId}/runs",
        query={"nextToken": "not-json"},
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_list_runs_query_clienterror_returns_500(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.side_effect = ClientError(
        {"Error": {"Code": "X", "Message": "boom"}}, "Query"
    )
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
    assert resp["statusCode"] == 500


def test_list_runs_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    bad = "bad$id"
    event = _base_event(
        workspace_id,
        f"/agents/{bad}/runs",
        {"wsId": workspace_id, "agentId": bad},
        "/agents/{agentId}/runs",
    )
    with patch("crud.runs.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_list_runs_auth_failure(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/agents/{agentId}/runs",
    )
    with patch("crud.runs.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


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


# ---------------------------------------------------------------------------
# GET /agents/{agentId}/runs/{runId}
# ---------------------------------------------------------------------------


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


def test_get_run_marks_stale_running_as_timeout(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.get_item.return_value = {
        "Item": {
            "agentId": "agt-test",
            "runId": "01JSTALE",
            "workspaceId": workspace_id,
            "trigger": "schedule",
            "status": "running",
            "input": "x",
            "startedAt": "2020-01-01T00:00:00Z",
        },
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01JSTALE",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01JSTALE"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    data = json.loads(resp["body"])
    assert data["status"] == "timeout"


def test_get_run_presign_clienterror_swallowed(mock_jwt, user_id, workspace_id):
    """If presigning fails, outputUrl is None but request still succeeds."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.get_item.return_value = {
        "Item": {
            "agentId": "agt-test",
            "runId": "01J1",
            "workspaceId": workspace_id,
            "status": "completed",
            "outputRef": "runs/agt/01J1/output.json",
        },
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01J1",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01J1"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table), \
         patch("crud.runs._generate_presigned_url", side_effect=ClientError(
             {"Error": {"Code": "X", "Message": "x"}}, "GetObject"
         )):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["outputUrl"] is None


def test_get_run_not_found(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": None}
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01JNONE",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01JNONE"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404


def test_get_run_clienterror_returns_500(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.get_item.side_effect = ClientError(
        {"Error": {"Code": "X", "Message": "boom"}}, "GetItem"
    )
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01JX",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01JX"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_get_run_workspace_mismatch_in_item(mock_jwt, user_id, workspace_id):
    """run item has workspaceId pointing elsewhere → forbidden."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.get_item.return_value = {
        "Item": {
            "runId": "01JX",
            "agentId": "agt-test",
            "workspaceId": "other-ws",
            "status": "completed",
        },
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01JX",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01JX"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga, \
         patch("crud.runs._get_runs_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_run_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    bad = "bad$id"
    event = _base_event(
        workspace_id,
        f"/agents/{bad}/runs/01JX",
        {"wsId": workspace_id, "agentId": bad, "runId": "01JX"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_run_invalid_run_id(mock_jwt, user_id, workspace_id):
    """runId outside the safe charset → 400."""
    from crud.handler import app
    bad_run_id = "$bad$"
    event = _base_event(
        workspace_id,
        f"/agents/agt-test/runs/{bad_run_id}",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": bad_run_id},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_run_auth_failure(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01JX",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01JX"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_run_agent_other_workspace_forbidden(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    event = _base_event(
        workspace_id,
        "/agents/agt-test/runs/01JX",
        {"wsId": workspace_id, "agentId": "agt-test", "runId": "01JX"},
        "/agents/{agentId}/runs/{runId}",
    )
    with patch("crud.runs.auth_check") as auth, \
         patch("crud.runs._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other-ws"}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403

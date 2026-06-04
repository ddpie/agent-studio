"""Tests for crud/traces.py — OTEL span tree assembly."""

import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("SPANS_LOG_GROUP", "aws/spans")
    import importlib

    import shared.config as _cfg

    importlib.reload(_cfg)


def _base_event(
    workspace_id: str, path_suffix: str, path_params: dict, resource: str, query: dict | None = None
):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{workspace_id}{path_suffix}",
        "resource": resource,
        "pathParameters": path_params,
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": query,
    }


# ---------------------------------------------------------------------------
# Helper functions  (_field, _as_utc_iso, _to_float, _to_int, _round_ms)
# ---------------------------------------------------------------------------


class TestFieldHelper:
    def test_field_present(self):
        from crud.traces import _field

        row = [{"field": "a", "value": "1"}, {"field": "b", "value": "2"}]
        assert _field(row, "a") == "1"
        assert _field(row, "b") == "2"

    def test_field_missing(self):
        from crud.traces import _field

        assert _field([], "missing") is None
        assert _field([{"field": "x", "value": "y"}], "z") is None


class TestAsUtcIso:
    def test_none_returns_none(self):
        from crud.traces import _as_utc_iso

        assert _as_utc_iso(None) is None

    def test_empty_returns_empty(self):
        from crud.traces import _as_utc_iso

        assert _as_utc_iso("") == ""
        # whitespace strips to empty
        assert _as_utc_iso("   ") == ""

    def test_already_zulu(self):
        from crud.traces import _as_utc_iso

        assert _as_utc_iso("2026-04-19T00:00:00Z") == "2026-04-19T00:00:00Z"

    def test_offset_kept(self):
        from crud.traces import _as_utc_iso

        assert _as_utc_iso("2026-04-19T00:00:00+05:00") == "2026-04-19T00:00:00+05:00"

    def test_utc_suffix_kept(self):
        from crud.traces import _as_utc_iso

        assert _as_utc_iso("2026-04-19 00:00:00 UTC") == "2026-04-19 00:00:00 UTC"

    def test_naive_normalised(self):
        from crud.traces import _as_utc_iso

        assert _as_utc_iso("2026-04-19 00:00:00.123") == "2026-04-19T00:00:00.123Z"


class TestNumericHelpers:
    def test_to_float(self):
        from crud.traces import _to_float

        assert _to_float(None) is None
        assert _to_float("3.14") == 3.14
        assert _to_float("bad") is None
        assert _to_float(float("nan")) is None
        assert _to_float(0) == 0.0

    def test_to_int(self):
        from crud.traces import _to_int

        assert _to_int(None) == 0
        assert _to_int("5.7") == 5
        assert _to_int("bad") == 0
        assert _to_int("bad", default=42) == 42

    def test_round_ms(self):
        from crud.traces import _round_ms

        assert _round_ms(None) is None
        assert _round_ms("12.4") == 12
        assert _round_ms("12.6") == 13
        assert _round_ms("bad") is None


# ---------------------------------------------------------------------------
# _run_query helper paths  (failed/cancelled/timeout)
# ---------------------------------------------------------------------------


class TestRunQuery:
    def test_complete_returns_results(self):
        from crud.traces import _run_query

        fake_logs = MagicMock()
        fake_logs.start_query.return_value = {"queryId": "q1"}
        fake_logs.get_query_results.return_value = {
            "status": "Complete",
            "results": [[{"field": "x", "value": "y"}]],
        }
        with patch("crud.traces._get_logs", return_value=fake_logs):
            rows = _run_query("query", hours=1, timeout_s=5)
        assert len(rows) == 1

    def test_failed_returns_empty(self):
        from crud.traces import _run_query

        fake_logs = MagicMock()
        fake_logs.start_query.return_value = {"queryId": "q1"}
        fake_logs.get_query_results.return_value = {"status": "Failed", "results": []}
        with patch("crud.traces._get_logs", return_value=fake_logs):
            rows = _run_query("query", hours=1, timeout_s=5)
        assert rows == []

    def test_cancelled_returns_empty(self):
        from crud.traces import _run_query

        fake_logs = MagicMock()
        fake_logs.start_query.return_value = {"queryId": "q1"}
        fake_logs.get_query_results.return_value = {"status": "Cancelled", "results": []}
        with patch("crud.traces._get_logs", return_value=fake_logs):
            rows = _run_query("query", hours=1, timeout_s=5)
        assert rows == []

    def test_timeout_calls_stop(self):
        """When the deadline expires before status==Complete, _run_query
        attempts to stop the query and returns []. Use timeout_s=0 to
        force the deadline to expire on entry to the while loop without
        patching time.time globally.
        """
        from crud.traces import _run_query

        fake_logs = MagicMock()
        fake_logs.start_query.return_value = {"queryId": "q1"}
        fake_logs.get_query_results.return_value = {"status": "Running", "results": []}
        with patch("crud.traces._get_logs", return_value=fake_logs):
            rows = _run_query("query", hours=1, timeout_s=-1)
        assert rows == []
        fake_logs.stop_query.assert_called_once_with(queryId="q1")

    def test_timeout_stop_query_clienterror_swallowed(self):
        from crud.traces import _run_query

        fake_logs = MagicMock()
        fake_logs.start_query.return_value = {"queryId": "q1"}
        fake_logs.get_query_results.return_value = {"status": "Running", "results": []}
        fake_logs.stop_query.side_effect = ClientError({"Error": {"Code": "X", "Message": "x"}}, "StopQuery")
        with patch("crud.traces._get_logs", return_value=fake_logs):
            rows = _run_query("query", hours=1, timeout_s=-1)
        assert rows == []


# ---------------------------------------------------------------------------
# Lazy boto3 init
# ---------------------------------------------------------------------------


class TestLazyInit:
    def test_get_logs_caches(self):
        import crud.traces as t

        t._logs = None
        with patch("crud.traces.boto3.client") as mk:
            mk.return_value = MagicMock(name="logs")
            c1 = t._get_logs()
            c2 = t._get_logs()
            assert c1 is c2
            mk.assert_called_once()
        t._logs = None

    def test_get_agent_item_caches_table(self):
        import crud.traces as t

        t._agents_table = None
        fake_resource = MagicMock()
        fake_table = MagicMock()
        fake_table.get_item.return_value = {"Item": {"agentId": "a"}}
        fake_resource.Table.return_value = fake_table
        with patch("crud.traces.boto3.resource", return_value=fake_resource):
            item = t._get_agent_item("a")
            assert item == {"agentId": "a"}
            t._get_agent_item("a")
        t._agents_table = None


# ---------------------------------------------------------------------------
# GET /traces  (list endpoint)
# ---------------------------------------------------------------------------


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
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
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


def test_list_traces_skips_rows_without_session_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q-1"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [
                {"field": "sessionId", "value": ""},  # falsy, skipped
                {"field": "traceId", "value": "x"},
                {"field": "firstEvent", "value": "2026-04-19 00:00:00.000"},
                {"field": "spanCount", "value": "5"},
            ],
            [
                {"field": "sessionId", "value": "sess-b"},
                {"field": "traceId", "value": "trace-b"},
                {"field": "firstEvent", "value": "2026-04-19 00:02:00.000"},
                {"field": "spanCount", "value": "bad"},  # ValueError -> count=0
            ],
        ],
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["spanCount"] == 0


def test_list_traces_meta_rows_merge(mock_jwt, user_id, workspace_id):
    """Rows from the second meta query merge into list rows by sessionId."""
    from crud.handler import app

    list_rows = [
        [
            {"field": "sessionId", "value": "sess-a"},
            {"field": "traceId", "value": "trace-a"},
            {"field": "firstEvent", "value": "2026-04-19 00:00:00.000"},
            {"field": "spanCount", "value": "12"},
        ],
    ]
    meta_rows = [
        [
            {"field": "sid", "value": "sess-a"},
            {"field": "model", "value": "claude-opus"},
            {"field": "totalTokens", "value": "1500"},
            {"field": "durMs", "value": "250"},
            {"field": "statusCode", "value": "OK"},
        ],
        [
            {"field": "sid", "value": ""},  # skipped (no sid)
            {"field": "model", "value": "ignored"},
        ],
    ]
    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q-1"}
    fake_logs.get_query_results.side_effect = [
        {"status": "Complete", "results": list_rows},
        {"status": "Complete", "results": meta_rows},
    ]
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    sess = data["sessions"][0]
    assert sess["model"] == "claude-opus"
    assert sess["totalTokens"] == 1500
    assert sess["durationMs"] == 250


def test_list_traces_list_query_clienterror_returns_500(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    fake_logs = MagicMock()
    fake_logs.start_query.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
        "StartQuery",
    )
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_list_traces_meta_query_clienterror_swallowed(mock_jwt, user_id, workspace_id):
    """A ClientError on the meta query keeps the list result; no meta values."""
    from crud.handler import app

    list_rows = [
        [
            {"field": "sessionId", "value": "sess-a"},
            {"field": "traceId", "value": "t"},
            {"field": "firstEvent", "value": "2026-04-19 00:00:00.000"},
            {"field": "spanCount", "value": "1"},
        ],
    ]
    # First call returns list rows; second call (meta) raises ClientError
    call_count = {"n": 0}

    def fake_run_query(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return list_rows
        raise ClientError({"Error": {"Code": "X", "Message": "boom"}}, "StartQuery")

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._run_query", side_effect=fake_run_query),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    sess = data["sessions"][0]
    assert sess["model"] is None
    assert sess["status"] == "OK"


def test_list_traces_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    bad = "bad$id"
    event = _base_event(
        workspace_id,
        f"/agents/{bad}/traces",
        {"wsId": workspace_id, "agentId": bad},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = None
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_list_traces_auth_check_failure(mock_jwt, user_id, workspace_id):
    """When auth_check returns an error response, the route returns it directly."""
    from crud.handler import app
    from shared.response import forbidden

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with patch("crud.traces.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_list_traces_forbidden_when_agent_in_other_workspace(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other-ws"}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_list_traces_agent_missing_returns_403(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces",
        {"wsId": workspace_id, "agentId": "agt-test"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces",
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = None
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# GET /traces/{sessionId}
# ---------------------------------------------------------------------------


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
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["root"]["spanId"] == "sp-root"
    assert len(data["root"]["children"]) == 1
    assert data["root"]["children"][0]["name"] == "tool:run_command"
    assert data["root"]["durationMs"] == 1500
    assert data["root"]["children"][0]["durationMs"] == 800
    assert data["totalSpans"] == 2


def test_get_session_trace_synthetic_root_when_all_rows_invalid(mock_jwt, user_id, workspace_id):
    """When all rows have empty spanId (skipped) but at least one row
    exists, the function reaches the spans_by_id assembly with an empty
    dict — no root, no orphans, so the synthetic root branch fires.
    """
    from crud.handler import app

    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            # Non-empty rows but all skip due to empty spanId
            [{"field": "spanId", "value": ""}, {"field": "name", "value": "x"}],
            [{"field": "spanId", "value": ""}, {"field": "name", "value": "y"}],
        ],
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-empty",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-empty"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["root"]["spanId"] == "__synthetic__"
    assert data["root"]["name"] == "session:sess-empty"
    assert data["root"]["children"] == []
    assert data["totalSpans"] == 0


def test_get_session_trace_with_orphans(mock_jwt, user_id, workspace_id):
    """Multiple roots → first becomes root, rest are appended as orphans."""
    from crud.handler import app

    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q-2"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [
                {"field": "spanId", "value": "sp-r1"},
                {"field": "name", "value": "first-root"},
                {"field": "startTimeUnixNano", "value": "1000000000"},
                {"field": "endTimeUnixNano", "value": "2000000000"},
            ],
            [
                {"field": "spanId", "value": "sp-r2"},
                {"field": "name", "value": "second-root"},
                {"field": "startTimeUnixNano", "value": "3000000000"},
                {"field": "endTimeUnixNano", "value": "4000000000"},
            ],
        ],
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-orph",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-orph"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    # first root keeps; second is appended as orphan child
    assert data["root"]["spanId"] == "sp-r1"
    child_ids = [c["spanId"] for c in data["root"]["children"]]
    assert "sp-r2" in child_ids


def test_get_session_trace_skips_invalid_rows(mock_jwt, user_id, workspace_id):
    """Rows missing spanId or with non-numeric times are skipped."""
    from crud.handler import app

    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q-2"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [
                {"field": "spanId", "value": ""},  # skipped
                {"field": "name", "value": "x"},
            ],
            [
                {"field": "spanId", "value": "sp-bad"},
                {"field": "name", "value": "n"},
                {"field": "startTimeUnixNano", "value": "not-a-number"},
                {"field": "endTimeUnixNano", "value": "0"},
            ],
            [
                {"field": "spanId", "value": "sp-good"},
                {"field": "name", "value": "ok"},
                {"field": "startTimeUnixNano", "value": "1000"},
                {"field": "endTimeUnixNano", "value": "2000"},
            ],
        ],
    }
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-x",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-x"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["totalSpans"] == 1
    assert data["root"]["spanId"] == "sp-good"


def test_get_session_trace_no_rows_returns_404(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q"}
    fake_logs.get_query_results.return_value = {"status": "Complete", "results": []}
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-empty",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-empty"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404


def test_get_session_trace_query_clienterror_returns_500(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-x",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-x"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch(
            "crud.traces._run_query",
            side_effect=ClientError({"Error": {"Code": "X", "Message": "boom"}}, "StartQuery"),
        ),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_get_session_trace_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    bad = "bad$id"
    event = _base_event(
        workspace_id,
        f"/agents/{bad}/traces/sess-x",
        {"wsId": workspace_id, "agentId": bad, "sessionId": "sess-x"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = None
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_session_trace_agent_other_workspace_forbidden(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-x",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-x"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other-ws"}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_session_trace_auth_failure(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from shared.response import forbidden

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/sess-x",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "sess-x"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
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
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# /traces/stats  (the literal "stats" sessionId short-circuits to stats handler)
# ---------------------------------------------------------------------------


def _stats_logs_with(summary_rows, series_rows):
    fake_logs = MagicMock()
    fake_logs.start_query.return_value = {"queryId": "q"}
    fake_logs.get_query_results.side_effect = [
        {"status": "Complete", "results": summary_rows},
        {"status": "Complete", "results": series_rows},
    ]
    return fake_logs


def test_get_trace_stats_default_24h(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    summary_rows = [
        [
            {"field": "total", "value": "10"},
            {"field": "errors", "value": "2"},
            {"field": "avgMs", "value": "300.7"},
            {"field": "p50", "value": "100"},
            {"field": "p90", "value": "400"},
            {"field": "p95", "value": "450"},
            {"field": "p99", "value": "500"},
        ]
    ]
    series_rows = [
        [
            {"field": "bucket", "value": "2026-04-19 00:00:00.000"},
            {"field": "c", "value": "5"},
            {"field": "e", "value": "1"},
            {"field": "p95", "value": "120"},
        ],
        [
            {"field": "bucket", "value": "2026-04-19 01:00:00"},  # no .ms
            {"field": "c", "value": "3"},
            {"field": "e", "value": "0"},
            {"field": "p95", "value": "100"},
        ],
        [
            {"field": "bucket", "value": ""},  # skipped (no bucket)
        ],
    ]
    fake_logs = _stats_logs_with(summary_rows, series_rows)
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/stats",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["range"] == "24h"
    assert data["count"] == 10
    assert data["errorCount"] == 2
    assert data["errorRate"] == 0.2
    assert data["latencyMs"]["p50"] == 100
    assert data["latencyMs"]["avg"] == 301
    # Two buckets parsed
    assert len(data["timeseries"]) == 2
    assert data["bucketSeconds"] == 3600


def test_get_trace_stats_explicit_7d(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    fake_logs = _stats_logs_with([], [])
    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/stats",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
        query={"range": "7d"},
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._get_logs", return_value=fake_logs),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["range"] == "7d"
    assert data["count"] == 0
    assert data["errorRate"] == 0.0
    assert data["bucketSeconds"] == 6 * 3600


def test_get_trace_stats_invalid_range(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/stats",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
        query={"range": "weekly"},
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_trace_stats_invalid_agent_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    bad = "bad$id"
    event = _base_event(
        workspace_id,
        f"/agents/{bad}/traces/stats",
        {"wsId": workspace_id, "agentId": bad, "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = None
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_trace_stats_agent_in_other_workspace(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/stats",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth, patch("crud.traces._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other-ws"}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_trace_stats_auth_failure(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from shared.response import forbidden

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/stats",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with patch("crud.traces.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_trace_stats_summary_clienterror_returns_500(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/stats",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch(
            "crud.traces._run_query",
            side_effect=ClientError({"Error": {"Code": "X", "Message": "boom"}}, "StartQuery"),
        ),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_get_trace_stats_series_clienterror_keeps_summary(mock_jwt, user_id, workspace_id):
    from crud.handler import app

    summary_rows = [
        [
            {"field": "total", "value": "10"},
            {"field": "errors", "value": "0"},
            {"field": "avgMs", "value": "100"},
            {"field": "p50", "value": "100"},
            {"field": "p90", "value": "100"},
            {"field": "p95", "value": "100"},
            {"field": "p99", "value": "100"},
        ]
    ]
    call_count = {"n": 0}

    def fake_run_query(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return summary_rows
        raise ClientError({"Error": {"Code": "X", "Message": "boom"}}, "StartQuery")

    event = _base_event(
        workspace_id,
        "/agents/agt-test/traces/stats",
        {"wsId": workspace_id, "agentId": "agt-test", "sessionId": "stats"},
        "/api/workspaces/{wsId}/agents/{agentId}/traces/{sessionId}",
    )
    with (
        patch("crud.traces.auth_check") as auth,
        patch("crud.traces._get_agent_item") as ga,
        patch("crud.traces._run_query", side_effect=fake_run_query),
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["count"] == 10
    assert data["timeseries"] == []

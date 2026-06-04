"""Tests for crud.logs — agent runtime log viewer."""
import json
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "")


@pytest.fixture
def mock_logs():
    with patch("crud.logs._get_logs") as g:
        c = MagicMock()
        g.return_value = c
        yield c


@pytest.fixture
def mock_get_agent_item():
    with patch("crud.logs._get_agent_item") as g:
        g.return_value = None
        yield g


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "viewer",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    with patch("shared.middleware.get_membership", return_value=None):
        yield


def _apigw(method, path, query_params=None):
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "requestContext": {
            "stage": "test",
            "requestId": "req-1",
            "identity": {"sourceIp": "127.0.0.1"},
        },
        "headers": {
            "Authorization": "Bearer X",
            "Content-Type": "application/json",
        },
        "body": None,
        "queryStringParameters": query_params or {},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestParseLevel:
    def test_bracket_error(self):
        from crud.logs import _parse_level
        assert _parse_level("[ERROR] something failed") == "ERROR"

    def test_bracket_warning_normalizes_to_warn(self):
        from crud.logs import _parse_level
        assert _parse_level("[WARNING] something") == "WARN"

    def test_bracket_warn(self):
        from crud.logs import _parse_level
        assert _parse_level("[WARN] something") == "WARN"

    def test_bracket_info(self):
        from crud.logs import _parse_level
        assert _parse_level("[INFO] hello") == "INFO"

    def test_bracket_debug(self):
        from crud.logs import _parse_level
        assert _parse_level("[DEBUG] x") == "DEBUG"

    def test_python_logging_format(self):
        from crud.logs import _parse_level
        assert _parse_level("2026-04-25 12:00:00,123 ERROR [app] msg") == "ERROR"

    def test_python_logging_warning_normalized(self):
        from crud.logs import _parse_level
        assert _parse_level("2026-04-25 12:00:00,123 WARNING [x] msg") == "WARN"

    def test_simple_prefix_error(self):
        from crud.logs import _parse_level
        assert _parse_level("ERROR: oops") == "ERROR"

    def test_simple_prefix_info(self):
        from crud.logs import _parse_level
        assert _parse_level("INFO: hello") == "INFO"

    def test_no_match_returns_empty(self):
        from crud.logs import _parse_level
        assert _parse_level("just a regular line") == ""

    def test_empty_returns_empty(self):
        from crud.logs import _parse_level
        assert _parse_level("") == ""

    def test_none_returns_empty(self):
        from crud.logs import _parse_level
        assert _parse_level(None) == ""


class TestTruncate:
    def test_short_returns_as_is(self):
        from crud.logs import _truncate
        assert _truncate("hello") == "hello"

    def test_empty(self):
        from crud.logs import _truncate
        assert _truncate("") == ""

    def test_none(self):
        from crud.logs import _truncate
        assert _truncate(None) == ""

    def test_long_truncated(self):
        from crud.logs import _truncate, MAX_MESSAGE_CHARS
        msg = "x" * (MAX_MESSAGE_CHARS + 100)
        result = _truncate(msg)
        assert len(result) == MAX_MESSAGE_CHARS + len("\n…[truncated]")
        assert result.endswith("[truncated]")


class TestBuildFilterPattern:
    def test_no_level_no_search(self):
        from crud.logs import _build_filter_pattern
        assert _build_filter_pattern("", "") == ""

    def test_all_level(self):
        from crud.logs import _build_filter_pattern
        # "ALL" returns search-only pattern
        assert _build_filter_pattern("ALL", "") == ""

    def test_error_level(self):
        from crud.logs import _build_filter_pattern
        assert _build_filter_pattern("ERROR", "") == '"ERROR"'

    def test_warn_includes_warning(self):
        from crud.logs import _build_filter_pattern
        # WARN matches both WARN and WARNING
        assert _build_filter_pattern("WARN", "") == '?"WARN" ?"WARNING"'

    def test_info_level(self):
        from crud.logs import _build_filter_pattern
        assert _build_filter_pattern("INFO", "") == '"INFO"'

    def test_search_only(self):
        from crud.logs import _build_filter_pattern
        assert _build_filter_pattern("", "needle") == '"needle"'

    def test_search_only_with_all(self):
        from crud.logs import _build_filter_pattern
        assert _build_filter_pattern("ALL", "needle") == '"needle"'

    def test_level_takes_precedence(self):
        from crud.logs import _build_filter_pattern
        # When both set, level wins (we filter search post-fetch)
        assert _build_filter_pattern("ERROR", "needle") == '"ERROR"'


class TestLazyInit:
    def test_get_logs_caches(self):
        import crud.logs as mod
        mod._logs = None
        sentinel = MagicMock()
        with patch("boto3.client", return_value=sentinel) as bc:
            assert mod._get_logs() is sentinel
            assert mod._get_logs() is sentinel
            bc.assert_called_once()
        mod._logs = None

    def test_get_agent_item_calls_table(self):
        import crud.logs as mod
        mod._agents_table = None
        fake_table = MagicMock()
        fake_table.get_item.return_value = {"Item": {"agentId": "agt1"}}
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table
        with patch("boto3.resource", return_value=fake_resource):
            assert mod._get_agent_item("agt1") == {"agentId": "agt1"}
        # Second call uses cached table
        with patch("boto3.resource") as br:
            mod._get_agent_item("agt1")
            br.assert_not_called()
        mod._agents_table = None

    def test_get_agent_item_returns_none_for_missing(self):
        import crud.logs as mod
        mod._agents_table = None
        fake_table = MagicMock()
        fake_table.get_item.return_value = {}
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table
        with patch("boto3.resource", return_value=fake_resource):
            assert mod._get_agent_item("agt1") is None
        mod._agents_table = None


# ---------------------------------------------------------------------------
# GET /agents/{agentId}/logs
# ---------------------------------------------------------------------------


class TestGetAgentLogs:
    def test_returns_events(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.return_value = {
            "events": [
                {"timestamp": 1700000001000, "message": "[ERROR] something",
                 "logStreamName": "s1"},
                {"timestamp": 1700000002000, "message": "regular line",
                 "logStreamName": "s2"},
            ],
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["events"]) == 2
        assert data["events"][0]["level"] == "ERROR"
        assert data["events"][1]["level"] == ""
        assert "nextCursor" not in data

    def test_returns_next_cursor(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.return_value = {
            "events": [],
            "nextToken": "abc-def",
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["nextCursor"] == "abc-def"

    def test_log_group_not_found_returns_empty(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "FilterLogEvents"
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["events"] == []

    def test_other_client_error_returns_500(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError"}}, "FilterLogEvents"
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        assert resp["statusCode"] == 500

    def test_invalid_agent_id(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/bad agt/logs"))
        assert resp["statusCode"] == 400

    def test_agent_not_found(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = None
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        assert resp["statusCode"] == 403

    def test_agent_in_other_workspace(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": "other-ws"
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        assert resp["statusCode"] == 403

    def test_invalid_since(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"since": "7d"},
        ))
        assert resp["statusCode"] == 400
        assert "since" in json.loads(resp["body"])["error"]

    def test_invalid_level(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"level": "TRACE"},
        ))
        assert resp["statusCode"] == 400
        assert "level" in json.loads(resp["body"])["error"]

    def test_invalid_search(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        # exotic chars rejected
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"search": "$$inject%%"},
        ))
        assert resp["statusCode"] == 400
        assert "search" in json.loads(resp["body"])["error"]

    def test_valid_since_15m(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.return_value = {"events": []}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"since": "15m"},
        ))
        assert resp["statusCode"] == 200

    def test_valid_since_24h(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.return_value = {"events": []}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"since": "24h"},
        ))
        assert resp["statusCode"] == 200

    def test_valid_search_param_passed(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.return_value = {"events": []}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"search": "needle"},
        ))
        assert resp["statusCode"] == 200
        call_kwargs = mock_logs.filter_log_events.call_args.kwargs
        assert call_kwargs["filterPattern"] == '"needle"'

    def test_level_and_search_post_filters_in_proc(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        """When both level and search are set, level is the CW filter and
        search is applied in-process over the page."""
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.return_value = {
            "events": [
                {"timestamp": 1, "message": "[ERROR] needle in haystack", "logStreamName": "s1"},
                {"timestamp": 2, "message": "[ERROR] some other error", "logStreamName": "s1"},
            ]
        }
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"level": "ERROR", "search": "needle"},
        ))
        assert resp["statusCode"] == 200
        events = json.loads(resp["body"])["events"]
        assert len(events) == 1
        assert "needle" in events[0]["message"]

        # CW filterPattern should be the level pattern, not search
        call_kwargs = mock_logs.filter_log_events.call_args.kwargs
        assert call_kwargs["filterPattern"] == '"ERROR"'

    def test_cursor_passed_through(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        mock_logs.filter_log_events.return_value = {"events": []}
        _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs",
            query_params={"cursor": "tok-123"},
        ))
        kwargs = mock_logs.filter_log_events.call_args.kwargs
        assert kwargs["nextToken"] == "tok-123"

    def test_truncates_long_messages(
        self, workspace_id, mock_jwt, _mock_viewer, mock_logs, mock_get_agent_item
    ):
        from crud.logs import MAX_MESSAGE_CHARS
        mock_get_agent_item.return_value = {
            "agentId": "agt1", "workspace_id": workspace_id
        }
        long_msg = "x" * (MAX_MESSAGE_CHARS + 100)
        mock_logs.filter_log_events.return_value = {
            "events": [{"timestamp": 1, "message": long_msg, "logStreamName": "s1"}]
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        events = json.loads(resp["body"])["events"]
        assert events[0]["message"].endswith("[truncated]")

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_logs
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/agents/agt1/logs"))
        assert resp["statusCode"] == 403

"""Tests for crud.costs — Logs Insights cost aggregation."""
import json
from unittest.mock import MagicMock, patch

import pytest

from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_logs():
    with patch("crud.costs._get_logs") as g:
        c = MagicMock()
        c.start_query.return_value = {"queryId": "q-1"}
        c.get_query_results.return_value = {"status": "Complete", "results": []}
        c.stop_query.return_value = {}
        g.return_value = c
        yield c


@pytest.fixture
def mock_agents_table():
    with patch("crud.costs._get_agents_table") as g:
        t = MagicMock()
        t.name = "test-agents"
        t.get_item.return_value = {"Item": None}
        # IMPORTANT: explicit LastEvaluatedKey: None to avoid hangs.
        t.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
        g.return_value = t
        yield t


@pytest.fixture
def _mock_editor(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "editor",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    with patch("shared.middleware.get_membership", return_value=None):
        yield


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset module-level cost caches between tests so they don't bleed."""
    import crud.costs as costs_mod
    costs_mod._GLOBAL_AGENTS_CACHE["data"] = None
    costs_mod._GLOBAL_AGENTS_CACHE["expires"] = 0
    costs_mod._GLOBAL_WORKSPACES_CACHE["data"] = None
    costs_mod._GLOBAL_WORKSPACES_CACHE["expires"] = 0
    yield


def _apigw(method, path, query_params=None, headers=None):
    h = {"Authorization": "Bearer tok", "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "headers": h,
        "body": None,
        "queryStringParameters": query_params or {},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


def _row(**fields):
    return [{"field": k, "value": str(v)} for k, v in fields.items()]


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------


class TestUnitPrice:
    def test_known_models(self):
        from crud.costs import _unit_price
        assert _unit_price("anthropic.claude-sonnet-4-6-20260301-v1:0") == (3.00, 15.00)
        assert _unit_price("us.anthropic.claude-haiku-4-5") == (0.80, 4.00)
        assert _unit_price("anthropic.claude-opus-4-7") == (15.00, 75.00)

    def test_nova_short_forms(self):
        from crud.costs import _unit_price
        assert _unit_price("amazon.nova-pro-v1:0") == (0.80, 3.20)
        assert _unit_price("nova.lite") == (0.06, 0.24)
        assert _unit_price("nova-micro-test") == (0.035, 0.14)

    def test_unknown_returns_zero(self):
        from crud.costs import _unit_price
        assert _unit_price("gpt-4-turbo") == (0.0, 0.0)
        assert _unit_price("") == (0.0, 0.0)
        assert _unit_price(None) == (0.0, 0.0)


class TestComputeCost:
    def test_basic_cost(self):
        from crud.costs import _compute_cost
        # 1M input * $3 + 1M output * $15 = $3 + $15 = $18 for sonnet
        cost = _compute_cost(1_000_000, 1_000_000, "claude-sonnet-4-6")
        assert cost == pytest.approx(18.0)

    def test_cache_read_cheap(self):
        from crud.costs import _compute_cost
        # 1M cache_read_input on sonnet: $3 * 0.10 = $0.30
        cost = _compute_cost(0, 0, "claude-sonnet-4-6", cache_read_tokens=1_000_000)
        assert cost == pytest.approx(0.30)

    def test_cache_write_expensive(self):
        from crud.costs import _compute_cost
        # 1M cache_write on sonnet: $3 * 1.25 = $3.75
        cost = _compute_cost(0, 0, "claude-sonnet-4-6", cache_write_tokens=1_000_000)
        assert cost == pytest.approx(3.75)

    def test_unknown_model_zero(self):
        from crud.costs import _compute_cost
        assert _compute_cost(1_000_000, 1_000_000, "unknown-model") == 0.0


class TestParseRange:
    def test_default_7d(self):
        from crud.costs import _parse_range
        s, e, b = _parse_range({})
        assert e - s == 7 * 86400
        assert b == "1d"

    def test_24h(self):
        from crud.costs import _parse_range
        s, e, b = _parse_range({"range": "24h"})
        assert e - s == 24 * 3600
        assert b == "1h"

    def test_30d(self):
        from crud.costs import _parse_range
        s, e, b = _parse_range({"range": "30d"})
        assert e - s == 30 * 86400
        assert b == "1d"

    def test_none(self):
        from crud.costs import _parse_range
        s, e, b = _parse_range(None)
        assert e - s == 7 * 86400


class TestFieldHelpers:
    def test_field_present(self):
        from crud.costs import _field
        row = [{"field": "a", "value": "1"}, {"field": "b", "value": "2"}]
        assert _field(row, "a") == "1"
        assert _field(row, "b") == "2"

    def test_field_missing(self):
        from crud.costs import _field
        assert _field([], "a") is None

    def test_to_int_to_float(self):
        from crud.costs import _to_int, _to_float
        assert _to_int("42") == 42
        assert _to_int("3.14") == 3
        assert _to_int(None) == 0
        assert _to_int("bad") == 0
        assert _to_float("1.5") == 1.5
        assert _to_float(None) == 0.0


# ---------------------------------------------------------------------------
# _run_query
# ---------------------------------------------------------------------------


class TestRunQuery:
    def test_complete(self, mock_logs):
        mock_logs.get_query_results.return_value = {
            "status": "Complete",
            "results": [_row(a="1")],
        }
        from crud.costs import _run_query
        rows = _run_query("query", 0, 100)
        assert len(rows) == 1

    def test_failed_returns_partial(self, mock_logs):
        mock_logs.get_query_results.return_value = {
            "status": "Failed",
            "results": [],
        }
        from crud.costs import _run_query
        rows = _run_query("query", 0, 100)
        assert rows == []

    def test_start_query_client_error(self, mock_logs):
        mock_logs.start_query.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
            "StartQuery",
        )
        from crud.costs import _run_query
        rows = _run_query("query", 0, 100)
        assert rows == []


# ---------------------------------------------------------------------------
# GET /api/workspaces/{ws}/costs
# ---------------------------------------------------------------------------


class TestWorkspaceCosts:
    def test_no_agents_returns_empty_skeleton(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        # mock_agents_table.query returns {Items: [], LastEvaluatedKey: None}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["agents"] == []
        ws = data["workspace"]
        assert ws["totalCalls"] == 0
        assert ws["totalCostUsd"] == 0.0
        assert ws["timeseries"] == []

    def test_with_agents_and_spans(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.query.return_value = {
            "Items": [
                {
                    "agentId": "agt-1",
                    "name": "Agent1",
                    "display_name": "Display 1",
                    "model_id": "claude-sonnet-4-6",
                    "status": "active",
                },
                {
                    "agentId": "agt-2",
                    "name": "Agent2",
                    "model_id": "claude-haiku-4-5",
                    "status": "active",
                },
            ],
            "LastEvaluatedKey": None,
        }
        # Sequence: 4 _run_query calls: per_agent_totals, per_agent_calls,
        # timeseries, timeseries_calls.
        token_results = [
            _row(
                agentRuntimeId="agt-1",
                model="claude-sonnet-4-6",
                inputTokens="1000000",
                outputTokens="500000",
                cacheReadTokens="0",
                cacheWriteTokens="0",
            )
        ]
        call_results = [_row(agentRuntimeId="agt-1", calls="42")]
        ts_results = [
            _row(
                agentRuntimeId="agt-1",
                model="claude-sonnet-4-6",
                bucket="2026-04-19 00:00",
                inputTokens="1000000",
                outputTokens="500000",
                cacheReadTokens="0",
                cacheWriteTokens="0",
            )
        ]
        ts_call_results = [
            _row(agentRuntimeId="agt-1", bucket="2026-04-19 00:00", calls="42")
        ]
        mock_logs.get_query_results.side_effect = [
            {"status": "Complete", "results": token_results},
            {"status": "Complete", "results": call_results},
            {"status": "Complete", "results": ts_results},
            {"status": "Complete", "results": ts_call_results},
        ]

        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/costs",
            query_params={"range": "7d"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        ws = data["workspace"]
        assert ws["totalCalls"] == 42
        assert ws["totalInputTokens"] == 1_000_000
        assert ws["totalOutputTokens"] == 500_000
        assert ws["totalCostUsd"] > 0
        # Two agents — agt-1 (with usage) + agt-2 (zero-usage but still listed)
        assert len(data["agents"]) == 2
        ids = sorted(a["agentId"] for a in data["agents"])
        assert ids == ["agt-1", "agt-2"]
        # Sorted by cost desc, agt-1 should be first
        assert data["agents"][0]["agentId"] == "agt-1"
        # Timeseries has one bucket
        assert len(ws["timeseries"]) == 1
        assert ws["timeseries"][0]["calls"] == 42

    def test_skips_agents_outside_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.query.return_value = {
            "Items": [{"agentId": "agt-1", "name": "A", "model_id": "m"}],
            "LastEvaluatedKey": None,
        }
        # Logs returns spans for agt-OTHER (not in workspace) — should be skipped
        mock_logs.get_query_results.side_effect = [
            {"status": "Complete", "results": [
                _row(agentRuntimeId="agt-OTHER", model="claude-sonnet-4-6",
                     inputTokens="1000000", outputTokens="0",
                     cacheReadTokens="0", cacheWriteTokens="0"),
            ]},
            {"status": "Complete", "results": []},
            {"status": "Complete", "results": []},
            {"status": "Complete", "results": []},
        ]
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        data = json.loads(resp["body"])
        # agt-1 still listed (zero usage), agt-OTHER NOT listed
        ids = [a["agentId"] for a in data["agents"]]
        assert "agt-OTHER" not in ids
        assert data["workspace"]["totalCostUsd"] == 0.0

    def test_falls_back_to_scan_on_validation_exception(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        # query() throws ValidationException (GSI missing); should fall back to scan.
        mock_agents_table.query.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "no idx"}},
            "Query",
        )
        mock_agents_table.scan.return_value = {
            "Items": [{"agentId": "agt-1", "name": "A", "model_id": "m"}],
            "LastEvaluatedKey": None,
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 200
        # Scan was called because GSI missing
        mock_agents_table.scan.assert_called()

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_agents_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# GET /api/workspaces/{ws}/agents/{agentId}/costs
# ---------------------------------------------------------------------------


class TestAgentCosts:
    def test_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "name": "A",
                "display_name": "A",
                "model_id": "claude-sonnet-4-6",
            }
        }
        # 2 queries: tokens, calls
        mock_logs.get_query_results.side_effect = [
            {"status": "Complete", "results": [
                _row(model="claude-sonnet-4-6", inputTokens="1000000",
                     outputTokens="500000", cacheReadTokens="0",
                     cacheWriteTokens="0"),
            ]},
            {"status": "Complete", "results": [_row(calls="10")]},
        ]
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agt-1/costs",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["agentId"] == "agt-1"
        assert data["calls"] == 10
        assert data["inputTokens"] == 1_000_000
        assert data["outputTokens"] == 500_000
        assert data["costUsd"] > 0
        assert data["modelId"] == "claude-sonnet-4-6"

    def test_no_spans_returns_ddb_model(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "name": "A",
                "default_model_id": "claude-haiku-4-5",
            }
        }
        # Both queries return empty
        mock_logs.get_query_results.return_value = {"status": "Complete", "results": []}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agt-1/costs",
        ))
        data = json.loads(resp["body"])
        # Falls back to default_model_id when no spans
        assert data["modelId"] == "claude-haiku-4-5"
        assert data["costUsd"] == 0.0
        assert data["calls"] == 0

    def test_other_workspace_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": "other-ws"}
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agt-1/costs",
        ))
        assert resp["statusCode"] == 403

    def test_agent_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agt-x/costs",
        ))
        assert resp["statusCode"] == 403

    def test_invalid_agent_id(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table
    ):
        # agent id with invalid chars should be rejected
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/has spaces!/costs",
        ))
        # Invalid path → may be 400 or 404 depending on router
        assert resp["statusCode"] in (400, 404)


# ---------------------------------------------------------------------------
# GET /api/admin/costs
# ---------------------------------------------------------------------------


class TestAdminCosts:
    def test_non_admin_forbidden(self, mock_jwt):
        with patch("crud.costs.check_platform_admin", return_value=("u1", False, None)):
            resp = _invoke(_apigw("GET", "/api/admin/costs"))
        assert resp["statusCode"] == 403

    def test_admin_returns_rollup(self, mock_jwt, mock_logs):
        agents_data = {
            "agt-1": {
                "agentId": "agt-1",
                "name": "A1",
                "model_id": "claude-sonnet-4-6",
                "workspace_id": "ws-1",
                "status": "active",
            },
            "agt-2": {
                "agentId": "agt-2",
                "name": "A2",
                "model_id": "claude-haiku-4-5",
                "workspace_id": "ws-2",
                "status": "active",
            },
        }
        ws_data = {
            "ws-1": {"workspaceId": "ws-1", "name": "WS One"},
            "ws-2": {"workspaceId": "ws-2", "name": "WS Two"},
        }
        with patch("crud.costs.check_platform_admin", return_value=("u1", True, None)), \
             patch("crud.costs._all_agents_by_runtime_id", return_value=agents_data), \
             patch("crud.costs._all_workspaces_by_id", return_value=ws_data):

            mock_logs.get_query_results.side_effect = [
                {"status": "Complete", "results": [
                    _row(agentRuntimeId="agt-1", model="claude-sonnet-4-6",
                         inputTokens="1000000", outputTokens="0",
                         cacheReadTokens="0", cacheWriteTokens="0"),
                ]},
                {"status": "Complete", "results": [
                    _row(agentRuntimeId="agt-1", calls="5")
                ]},
            ]
            resp = _invoke(_apigw("GET", "/api/admin/costs"))

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        # Workspaces sorted by cost desc; both should appear
        ws_ids = [w["workspaceId"] for w in data["workspaces"]]
        assert "ws-1" in ws_ids and "ws-2" in ws_ids
        # ws-1 has the cost
        ws1 = next(w for w in data["workspaces"] if w["workspaceId"] == "ws-1")
        assert ws1["calls"] == 5
        # Totals
        assert data["totals"]["calls"] == 5
        assert data["totals"]["inputTokens"] == 1_000_000

    def test_admin_orphan_agent_synthetic_workspace(self, mock_jwt, mock_logs):
        """Agent points at a workspace that doesn't exist — synthetic entry created."""
        agents_data = {
            "agt-orphan": {
                "agentId": "agt-orphan",
                "name": "Orphan",
                "model_id": "claude-sonnet-4-6",
                "workspace_id": "ws-deleted-abcdef123456",
                "status": "active",
            },
        }
        ws_data = {}  # no workspaces
        with patch("crud.costs.check_platform_admin", return_value=("u1", True, None)), \
             patch("crud.costs._all_agents_by_runtime_id", return_value=agents_data), \
             patch("crud.costs._all_workspaces_by_id", return_value=ws_data):

            mock_logs.get_query_results.side_effect = [
                {"status": "Complete", "results": [
                    _row(agentRuntimeId="agt-orphan", model="claude-sonnet-4-6",
                         inputTokens="1000000", outputTokens="0",
                         cacheReadTokens="0", cacheWriteTokens="0"),
                ]},
                {"status": "Complete", "results": [
                    _row(agentRuntimeId="agt-orphan", calls="1")
                ]},
            ]
            resp = _invoke(_apigw("GET", "/api/admin/costs"))
        data = json.loads(resp["body"])
        # Synthetic workspace surfaced under "(deleted: ...)"
        ws_names = [w["name"] for w in data["workspaces"]]
        assert any("deleted" in n for n in ws_names)

    def test_admin_skips_unknown_agent_ids(self, mock_jwt, mock_logs):
        """Spans for agentRuntimeIds we don't have a DDB row for are skipped."""
        with patch("crud.costs.check_platform_admin", return_value=("u1", True, None)), \
             patch("crud.costs._all_agents_by_runtime_id", return_value={}), \
             patch("crud.costs._all_workspaces_by_id", return_value={}):

            mock_logs.get_query_results.side_effect = [
                {"status": "Complete", "results": [
                    _row(agentRuntimeId="ghost", model="claude-sonnet-4-6",
                         inputTokens="999", outputTokens="0",
                         cacheReadTokens="0", cacheWriteTokens="0"),
                ]},
                {"status": "Complete", "results": []},
            ]
            resp = _invoke(_apigw("GET", "/api/admin/costs"))
        data = json.loads(resp["body"])
        assert data["agents"] == []
        assert data["totals"]["calls"] == 0


# ---------------------------------------------------------------------------
# Cache scanners
# ---------------------------------------------------------------------------


class TestAgentScanners:
    def test_all_agents_by_runtime_id_paginates(self, mock_agents_table):
        from crud.costs import _all_agents_by_runtime_id
        # Two pages
        mock_agents_table.scan.side_effect = [
            {"Items": [{"agentId": "a1", "name": "A1", "model_id": "m"}],
             "LastEvaluatedKey": {"agentId": "a1"}},
            {"Items": [{"agentId": "a2", "name": "A2", "model_id": "m"}],
             "LastEvaluatedKey": None},
        ]
        result = _all_agents_by_runtime_id()
        assert "a1" in result
        assert "a2" in result

    def test_all_agents_skips_no_id(self, mock_agents_table):
        from crud.costs import _all_agents_by_runtime_id
        mock_agents_table.scan.return_value = {
            "Items": [{"name": "no-id"}, {"agentId": "a1", "name": "A1"}],
            "LastEvaluatedKey": None,
        }
        result = _all_agents_by_runtime_id()
        assert "a1" in result
        assert len(result) == 1

    def test_all_agents_clienterror_swallowed(self, mock_agents_table):
        from crud.costs import _all_agents_by_runtime_id
        mock_agents_table.scan.side_effect = ClientError(
            {"Error": {"Code": "X", "Message": "boom"}}, "Scan"
        )
        # Returns whatever was accumulated (empty dict), no raise.
        result = _all_agents_by_runtime_id()
        assert result == {}

    def test_all_agents_cache_hit(self, mock_agents_table):
        """Subsequent call within TTL returns cached value without scanning."""
        import crud.costs as cm
        cm._GLOBAL_AGENTS_CACHE["data"] = {"a": {"agentId": "a"}}
        cm._GLOBAL_AGENTS_CACHE["expires"] = float("inf")
        from crud.costs import _all_agents_by_runtime_id
        result = _all_agents_by_runtime_id()
        assert "a" in result
        mock_agents_table.scan.assert_not_called()

    def test_all_workspaces_paginates(self):
        from crud.costs import _all_workspaces_by_id
        fake_table = MagicMock()
        fake_table.scan.side_effect = [
            {
                "Items": [{"workspaceId": "ws-1", "name": "W1"}],
                "LastEvaluatedKey": {"workspaceId": "ws-1"},
            },
            {
                "Items": [{"workspaceId": "ws-2", "display_name": "W2"}],
                "LastEvaluatedKey": None,
            },
        ]
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table
        with patch("crud.costs.boto3.resource", return_value=fake_resource):
            result = _all_workspaces_by_id()
        assert result["ws-1"]["name"] == "W1"
        assert result["ws-2"]["name"] == "W2"

    def test_all_workspaces_cache_hit(self):
        import crud.costs as cm
        cm._GLOBAL_WORKSPACES_CACHE["data"] = {"ws-x": {"workspaceId": "ws-x"}}
        cm._GLOBAL_WORKSPACES_CACHE["expires"] = float("inf")
        from crud.costs import _all_workspaces_by_id
        with patch("crud.costs.boto3.resource") as mk:
            r = _all_workspaces_by_id()
            mk.assert_not_called()
        assert "ws-x" in r

    def test_all_workspaces_skips_items_without_id(self):
        from crud.costs import _all_workspaces_by_id
        fake_table = MagicMock()
        fake_table.scan.return_value = {
            "Items": [{"name": "no-id"}, {"workspaceId": "ws-1", "name": "W"}],
            "LastEvaluatedKey": None,
        }
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table
        with patch("crud.costs.boto3.resource", return_value=fake_resource):
            r = _all_workspaces_by_id()
        assert "ws-1" in r
        assert len(r) == 1

    def test_all_workspaces_clienterror_swallowed(self):
        from crud.costs import _all_workspaces_by_id
        fake_table = MagicMock()
        fake_table.scan.side_effect = ClientError(
            {"Error": {"Code": "X", "Message": "boom"}}, "Scan"
        )
        fake_resource = MagicMock()
        fake_resource.Table.return_value = fake_table
        with patch("crud.costs.boto3.resource", return_value=fake_resource):
            r = _all_workspaces_by_id()
        assert r == {}


# ---------------------------------------------------------------------------
# Lazy-init helpers
# ---------------------------------------------------------------------------


class TestLazyInit:
    def test_get_logs_caches(self):
        import crud.costs as c
        c._logs = None
        with patch("crud.costs.boto3.client") as mk:
            mk.return_value = MagicMock()
            l1 = c._get_logs()
            l2 = c._get_logs()
            assert l1 is l2
            mk.assert_called_once()
        c._logs = None

    def test_get_agents_table_caches(self):
        import crud.costs as c
        c._agents_table = None
        with patch("crud.costs.boto3.resource") as mk:
            tbl = MagicMock()
            mk.return_value.Table.return_value = tbl
            t1 = c._get_agents_table()
            t2 = c._get_agents_table()
            assert t1 is t2
        c._agents_table = None


# ---------------------------------------------------------------------------
# _agent_ids_for_workspace fallback edge cases
# ---------------------------------------------------------------------------


class TestAgentIdsForWorkspace:
    def test_non_validation_clienterror_raises(self, mock_agents_table):
        """A non-ValidationException ClientError must propagate."""
        from crud.costs import _agent_ids_for_workspace
        mock_agents_table.query.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}}, "Query"
        )
        with pytest.raises(ClientError):
            _agent_ids_for_workspace("ws-x")


# ---------------------------------------------------------------------------
# _run_query: timeout path (currently uncovered)
# ---------------------------------------------------------------------------


class TestRunQueryTimeout:
    def test_timeout_returns_partial(self, mock_logs):
        """Use timeout_s=-1 so the deadline is in the past on entry; the
        while loop never iterates, stop_query is called, and the function
        returns the (empty) last_results."""
        from crud.costs import _run_query
        mock_logs.get_query_results.return_value = {"status": "Running", "results": []}
        rows = _run_query("query", 0, 100, timeout_s=-1)
        assert rows == []
        mock_logs.stop_query.assert_called_once()

    def test_timeout_stop_query_clienterror_swallowed(self, mock_logs):
        from crud.costs import _run_query
        mock_logs.get_query_results.return_value = {"status": "Running", "results": []}
        mock_logs.stop_query.side_effect = ClientError(
            {"Error": {"Code": "X", "Message": "x"}}, "StopQuery"
        )
        rows = _run_query("query", 0, 100, timeout_s=-1)
        assert rows == []


# ---------------------------------------------------------------------------
# workspace_costs: timeseries query ClientError paths
# ---------------------------------------------------------------------------


class TestWorkspaceCostsTimeseriesErrors:
    def test_token_query_clienterror_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        """Token query raising ClientError → rows=[]; endpoint still returns."""
        mock_agents_table.query.return_value = {
            "Items": [{"agentId": "agt-1", "name": "A", "model_id": "m"}],
            "LastEvaluatedKey": None,
        }
        # 1st _run_query (per_agent_totals) raises; 2nd (calls), 3rd, 4th return empty
        call_count = {"n": 0}

        def fake_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "StartQuery")
            return []

        with patch("crud.costs._run_query", side_effect=fake_run):
            resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 200

    def test_calls_query_clienterror_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.query.return_value = {
            "Items": [{"agentId": "agt-1", "name": "A", "model_id": "m"}],
            "LastEvaluatedKey": None,
        }
        call_count = {"n": 0}

        def fake_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:  # per_agent_calls
                raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "StartQuery")
            return []

        with patch("crud.costs._run_query", side_effect=fake_run):
            resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 200

    def test_timeseries_query_clienterror_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.query.return_value = {
            "Items": [{"agentId": "agt-1", "name": "A", "model_id": "m"}],
            "LastEvaluatedKey": None,
        }
        call_count = {"n": 0}

        def fake_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] in (3, 4):  # ts and ts_calls
                raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "StartQuery")
            return []

        with patch("crud.costs._run_query", side_effect=fake_run):
            resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["workspace"]["timeseries"] == []

    def test_workspace_agent_query_clienterror_returns_500(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        """A non-ValidationException ClientError on the agents query
        cascades up and returns 500."""
        mock_agents_table.query.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
            "Query",
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 500

    def test_workspace_costs_with_call_rows_no_token_rows(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        """Call rows / ts_call_rows path covered when no token rows are emitted.
        totalCalls remains 0 because aggregation only runs over agents present
        in the per-agent token accumulator, but the call_rows + ts_call_rows
        loops still execute and increment buckets[bkt]['calls']."""
        mock_agents_table.query.return_value = {
            "Items": [{"agentId": "agt-1", "name": "A", "model_id": "m"}],
            "LastEvaluatedKey": None,
        }
        token_results = []  # empty
        call_results = [_row(agentRuntimeId="agt-1", calls="42")]
        ts_results = []
        ts_call_results = [
            _row(agentRuntimeId="agt-1", bucket="2026-04-19 00:00", calls="42")
        ]
        mock_logs.get_query_results.side_effect = [
            {"status": "Complete", "results": token_results},
            {"status": "Complete", "results": call_results},
            {"status": "Complete", "results": ts_results},
            {"status": "Complete", "results": ts_call_results},
        ]
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        # Timeseries entry created from ts_call_rows
        assert len(data["workspace"]["timeseries"]) == 1
        assert data["workspace"]["timeseries"][0]["calls"] == 42

    def test_workspace_costs_skips_unknown_runtime_in_ts_rows(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        """ts rows referencing agents not in the workspace are skipped."""
        mock_agents_table.query.return_value = {
            "Items": [{"agentId": "agt-1", "name": "A", "model_id": "m"}],
            "LastEvaluatedKey": None,
        }
        ts_results = [
            _row(agentRuntimeId="ghost", model="x", bucket="b1",
                 inputTokens="1", outputTokens="0",
                 cacheReadTokens="0", cacheWriteTokens="0"),
            _row(agentRuntimeId="agt-1", model="m", bucket="",  # empty bucket, skipped
                 inputTokens="1", outputTokens="0",
                 cacheReadTokens="0", cacheWriteTokens="0"),
        ]
        ts_call_results = [
            _row(agentRuntimeId="ghost", bucket="b1", calls="10"),
            _row(agentRuntimeId="agt-1", bucket="", calls="5"),  # empty bucket, skipped
        ]
        mock_logs.get_query_results.side_effect = [
            {"status": "Complete", "results": []},
            {"status": "Complete", "results": []},
            {"status": "Complete", "results": ts_results},
            {"status": "Complete", "results": ts_call_results},
        ]
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/costs"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        # ghost rows ignored, empty bucket rows ignored → no timeseries entries
        assert data["workspace"]["timeseries"] == []


# ---------------------------------------------------------------------------
# Admin costs: error/edge paths
# ---------------------------------------------------------------------------


class TestAdminCostsExtra:
    def test_admin_token_query_clienterror_swallowed(self, mock_jwt, mock_logs):
        """Even when the token query raises, the endpoint still answers 200."""
        with patch("crud.costs.check_platform_admin", return_value=("u", True, None)), \
             patch("crud.costs._all_agents_by_runtime_id", return_value={}), \
             patch("crud.costs._all_workspaces_by_id", return_value={}):
            call_count = {"n": 0}

            def fake_run(*args, **kwargs):
                call_count["n"] += 1
                raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "StartQuery")

            with patch("crud.costs._run_query", side_effect=fake_run):
                resp = _invoke(_apigw("GET", "/api/admin/costs"))
        assert resp["statusCode"] == 200

    def test_admin_calls_query_clienterror_swallowed(self, mock_jwt, mock_logs):
        agents = {"agt-1": {"agentId": "agt-1", "name": "n", "model_id": "m",
                              "workspace_id": "w", "status": "active"}}
        with patch("crud.costs.check_platform_admin", return_value=("u", True, None)), \
             patch("crud.costs._all_agents_by_runtime_id", return_value=agents), \
             patch("crud.costs._all_workspaces_by_id", return_value={}):
            call_count = {"n": 0}

            def fake_run(*args, **kwargs):
                call_count["n"] += 1
                if call_count["n"] == 2:
                    raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "StartQuery")
                return []

            with patch("crud.costs._run_query", side_effect=fake_run):
                resp = _invoke(_apigw("GET", "/api/admin/costs"))
        assert resp["statusCode"] == 200

    def test_admin_check_platform_admin_returns_error(self, mock_jwt):
        """When check_platform_admin returns an error response (e.g. JWT
        invalid), the route returns it directly."""
        from shared.response import forbidden
        with patch("crud.costs.check_platform_admin", return_value=(None, False, forbidden())):
            resp = _invoke(_apigw("GET", "/api/admin/costs"))
        assert resp["statusCode"] == 403

    def test_admin_skips_token_rows_without_runtime_id(self, mock_jwt, mock_logs):
        """Empty agentRuntimeId in token rows is skipped."""
        agents = {"agt-1": {"agentId": "agt-1", "name": "n", "model_id": "m",
                              "workspace_id": "w", "status": "active"}}
        ws_data = {"w": {"workspaceId": "w", "name": "W"}}
        with patch("crud.costs.check_platform_admin", return_value=("u", True, None)), \
             patch("crud.costs._all_agents_by_runtime_id", return_value=agents), \
             patch("crud.costs._all_workspaces_by_id", return_value=ws_data):
            mock_logs.get_query_results.side_effect = [
                {"status": "Complete", "results": [
                    _row(agentRuntimeId="", model="m", inputTokens="1", outputTokens="0",
                         cacheReadTokens="0", cacheWriteTokens="0"),
                ]},
                {"status": "Complete", "results": []},
            ]
            resp = _invoke(_apigw("GET", "/api/admin/costs"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["totals"]["calls"] == 0


# ---------------------------------------------------------------------------
# agent_costs: error paths
# ---------------------------------------------------------------------------


class TestAgentCostsExtra:
    def test_token_query_clienterror_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        """ClientError on _single_agent_query → rows=[]; still 200."""
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": workspace_id,
                     "model_id": "claude-sonnet-4-6"}
        }
        call_count = {"n": 0}

        def fake_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "StartQuery")
            return []

        with patch("crud.costs._run_query", side_effect=fake_run):
            resp = _invoke(_apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/agents/agt-1/costs",
            ))
        assert resp["statusCode"] == 200

    def test_calls_query_clienterror_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_agents_table, mock_logs
    ):
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": workspace_id,
                     "model_id": "claude-sonnet-4-6"}
        }
        call_count = {"n": 0}

        def fake_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise ClientError({"Error": {"Code": "X", "Message": "x"}}, "StartQuery")
            return []

        with patch("crud.costs._run_query", side_effect=fake_run):
            resp = _invoke(_apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/agents/agt-1/costs",
            ))
        assert resp["statusCode"] == 200

    def test_no_membership(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agt-1/costs",
        ))
        assert resp["statusCode"] == 403

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
    # Also reload crud.evaluations so its module-level EVALUATOR_ROLE_ARN
    # reference is fresh — earlier tests may have reloaded with an empty
    # value and left the module in that state.
    import crud.evaluations as _ev
    importlib.reload(_ev)


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


# ---------------------------------------------------------------------------
# Helper / pure function tests
# ---------------------------------------------------------------------------


def test_eval_config_name_is_deterministic_and_capped():
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.evaluations as _ev
    importlib.reload(_ev)
    n1 = _ev._eval_config_name_for_agent("ws-very-long-id-12345", "Agent-AAA-bbb-ccc-XYZ")
    n2 = _ev._eval_config_name_for_agent("ws-very-long-id-12345", "Agent-AAA-bbb-ccc-XYZ")
    assert n1 == n2
    assert len(n1) <= 48
    assert n1.startswith("agentstudio_")


def test_eval_config_name_includes_hash_to_avoid_collision():
    import crud.evaluations as _ev
    a = _ev._eval_config_name_for_agent("ws", "CustomerServiceBotV1-x")
    b = _ev._eval_config_name_for_agent("ws", "CustomerServiceBotV2-y")
    assert a != b


def test_runtime_log_group_for_agent():
    import crud.evaluations as _ev
    assert _ev._runtime_log_group_for_agent("agt-x") == "/aws/bedrock-agentcore/runtimes/agt-x-DEFAULT"


def test_field_helper_returns_value_or_none():
    import crud.evaluations as _ev
    row = [{"field": "a", "value": "1"}, {"field": "b", "value": "2"}]
    assert _ev._field(row, "a") == "1"
    assert _ev._field(row, "b") == "2"
    assert _ev._field(row, "missing") is None


def test_get_logs_lazy_init():
    """First call constructs the logs client; subsequent calls reuse cache."""
    import crud.evaluations as _ev
    _ev._logs = None
    with patch("crud.evaluations.boto3.client") as mock_b:
        mock_b.return_value = MagicMock()
        c1 = _ev._get_logs()
        c2 = _ev._get_logs()
        assert c1 is c2
        assert mock_b.call_count == 1


def test_get_control_lazy_init():
    import crud.evaluations as _ev
    _ev._control = None
    with patch("crud.evaluations.boto3.client") as mock_b:
        mock_b.return_value = MagicMock()
        c1 = _ev._get_control()
        c2 = _ev._get_control()
        assert c1 is c2


def test_get_agents_table_lazy_init():
    import crud.evaluations as _ev
    _ev._agents_table = None
    with patch("crud.evaluations.boto3.resource") as mock_r:
        mock_r.return_value.Table.return_value = MagicMock()
        t1 = _ev._get_agents_table()
        t2 = _ev._get_agents_table()
        assert t1 is t2


def test_get_agent_item_returns_item():
    import crud.evaluations as _ev
    fake_table = MagicMock()
    fake_table.get_item.return_value = {"Item": {"agentId": "agt-1"}}
    with patch("crud.evaluations._get_agents_table", return_value=fake_table):
        out = _ev._get_agent_item("agt-1")
    assert out == {"agentId": "agt-1"}


# ---------------------------------------------------------------------------
# _find_config_by_name pagination
# ---------------------------------------------------------------------------


def test_find_config_by_name_returns_match_first_page():
    import crud.evaluations as _ev
    client = MagicMock()
    client.list_online_evaluation_configs.return_value = {
        "onlineEvaluationConfigs": [
            {"onlineEvaluationConfigName": "agentstudio_ws_a", "id": "cfg-1"},
            {"onlineEvaluationConfigName": "agentstudio_ws_b", "id": "cfg-2"},
        ],
    }
    with patch("crud.evaluations._get_control", return_value=client):
        result = _ev._find_config_by_name("agentstudio_ws_b")
    assert result["id"] == "cfg-2"


def test_find_config_by_name_paginates_then_finds():
    import crud.evaluations as _ev
    client = MagicMock()
    # First page: nextToken set, no match. Second page: match. Always
    # follow with explicit None nextToken to avoid hangs.
    client.list_online_evaluation_configs.side_effect = [
        {"onlineEvaluationConfigSummaries": [{"name": "x"}], "nextToken": "tok-1"},
        {"items": [{"name": "agentstudio_target", "id": "cfg-target"}], "nextToken": None},
    ]
    with patch("crud.evaluations._get_control", return_value=client):
        result = _ev._find_config_by_name("agentstudio_target")
    assert result["id"] == "cfg-target"


def test_find_config_by_name_returns_none_when_exhausted():
    import crud.evaluations as _ev
    client = MagicMock()
    client.list_online_evaluation_configs.side_effect = [
        {"items": [{"name": "x"}], "nextToken": "t1"},
        {"items": [{"name": "y"}], "nextToken": None},
    ]
    with patch("crud.evaluations._get_control", return_value=client):
        result = _ev._find_config_by_name("does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# create_eval_config_for_agent paths
# ---------------------------------------------------------------------------


def test_create_eval_config_no_role_returns_empty_string(monkeypatch):
    """If EVALUATOR_ROLE_ARN is empty, returns ''."""
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.evaluations as _ev
    importlib.reload(_ev)
    name = _ev.create_eval_config_for_agent("ws-1", "agent-1")
    assert name == ""


def test_create_eval_config_succeeds_path():
    import crud.evaluations as _ev
    with patch("crud.evaluations._get_control") as mock_c:
        client = MagicMock()
        client.create_online_evaluation_config.return_value = {}
        mock_c.return_value = client
        name = _ev.create_eval_config_for_agent("ws-1", "agent-1")
    assert name.startswith("agentstudio_")
    assert client.create_online_evaluation_config.called


def test_create_eval_config_unknown_error_reraises():
    import crud.evaluations as _ev
    from botocore.exceptions import ClientError
    with patch("crud.evaluations._get_control") as mock_c:
        client = MagicMock()
        client.create_online_evaluation_config.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "nope"}},
            "CreateOnlineEvaluationConfig",
        )
        mock_c.return_value = client
        with pytest.raises(ClientError):
            _ev.create_eval_config_for_agent("ws-1", "agent-1")


# ---------------------------------------------------------------------------
# _ensure_runtime_log_group
# ---------------------------------------------------------------------------


def test_ensure_runtime_log_group_no_match_noops():
    import crud.evaluations as _ev
    with patch("crud.evaluations._find_config_by_name", return_value=None), \
         patch("crud.evaluations._get_control") as mock_c:
        _ev._ensure_runtime_log_group("name-x", "agt-1")
        mock_c.return_value.update_online_evaluation_config.assert_not_called()


def test_ensure_runtime_log_group_find_clienterror_swallowed():
    import crud.evaluations as _ev
    from botocore.exceptions import ClientError
    with patch("crud.evaluations._find_config_by_name") as mock_find, \
         patch("crud.evaluations._get_control") as mock_c:
        mock_find.side_effect = ClientError({"Error": {"Code": "Throttling"}}, "List")
        # Should not raise
        _ev._ensure_runtime_log_group("name", "agt-1")
        mock_c.return_value.update_online_evaluation_config.assert_not_called()


def test_ensure_runtime_log_group_no_cfg_id_noops():
    import crud.evaluations as _ev
    with patch("crud.evaluations._find_config_by_name", return_value={"name": "x"}), \
         patch("crud.evaluations._get_control") as mock_c:
        _ev._ensure_runtime_log_group("name-x", "agt-1")
        mock_c.return_value.update_online_evaluation_config.assert_not_called()


def test_ensure_runtime_log_group_get_clienterror_swallowed():
    import crud.evaluations as _ev
    from botocore.exceptions import ClientError
    client = MagicMock()
    client.get_online_evaluation_config.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "GetOnlineEvaluationConfig"
    )
    with patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-1"}), \
         patch("crud.evaluations._get_control", return_value=client):
        _ev._ensure_runtime_log_group("name-x", "agt-1")
        client.update_online_evaluation_config.assert_not_called()


def test_ensure_runtime_log_group_already_present_noops():
    import crud.evaluations as _ev
    client = MagicMock()
    client.get_online_evaluation_config.return_value = {
        "dataSourceConfig": {
            "cloudWatchLogs": {
                "logGroupNames": ["aws/spans", "/aws/bedrock-agentcore/runtimes/agt-1-DEFAULT"],
                "serviceNames": ["agt-1"],
            },
        }
    }
    with patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-1"}), \
         patch("crud.evaluations._get_control", return_value=client):
        _ev._ensure_runtime_log_group("name-x", "agt-1")
        client.update_online_evaluation_config.assert_not_called()


def test_ensure_runtime_log_group_backfill_path():
    import crud.evaluations as _ev
    client = MagicMock()
    client.get_online_evaluation_config.return_value = {
        "dataSourceConfig": {"cloudWatchLogs": {"logGroupNames": ["aws/spans"]}}
    }
    with patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-1"}), \
         patch("crud.evaluations._get_control", return_value=client):
        _ev._ensure_runtime_log_group("name-x", "agt-1")
    client.update_online_evaluation_config.assert_called_once()


def test_ensure_runtime_log_group_update_clienterror_logged():
    import crud.evaluations as _ev
    from botocore.exceptions import ClientError
    client = MagicMock()
    client.get_online_evaluation_config.return_value = {
        "dataSourceConfig": {"cloudWatchLogs": {"logGroupNames": []}}
    }
    client.update_online_evaluation_config.side_effect = ClientError(
        {"Error": {"Code": "Throttling"}}, "UpdateOnlineEvaluationConfig"
    )
    with patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-1"}), \
         patch("crud.evaluations._get_control", return_value=client):
        # Should not raise
        _ev._ensure_runtime_log_group("name-x", "agt-1")


# ---------------------------------------------------------------------------
# delete_eval_config_for_agent
# ---------------------------------------------------------------------------


def test_delete_eval_config_no_role_short_circuits(monkeypatch):
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.evaluations as _ev
    importlib.reload(_ev)
    with patch("crud.evaluations._find_config_by_name") as f:
        _ev.delete_eval_config_for_agent("ws", "agt")
        f.assert_not_called()


def test_delete_eval_config_missing_noops():
    import crud.evaluations as _ev
    with patch("crud.evaluations._find_config_by_name", return_value=None), \
         patch("crud.evaluations._get_control") as mock_c:
        _ev.delete_eval_config_for_agent("ws", "agt")
        mock_c.return_value.delete_online_evaluation_config.assert_not_called()


def test_delete_eval_config_calls_delete():
    import crud.evaluations as _ev
    client = MagicMock()
    with patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-1"}), \
         patch("crud.evaluations._get_control", return_value=client):
        _ev.delete_eval_config_for_agent("ws", "agt")
    client.delete_online_evaluation_config.assert_called_once_with(onlineEvaluationConfigId="cfg-1")


def test_delete_eval_config_clienterror_swallowed():
    import crud.evaluations as _ev
    from botocore.exceptions import ClientError
    client = MagicMock()
    client.delete_online_evaluation_config.side_effect = ClientError(
        {"Error": {"Code": "Throttling"}}, "DeleteOnlineEvaluationConfig"
    )
    with patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-1"}), \
         patch("crud.evaluations._get_control", return_value=client):
        # Must not raise
        _ev.delete_eval_config_for_agent("ws", "agt")


def test_sync_eval_config_calls_create():
    import crud.evaluations as _ev
    with patch("crud.evaluations.create_eval_config_for_agent", return_value="x") as mock_create:
        _ev.sync_eval_config_for_agent("ws", "agt")
    mock_create.assert_called_once_with("ws", "agt")


# ---------------------------------------------------------------------------
# create_eval_config_for_workspace fan-out
# ---------------------------------------------------------------------------


def test_create_eval_config_for_workspace_fans_out():
    import crud.evaluations as _ev
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [
            {"agentId": "a1", "status": "active"},
            {"agentId": "a2", "status": "active"},
            {"agentId": "a3", "status": "archived"},  # skipped
            {"agentId": "", "status": "active"},        # skipped: no id
        ],
    }
    with patch("crud.evaluations._get_agents_table", return_value=fake_table), \
         patch("crud.evaluations.create_eval_config_for_agent", side_effect=["n1", "n2"]) as mock_create:
        result = _ev.create_eval_config_for_workspace("ws-1")
    assert "n1" in result and "n2" in result
    assert mock_create.call_count == 2


def test_create_eval_config_for_workspace_clienterror_returns_empty():
    import crud.evaluations as _ev
    from botocore.exceptions import ClientError
    fake_table = MagicMock()
    fake_table.query.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException"}}, "Query"
    )
    with patch("crud.evaluations._get_agents_table", return_value=fake_table):
        result = _ev.create_eval_config_for_workspace("ws-1")
    assert result == ""


# ---------------------------------------------------------------------------
# _run_logs_query
# ---------------------------------------------------------------------------


def test_run_logs_query_empty_log_groups_returns_empty():
    import crud.evaluations as _ev
    assert _ev._run_logs_query([], "fields @timestamp", 0, 100) == []


def test_run_logs_query_complete_returns_results():
    import crud.evaluations as _ev
    fake = MagicMock()
    fake.start_query.return_value = {"queryId": "q-1"}
    fake.get_query_results.return_value = {"status": "Complete", "results": [["x"]]}
    with patch("crud.evaluations._get_logs", return_value=fake):
        out = _ev._run_logs_query(["lg-1"], "q", 0, 100)
    assert out == [["x"]]


def test_run_logs_query_failed_status_returns_empty():
    import crud.evaluations as _ev
    fake = MagicMock()
    fake.start_query.return_value = {"queryId": "q-1"}
    fake.get_query_results.return_value = {"status": "Failed"}
    with patch("crud.evaluations._get_logs", return_value=fake):
        out = _ev._run_logs_query(["lg-1"], "q", 0, 100)
    assert out == []


def test_run_logs_query_cancelled_status_returns_empty():
    import crud.evaluations as _ev
    fake = MagicMock()
    fake.start_query.return_value = {"queryId": "q-1"}
    fake.get_query_results.return_value = {"status": "Cancelled"}
    with patch("crud.evaluations._get_logs", return_value=fake):
        out = _ev._run_logs_query(["lg-1"], "q", 0, 100)
    assert out == []


def test_run_logs_query_timeout_attempts_stop_query():
    import crud.evaluations as _ev
    fake = MagicMock()
    fake.start_query.return_value = {"queryId": "q-1"}
    # Always return Running so the deadline elapses.
    fake.get_query_results.return_value = {"status": "Running"}
    with patch("crud.evaluations._get_logs", return_value=fake), \
         patch("crud.evaluations.time.time") as mock_time, \
         patch("crud.evaluations.time.sleep"):
        # First call: now. Loop checks "while time.time() < deadline" so
        # second call is past. Then stop_query runs.
        mock_time.side_effect = [0, 100]
        out = _ev._run_logs_query(["lg-1"], "q", 0, 100, timeout_s=15)
    assert out == []
    fake.stop_query.assert_called_once_with(queryId="q-1")


def test_run_logs_query_timeout_stop_query_clienterror_swallowed():
    import crud.evaluations as _ev
    from botocore.exceptions import ClientError
    fake = MagicMock()
    fake.start_query.return_value = {"queryId": "q-1"}
    fake.get_query_results.return_value = {"status": "Running"}
    fake.stop_query.side_effect = ClientError({"Error": {"Code": "x"}}, "StopQuery")
    with patch("crud.evaluations._get_logs", return_value=fake), \
         patch("crud.evaluations.time.time") as mock_time, \
         patch("crud.evaluations.time.sleep"):
        mock_time.side_effect = [0, 100]
        out = _ev._run_logs_query(["lg-1"], "q", 0, 100, timeout_s=15)
    assert out == []


# ---------------------------------------------------------------------------
# get_agent_evaluations error / edge paths
# ---------------------------------------------------------------------------


def test_get_agent_evaluations_invalid_agent_id_400(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    bad_event = _agent_eval_event(workspace_id, "bad$id")
    bad_event["pathParameters"] = {"wsId": workspace_id, "agentId": "bad$id"}
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(bad_event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_agent_evaluations_auth_err_returns_err(mock_jwt, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_get_agent_evaluations_no_config_returns_empty(mock_jwt, user_id, workspace_id):
    """If no per-agent config exists yet, returns empty (not 500)."""
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value=None):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["evaluations"] == []


def test_get_agent_evaluations_no_cfg_id_returns_empty(mock_jwt, user_id, workspace_id):
    """Config with neither id nor onlineEvaluationConfigId returns []."""
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value={"foo": "bar"}):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["evaluations"] == []


def test_get_agent_evaluations_describe_log_groups_clienterror_continues(mock_jwt, user_id, workspace_id):
    """describe_log_groups failure is logged but query still runs."""
    from crud.handler import app
    from botocore.exceptions import ClientError
    fake_logs = MagicMock()
    fake_logs.describe_log_groups.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "DescribeLogGroups"
    )
    fake_logs.start_query.return_value = {"queryId": "q-1"}
    fake_logs.get_query_results.return_value = {"status": "Complete", "results": []}
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-x"}), \
         patch("crud.evaluations._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200


def test_get_agent_evaluations_query_clienterror_500(mock_jwt, user_id, workspace_id):
    """ClientError from start_query bubbles to internal_error path."""
    from crud.handler import app
    from botocore.exceptions import ClientError
    fake_logs = MagicMock()
    fake_logs.describe_log_groups.return_value = {"logGroups": []}
    fake_logs.start_query.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "StartQuery"
    )
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-x"}), \
         patch("crud.evaluations._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 500


def test_get_agent_evaluations_filters_invalid_score_and_missing_evaluator(mock_jwt, user_id, workspace_id):
    """Rows with no evaluator, non-numeric score, or null score are dropped."""
    from crud.handler import app
    fake_logs = MagicMock()
    fake_logs.describe_log_groups.return_value = {"logGroups": []}
    fake_logs.start_query.return_value = {"queryId": "q-1"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            # Valid
            [
                {"field": "@timestamp", "value": "2026-04-19 00:01:00.000"},
                {"field": "evaluator", "value": "Builtin.Correctness"},
                {"field": "score", "value": "0.85"},
            ],
            # Score is unparseable
            [
                {"field": "evaluator", "value": "Builtin.Correctness"},
                {"field": "score", "value": "not-a-number"},
            ],
            # Missing evaluator
            [
                {"field": "score", "value": "0.5"},
            ],
            # Missing score (None)
            [
                {"field": "evaluator", "value": "Builtin.Helpfulness"},
            ],
        ],
    }
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-x"}), \
         patch("crud.evaluations._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    data = json.loads(resp["body"])
    assert len(data["evaluations"]) == 1
    assert data["evaluations"][0]["score"] == 0.85


def test_get_agent_evaluations_all_errors_diagnostics(mock_jwt, user_id, workspace_id):
    """When all rows have errorType and none parse, surface diagnostics."""
    from crud.handler import app
    fake_logs = MagicMock()
    fake_logs.describe_log_groups.return_value = {"logGroups": []}
    fake_logs.start_query.return_value = {"queryId": "q-1"}
    fake_logs.get_query_results.return_value = {
        "status": "Complete",
        "results": [
            [
                {"field": "errorType", "value": "AgentSpanMappingException"},
                {"field": "evaluator", "value": "Builtin.Correctness"},
            ],
            [
                {"field": "errorType", "value": "AgentSpanMappingException"},
            ],
        ],
    }
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-x"}), \
         patch("crud.evaluations._get_logs", return_value=fake_logs):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_agent_eval_event(workspace_id), MagicMock())
    data = json.loads(resp["body"])
    assert data["evaluations"] == []
    assert data["diagnostics"]["allFailed"] is True
    assert data["diagnostics"]["errorCount"] == 2


# ---------------------------------------------------------------------------
# enable_agent_evaluations
# ---------------------------------------------------------------------------


def _enable_event(workspace_id, agent_id="agt-test"):
    return {
        "httpMethod": "POST",
        "path": f"/api/workspaces/{workspace_id}/agents/{agent_id}/evaluations/enable",
        "resource": "/api/workspaces/{wsId}/agents/{agentId}/evaluations/enable",
        "pathParameters": {"wsId": workspace_id, "agentId": agent_id},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_enable_agent_evaluations_auth_err(mock_jwt, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_enable_agent_evaluations_invalid_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    bad = _enable_event(workspace_id, agent_id="bad$id")
    bad["pathParameters"] = {"wsId": workspace_id, "agentId": "bad$id"}
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(bad, MagicMock())
    assert resp["statusCode"] == 400


def test_enable_agent_evaluations_agent_not_in_ws(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other"}
        resp = app.resolve(_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_enable_agent_evaluations_no_role_arn_500(mock_jwt, user_id, workspace_id, monkeypatch):
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.evaluations as _ev
    importlib.reload(_ev)
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 500


def test_enable_agent_evaluations_already_exists(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value={"id": "cfg-1"}), \
         patch("crud.evaluations.create_eval_config_for_agent") as mock_create:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "ALREADY_EXISTS"
    mock_create.assert_not_called()


def test_enable_agent_evaluations_creates_when_missing(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value=None), \
         patch("crud.evaluations.create_eval_config_for_agent", return_value="name-x"):
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "CREATED"


def test_enable_agent_evaluations_clienterror_500(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from botocore.exceptions import ClientError
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name") as f:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        f.side_effect = ClientError({"Error": {"Code": "ServiceUnavailable"}}, "List")
        resp = app.resolve(_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# get_agent_evaluations_status
# ---------------------------------------------------------------------------


def _status_event(workspace_id, agent_id="agt-test"):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{workspace_id}/agents/{agent_id}/evaluations/status",
        "resource": "/api/workspaces/{wsId}/agents/{agentId}/evaluations/status",
        "pathParameters": {"wsId": workspace_id, "agentId": agent_id},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_status_auth_err(mock_jwt, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_status_invalid_id(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    bad = _status_event(workspace_id, agent_id="bad$id")
    bad["pathParameters"] = {"wsId": workspace_id, "agentId": "bad$id"}
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(bad, MagicMock())
    assert resp["statusCode"] == 400


def test_status_agent_not_in_ws(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other"}
        resp = app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_status_returns_not_exists_when_missing(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value=None):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["exists"] is False
    assert body["status"] is None


def test_status_returns_active(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name", return_value={
             "id": "cfg-1", "status": "ACTIVE", "executionStatus": "ENABLED",
         }):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        resp = app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["exists"] is True
    assert body["status"] == "ACTIVE"
    assert body["executionStatus"] == "ENABLED"


def test_status_clienterror_500(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from botocore.exceptions import ClientError
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agent_item") as ga, \
         patch("crud.evaluations._find_config_by_name") as f:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        f.side_effect = ClientError({"Error": {"Code": "Throttling"}}, "List")
        resp = app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# Workspace-level legacy endpoints
# ---------------------------------------------------------------------------


def _ws_enable_event(workspace_id):
    return {
        "httpMethod": "POST",
        "path": f"/api/workspaces/{workspace_id}/evaluations/enable",
        "resource": "/api/workspaces/{wsId}/evaluations/enable",
        "pathParameters": {"wsId": workspace_id},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def _ws_status_event(workspace_id):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{workspace_id}/evaluations/status",
        "resource": "/api/workspaces/{wsId}/evaluations/status",
        "pathParameters": {"wsId": workspace_id},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_ws_enable_auth_err(mock_jwt, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_ws_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_ws_enable_no_role_500(mock_jwt, user_id, workspace_id, monkeypatch):
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.evaluations as _ev
    importlib.reload(_ev)
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(_ws_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 500


def test_ws_enable_creates(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations.create_eval_config_for_workspace", return_value="n1,n2"):
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(_ws_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "CREATED"


def test_ws_enable_noop_when_no_agents(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations.create_eval_config_for_workspace", return_value=""):
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(_ws_enable_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["status"] == "NOOP"


def test_ws_status_auth_err(mock_jwt, workspace_id):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.evaluations.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(_ws_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 403


def test_ws_status_query_clienterror_returns_empty(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    from botocore.exceptions import ClientError
    fake_table = MagicMock()
    fake_table.query.side_effect = ClientError(
        {"Error": {"Code": "Throttling"}}, "Query"
    )
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agents_table", return_value=fake_table):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_ws_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["exists"] is False
    assert body["status"] is None


def test_ws_status_with_agents_returns_active(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [
            {"agentId": "a1", "status": "active"},
            {"agentId": "a2", "status": "archived"},  # skipped
            {"agentId": "", "status": "active"},        # skipped
        ],
    }
    # Match returned with ACTIVE status
    fake_match = {"id": "cfg-1", "status": "ACTIVE"}
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agents_table", return_value=fake_table), \
         patch("crud.evaluations._find_config_by_name", return_value=fake_match):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_ws_status_event(workspace_id), MagicMock())
    body = json.loads(resp["body"])
    assert body["exists"] is True
    assert body["status"] == "ACTIVE"
    assert body["executionStatus"] == "ENABLED"


def test_ws_status_creating_when_only_inactive(mock_jwt, user_id, workspace_id):
    """At least one config exists but none active → CREATING."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [{"agentId": "a1", "status": "active"}],
    }
    fake_match = {"id": "cfg-1", "status": "CREATING"}
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agents_table", return_value=fake_table), \
         patch("crud.evaluations._find_config_by_name", return_value=fake_match):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_ws_status_event(workspace_id), MagicMock())
    body = json.loads(resp["body"])
    assert body["exists"] is True
    assert body["status"] == "CREATING"
    assert body["executionStatus"] is None


def test_ws_status_no_configs(mock_jwt, user_id, workspace_id):
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.query.return_value = {
        "Items": [{"agentId": "a1", "status": "active"}],
    }
    with patch("crud.evaluations.auth_check") as auth, \
         patch("crud.evaluations._get_agents_table", return_value=fake_table), \
         patch("crud.evaluations._find_config_by_name", return_value=None):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_ws_status_event(workspace_id), MagicMock())
    body = json.loads(resp["body"])
    assert body["exists"] is False
    assert body["status"] is None

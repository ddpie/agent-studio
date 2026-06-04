"""Tests for crud.schedules module."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_validate_cron_accepts_cron():
    from crud.schedules import _validate_cron
    assert _validate_cron("cron(0 12 * * ? *)") is None


def test_validate_cron_accepts_rate():
    from crud.schedules import _validate_cron
    assert _validate_cron("rate(5 minutes)") is None
    assert _validate_cron("rate(1 hour)") is None
    assert _validate_cron("rate(2 days)") is None


def test_validate_cron_rejects_empty():
    from crud.schedules import _validate_cron
    err = _validate_cron("")
    assert err is not None
    assert "required" in err


def test_validate_cron_rejects_too_long():
    from crud.schedules import _validate_cron
    assert _validate_cron("cron(" + "a" * 300 + ")") is not None


def test_validate_cron_rejects_at_expression():
    """at(...) is intentionally excluded because UI is cron-focused."""
    from crud.schedules import _validate_cron
    err = _validate_cron("at(2026-01-01T00:00:00)")
    assert err is not None
    assert "cron" in err.lower() or "rate" in err.lower()


def test_validate_cron_rejects_garbage():
    from crud.schedules import _validate_cron
    assert _validate_cron("not-a-cron") is not None
    assert _validate_cron("cron(") is not None
    assert _validate_cron("rate(5 weeks)") is not None  # weeks not allowed


def test_validate_suffix_accepts_simple():
    from crud.schedules import _validate_suffix
    assert _validate_suffix("daily-report") is None
    assert _validate_suffix("MyReport_v2") is None


def test_validate_suffix_rejects_empty():
    from crud.schedules import _validate_suffix
    assert _validate_suffix("") is not None


def test_validate_suffix_rejects_too_long():
    from crud.schedules import _validate_suffix, _MAX_SUFFIX_LEN
    assert _validate_suffix("a" * (_MAX_SUFFIX_LEN + 1)) is not None


def test_validate_suffix_rejects_special_chars():
    from crud.schedules import _validate_suffix
    assert _validate_suffix("has spaces") is not None
    assert _validate_suffix("dot.notallowed") is not None
    assert _validate_suffix("plus+notallowed") is not None


def test_name_prefix_format():
    from crud.schedules import _name_prefix, _build_full_name
    assert _name_prefix("agent1") == "agent-studio-agent1-"
    assert _build_full_name("agent1", "daily") == "agent-studio-agent1-daily"


def test_extract_prompt_well_formed():
    from crud.schedules import _extract_prompt
    payload = json.dumps({"prompt": "hello"})
    sched = {
        "Target": {
            "Input": json.dumps({"Payload": payload})
        }
    }
    assert _extract_prompt(sched) == "hello"


def test_extract_prompt_missing_input_returns_empty():
    from crud.schedules import _extract_prompt
    assert _extract_prompt({"Target": {}}) == ""
    assert _extract_prompt({}) == ""


def test_extract_prompt_malformed_json_returns_empty():
    from crud.schedules import _extract_prompt
    sched = {"Target": {"Input": "not-json"}}
    assert _extract_prompt(sched) == ""


def test_extract_prompt_inner_payload_malformed():
    from crud.schedules import _extract_prompt
    sched = {"Target": {"Input": json.dumps({"Payload": "{not-json"})}}
    assert _extract_prompt(sched) == ""


def test_status_from_code_buckets():
    from crud.schedules import _status_from_code
    assert _status_from_code(None) == "success"
    assert _status_from_code("") == "success"
    assert _status_from_code("OK") == "success"
    assert _status_from_code("ERROR") == "failure"
    assert _status_from_code("FAILED") == "failure"
    assert _status_from_code("FAILURE") == "failure"
    assert _status_from_code("UNSET") == "running"
    assert _status_from_code("RUNNING") == "running"
    assert _status_from_code("error") == "failure"  # case-insensitive


def test_field_returns_value():
    from crud.schedules import _field
    row = [{"field": "x", "value": "1"}, {"field": "y", "value": "2"}]
    assert _field(row, "x") == "1"
    assert _field(row, "y") == "2"
    assert _field(row, "z") is None


def test_iso_handles_none():
    from crud.schedules import _iso
    assert _iso(None) == ""


def test_iso_handles_naive_datetime():
    from crud.schedules import _iso
    naive = datetime(2026, 1, 1, 12, 0, 0)
    out = _iso(naive)
    # Should append UTC offset
    assert "2026-01-01T12:00:00" in out
    assert "+00:00" in out or out.endswith("Z")


def test_iso_handles_aware_datetime():
    from crud.schedules import _iso
    aware = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    out = _iso(aware)
    assert "2026-01-01T12:00:00" in out


def test_iso_falls_back_to_str():
    from crud.schedules import _iso
    # Object without isoformat
    assert _iso("plain-string") == "plain-string"


def test_schedule_response_shape():
    from crud.schedules import _schedule_response
    sched = {
        "Name": "agent-studio-a1-daily",
        "ScheduleExpression": "cron(0 12 * * ? *)",
        "State": "ENABLED",
        "Arn": "arn:aws:scheduler:us-east-1:123:schedule/default/agent-studio-a1-daily",
        "GroupName": "default",
        "Target": {
            "Input": json.dumps({
                "Payload": json.dumps({"prompt": "do work"})
            })
        }
    }
    out = _schedule_response("a1", sched)
    assert out["name"] == "agent-studio-a1-daily"
    assert out["suffix"] == "daily"
    assert out["cron"] == "cron(0 12 * * ? *)"
    assert out["state"] == "ENABLED"
    assert out["prompt"] == "do work"


def test_schedule_response_unknown_name_keeps_name_as_suffix():
    from crud.schedules import _schedule_response
    sched = {"Name": "stranger-name", "ScheduleExpression": "rate(1 hour)"}
    out = _schedule_response("a1", sched)
    # Doesn't start with prefix → suffix is the full name
    assert out["suffix"] == "stranger-name"


def test_agent_arn_format():
    from crud.schedules import _agent_arn
    out = _agent_arn("agent1")
    assert out.startswith("arn:aws:bedrock-agentcore:")
    assert out.endswith(":runtime/agent1")


# ---------------------------------------------------------------------------
# Handler tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "test-origin")


@pytest.fixture
def mock_scheduler():
    with patch("crud.schedules._get_scheduler") as g:
        s = MagicMock()
        # Default paginator returns a single empty page
        paginator = MagicMock()
        paginator.paginate.return_value = iter([{"Schedules": []}])
        s.get_paginator.return_value = paginator
        g.return_value = s
        yield s


@pytest.fixture
def mock_agents_table_for_sched():
    with patch("crud.schedules._get_agents_table") as g:
        t = MagicMock()
        t.get_item.return_value = {"Item": None}
        g.return_value = t
        yield t


@pytest.fixture
def mock_logs():
    with patch("crud.schedules._get_logs") as g:
        c = MagicMock()
        c.start_query.return_value = {"queryId": "qid-1"}
        c.get_query_results.return_value = {"status": "Complete", "results": []}
        g.return_value = c
        yield c


@pytest.fixture
def configure_scheduler_env(monkeypatch):
    """Patch the module-level constants the handlers read at call time."""
    import crud.schedules as s
    monkeypatch.setattr(s, "_SCHEDULER_TARGET_ROLE_ARN", "arn:aws:iam::123:role/scheduler")
    monkeypatch.setattr(s, "_SCHEDULE_RUNNER_LAMBDA_ARN", "arn:aws:lambda:us-east-1:123:function:runner")
    yield


@pytest.fixture
def _mock_editor(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "editor",
        "joined_at": datetime.utcnow().isoformat() + "Z",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "viewer",
        "joined_at": datetime.utcnow().isoformat() + "Z",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    with patch("shared.middleware.get_membership", return_value=None):
        yield


def _apigw(method, path, body=None, query_params=None):
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
            "x-origin-verify": "test-origin",
            "Content-Type": "application/json",
        },
        "body": json.dumps(body) if body else None,
        "queryStringParameters": query_params or {},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


def _agent_in_ws(ws_id, **kw):
    """Build a fake agent record. Note: parameter is 'ws_id' (not 'workspace_id')
    so callers can pass workspace_id as a keyword override."""
    item = {
        "agentId": "agent1",
        "workspace_id": ws_id,
        "status": "active",
        "name": "X",
    }
    item.update(kw)
    return item


# ---------------------------------------------------------------------------
# LIST SCHEDULES
# ---------------------------------------------------------------------------


class TestListSchedules:
    def test_list_empty(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/schedules"
        ))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["schedules"] == []

    def test_list_invalid_id(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/has spaces/schedules"
        ))
        assert resp["statusCode"] == 400

    def test_list_agent_not_found(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/schedules"
        ))
        assert resp["statusCode"] == 403

    def test_list_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership,
        mock_agents_table_for_sched, mock_scheduler
    ):
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/schedules"
        ))
        assert resp["statusCode"] == 403

    def test_list_other_workspace_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {
            "Item": _agent_in_ws("other-ws")
        }
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/schedules"
        ))
        assert resp["statusCode"] == 403

    def test_list_returns_schedules_filters_by_prefix(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        # paginator yields one page with two schedules — one matches prefix,
        # one is from a different agent (shouldn't be listed).
        paginator = MagicMock()
        paginator.paginate.return_value = iter([{
            "Schedules": [
                {"Name": "agent-studio-agent1-daily",
                 "ScheduleExpression": "cron(0 12 * * ? *)",
                 "State": "ENABLED"},
                {"Name": "agent-studio-other-foo",
                 "ScheduleExpression": "rate(1 hour)",
                 "State": "ENABLED"},
            ]
        }])
        mock_scheduler.get_paginator.return_value = paginator
        # get_schedule re-fetch returns one with full target / prompt
        mock_scheduler.get_schedule.return_value = {
            "Name": "agent-studio-agent1-daily",
            "ScheduleExpression": "cron(0 12 * * ? *)",
            "State": "ENABLED",
            "Target": {"Input": json.dumps({"Payload": json.dumps({"prompt": "hi"})})},
        }
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/schedules"
        ))
        assert resp["statusCode"] == 200
        schedules = json.loads(resp["body"])["schedules"]
        assert len(schedules) == 1
        assert schedules[0]["suffix"] == "daily"
        assert schedules[0]["prompt"] == "hi"

    def test_list_get_schedule_failure_falls_back(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        paginator = MagicMock()
        paginator.paginate.return_value = iter([{
            "Schedules": [
                {"Name": "agent-studio-agent1-x",
                 "ScheduleExpression": "rate(1 hour)",
                 "State": "ENABLED"},
            ]
        }])
        mock_scheduler.get_paginator.return_value = paginator
        mock_scheduler.get_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSchedule"
        )

        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/schedules"
        ))
        assert resp["statusCode"] == 200
        schedules = json.loads(resp["body"])["schedules"]
        assert len(schedules) == 1
        # Falls back to the list-row response with empty prompt
        assert schedules[0]["prompt"] == ""

    def test_list_paginator_error_returns_500(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        paginator = MagicMock()
        paginator.paginate.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError"}}, "ListSchedules"
        )
        mock_scheduler.get_paginator.return_value = paginator
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/agents/agent1/schedules"
        ))
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# CREATE SCHEDULE
# ---------------------------------------------------------------------------


class TestCreateSchedule:
    def _body(self, **kw):
        body = {"name": "daily", "cron": "cron(0 12 * * ? *)", "prompt": "hello"}
        body.update(kw)
        return body

    def test_create_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.create_schedule.return_value = {
            "ScheduleArn": "arn:aws:scheduler:us-east-1:123:schedule/default/agent-studio-agent1-daily"
        }
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(),
        ))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["name"] == "agent-studio-agent1-daily"
        assert data["suffix"] == "daily"
        assert data["cron"] == "cron(0 12 * * ? *)"

    def test_create_accepts_full_name(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        """Passing the full prefixed name should be normalised."""
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.create_schedule.return_value = {"ScheduleArn": "arn:x"}
        body = self._body(name="agent-studio-agent1-daily")
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules", body=body,
        ))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["suffix"] == "daily"

    def test_create_missing_name(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        body = self._body()
        del body["name"]
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules", body=body,
        ))
        assert resp["statusCode"] == 400

    def test_create_invalid_suffix(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(name="has spaces"),
        ))
        assert resp["statusCode"] == 400

    def test_create_invalid_cron(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(cron="garbage"),
        ))
        assert resp["statusCode"] == 400

    def test_create_missing_prompt(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(prompt="   "),
        ))
        assert resp["statusCode"] == 400
        assert "prompt" in json.loads(resp["body"])["error"]

    def test_create_prompt_too_long(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(prompt="a" * 4001),
        ))
        assert resp["statusCode"] == 400

    def test_create_prompt_not_string(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(prompt=123),
        ))
        assert resp["statusCode"] == 400

    def test_create_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(),
        ))
        assert resp["statusCode"] == 403

    def test_create_archived_agent_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {
            "Item": _agent_in_ws(workspace_id, status="archived")
        }
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(),
        ))
        assert resp["statusCode"] == 403

    def test_create_no_role_arn_returns_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, monkeypatch
    ):
        import crud.schedules as s
        monkeypatch.setattr(s, "_SCHEDULER_TARGET_ROLE_ARN", "")
        monkeypatch.setattr(s, "_SCHEDULE_RUNNER_LAMBDA_ARN", "arn:x")
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(),
        ))
        assert resp["statusCode"] == 500

    def test_create_conflict_exception(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.create_schedule.side_effect = ClientError(
            {"Error": {"Code": "ConflictException", "Message": "exists"}},
            "CreateSchedule",
        )
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(),
        ))
        assert resp["statusCode"] == 400
        assert "exists" in json.loads(resp["body"])["error"].lower()

    def test_create_validation_exception(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.create_schedule.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad cron"}},
            "CreateSchedule",
        )
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(),
        ))
        assert resp["statusCode"] == 400

    def test_create_other_client_error_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.create_schedule.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "CreateSchedule",
        )
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/agents/agent1/schedules",
            body=self._body(),
        ))
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# UPDATE SCHEDULE
# ---------------------------------------------------------------------------


class TestUpdateSchedule:
    def _existing_schedule(self):
        return {
            "Name": "agent-studio-agent1-daily",
            "ScheduleExpression": "cron(0 12 * * ? *)",
            "State": "ENABLED",
            "FlexibleTimeWindow": {"Mode": "OFF"},
            "Description": "Existing",
            "Target": {
                "Arn": "arn:lambda:runner",
                "RoleArn": "arn:iam:role",
                "Input": json.dumps({
                    "AgentRuntimeArn": "arn:agent",
                    "RuntimeSessionId": "sess-1",
                    "Payload": json.dumps({
                        "prompt": "old",
                        "__schedule_name": "agent-studio-agent1-daily",
                        "session_id": "sess-1",
                        "workspace_id": "ws-x",
                    }),
                }),
            },
        }

    def test_update_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/has spaces/schedules/x",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 400

    def test_update_agent_not_found_403(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 403

    def test_update_archived_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {
            "Item": _agent_in_ws(workspace_id, status="archived")
        }
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 403

    def test_update_name_missing_prefix(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/schedules/wrong-prefix",
            body={"prompt": "x"},
        ))
        assert resp["statusCode"] == 400
        assert "must start with" in json.loads(resp["body"])["error"]

    def test_update_name_invalid_charset(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        # Name starts with valid prefix but has bad chars; URL-encoded-ish name
        # passed via path. We can't inject spaces (URL parsing) but use $
        # which fails the regex.
        bad_name = "agent-studio-agent1-bad$char"
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/schedules/{bad_name}",
            body={"prompt": "x"},
        ))
        assert resp["statusCode"] == 400

    def test_update_no_fields_provided(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={},
        ))
        assert resp["statusCode"] == 400
        assert "at least one" in json.loads(resp["body"])["error"]

    def test_update_invalid_cron(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"cron": "not-cron"},
        ))
        assert resp["statusCode"] == 400

    def test_update_cron_not_string(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"cron": 123},
        ))
        assert resp["statusCode"] == 400

    def test_update_invalid_state(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"state": "PAUSED"},
        ))
        assert resp["statusCode"] == 400

    def test_update_prompt_empty(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "  "},
        ))
        assert resp["statusCode"] == 400

    def test_update_prompt_too_long(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "a" * 4001},
        ))
        assert resp["statusCode"] == 400

    def test_update_get_schedule_not_found_404(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSchedule"
        )
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 404

    def test_update_get_schedule_other_error_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError"}}, "GetSchedule"
        )
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 500

    def test_update_prompt_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        # First get returns existing; update succeeds; second get returns refreshed
        mock_scheduler.get_schedule.side_effect = [
            self._existing_schedule(),
            {**self._existing_schedule(),
             "Target": {**self._existing_schedule()["Target"],
                        "Input": json.dumps({
                            "Payload": json.dumps({"prompt": "new"}),
                        })}},
        ]
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["prompt"] == "new"
        mock_scheduler.update_schedule.assert_called_once()

    def test_update_cron_and_state(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.side_effect = [
            self._existing_schedule(),
            {**self._existing_schedule(), "ScheduleExpression": "rate(5 minutes)", "State": "DISABLED"},
        ]
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"cron": "rate(5 minutes)", "state": "DISABLED"},
        ))
        assert resp["statusCode"] == 200

    def test_update_existing_payload_malformed(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        """Malformed existing Input shouldn't crash the update."""
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        existing = self._existing_schedule()
        existing["Target"]["Input"] = "not-json"
        mock_scheduler.get_schedule.side_effect = [existing, existing]
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 200

    def test_update_schedule_validation_error(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.return_value = self._existing_schedule()
        mock_scheduler.update_schedule.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad input"}},
            "UpdateSchedule",
        )
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 400

    def test_update_schedule_not_found(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.return_value = self._existing_schedule()
        mock_scheduler.update_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "UpdateSchedule"
        )
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 404

    def test_update_other_client_error_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.return_value = self._existing_schedule()
        mock_scheduler.update_schedule.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError"}}, "UpdateSchedule"
        )
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 500

    def test_update_refresh_get_failure_falls_back(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        """Post-update refresh fetch failing returns success based on local state."""
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.side_effect = [
            self._existing_schedule(),  # initial get
            ClientError({"Error": {"Code": "Throttle"}}, "GetSchedule"),  # refresh fails
        ]
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "new"},
        ))
        assert resp["statusCode"] == 200

    def test_update_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        resp = _invoke(_apigw(
            "PUT",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
            body={"prompt": "x"},
        ))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# DELETE SCHEDULE
# ---------------------------------------------------------------------------


class TestDeleteSchedule:
    def test_delete_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["deleted"] == "agent-studio-agent1-daily"
        mock_scheduler.delete_schedule.assert_called_once()

    def test_delete_invalid_agent_id(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler
    ):
        resp = _invoke(_apigw(
            "DELETE", f"/api/workspaces/{workspace_id}/agents/has spaces/schedules/x",
        ))
        assert resp["statusCode"] == 400

    def test_delete_agent_not_found(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-x",
        ))
        assert resp["statusCode"] == 403

    def test_delete_name_missing_prefix(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler
    ):
        """Path-injection guard: schedule name without proper prefix is rejected
        even if the agent exists."""
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/some-other-schedule",
        ))
        assert resp["statusCode"] == 400
        mock_scheduler.delete_schedule.assert_not_called()

    def test_delete_invalid_charset(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        bad_name = "agent-studio-agent1-bad$char"
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/{bad_name}",
        ))
        assert resp["statusCode"] == 400

    def test_delete_not_found(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.delete_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DeleteSchedule"
        )
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
        ))
        assert resp["statusCode"] == 404

    def test_delete_other_error_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.delete_schedule.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError"}}, "DeleteSchedule"
        )
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
        ))
        assert resp["statusCode"] == 500

    def test_delete_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_scheduler
    ):
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily",
        ))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# RUN-NOW
# ---------------------------------------------------------------------------


class TestRunNow:
    def _existing_with_prompt(self):
        return {
            "Name": "agent-studio-agent1-daily",
            "ScheduleExpression": "cron(0 12 * * ? *)",
            "Target": {
                "Input": json.dumps({
                    "Payload": json.dumps({"prompt": "do thing"}),
                })
            },
        }

    def test_run_now_success(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.return_value = self._existing_with_prompt()
        mock_scheduler.create_schedule.return_value = {"ScheduleArn": "arn:x"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 202
        data = json.loads(resp["body"])
        assert data["sessionId"].startswith("sched-daily-manual-")
        assert "scheduledFor" in data
        assert "oneShotName" in data

    def test_run_now_invalid_id(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/has spaces/schedules/x/run-now",
        ))
        assert resp["statusCode"] == 400

    def test_run_now_archived_forbidden(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {
            "Item": _agent_in_ws(workspace_id, status="archived")
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 403

    def test_run_now_missing_prefix(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/wrong-prefix/run-now",
        ))
        assert resp["statusCode"] == 400

    def test_run_now_no_role_arn_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, monkeypatch
    ):
        import crud.schedules as s
        monkeypatch.setattr(s, "_SCHEDULER_TARGET_ROLE_ARN", "")
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 500

    def test_run_now_get_schedule_not_found_404(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSchedule"
        )
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 404

    def test_run_now_get_schedule_other_error_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError"}}, "GetSchedule"
        )
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 500

    def test_run_now_no_prompt_400(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        # Existing schedule with empty prompt
        mock_scheduler.get_schedule.return_value = {
            "Name": "agent-studio-agent1-daily",
            "Target": {"Input": json.dumps({"Payload": json.dumps({"prompt": ""})})},
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 400
        assert "no prompt" in json.loads(resp["body"])["error"]

    def test_run_now_create_conflict(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.return_value = self._existing_with_prompt()
        mock_scheduler.create_schedule.side_effect = ClientError(
            {"Error": {"Code": "ConflictException", "Message": "already"}},
            "CreateSchedule",
        )
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 400

    def test_run_now_create_other_error_500(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.return_value = self._existing_with_prompt()
        mock_scheduler.create_schedule.side_effect = ClientError(
            {"Error": {"Code": "InternalServerError", "Message": "boom"}},
            "CreateSchedule",
        )
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 500

    def test_run_now_existing_input_malformed(
        self, workspace_id, mock_jwt, _mock_editor,
        mock_agents_table_for_sched, mock_scheduler, configure_scheduler_env
    ):
        """Malformed Input → empty inner → 400 because no prompt to run."""
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_scheduler.get_schedule.return_value = {
            "Name": "agent-studio-agent1-daily",
            "Target": {"Input": "not-json"},
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/run-now",
        ))
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# LIST EXECUTIONS
# ---------------------------------------------------------------------------


class TestListExecutions:
    def test_invalid_id(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/has spaces/schedules/x/executions",
        ))
        assert resp["statusCode"] == 400

    def test_agent_not_found(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 403

    def test_missing_prefix(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/wrong-prefix/executions",
        ))
        assert resp["statusCode"] == 400

    def test_invalid_charset(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-bad$char/executions",
        ))
        assert resp["statusCode"] == 400

    def test_executions_empty(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        # primary returns []; fallback also returns []
        mock_logs.get_query_results.return_value = {"status": "Complete", "results": []}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["executions"] == []
        assert data["scheduleName"] == "agent-studio-agent1-daily"

    def test_executions_returns_rows(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        # First (primary) call returns one row
        rows = [[
            {"field": "sessionId", "value": "sched-daily-2026-04-19T10:00:00"},
            {"field": "firstStartNs", "value": "1700000000000000000"},
            {"field": "lastEndNs", "value": "1700000005000000000"},
            {"field": "worstStatus", "value": "OK"},
        ]]
        mock_logs.get_query_results.return_value = {"status": "Complete", "results": rows}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        executions = data["executions"]
        assert len(executions) == 1
        ex = executions[0]
        assert ex["sessionId"] == "sched-daily-2026-04-19T10:00:00"
        assert ex["scheduledTime"] == "2026-04-19T10:00:00"
        assert ex["durationMs"] == 5000
        assert ex["status"] == "success"

    def test_executions_dedupes_by_session_id(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        rows = [
            [{"field": "sessionId", "value": "sched-daily-T1"},
             {"field": "firstStartNs", "value": "1000000"},
             {"field": "lastEndNs", "value": "2000000"},
             {"field": "worstStatus", "value": "OK"}],
            [{"field": "sessionId", "value": "sched-daily-T1"},  # dup
             {"field": "firstStartNs", "value": "3000000"},
             {"field": "lastEndNs", "value": "4000000"},
             {"field": "worstStatus", "value": "OK"}],
        ]
        mock_logs.get_query_results.return_value = {"status": "Complete", "results": rows}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        executions = json.loads(resp["body"])["executions"]
        assert len(executions) == 1

    def test_executions_skips_rows_without_sid(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        rows = [
            [{"field": "firstStartNs", "value": "1000000"}],  # no sessionId
        ]
        mock_logs.get_query_results.return_value = {"status": "Complete", "results": rows}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["executions"] == []

    def test_executions_query_failed_returns_empty(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_logs.get_query_results.return_value = {"status": "Failed"}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        # Both queries return Failed, so executions is []
        assert json.loads(resp["body"])["executions"] == []

    def test_executions_start_query_clienterror_returns_empty(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_logs.start_query.side_effect = ClientError(
            {"Error": {"Code": "InvalidParameterException"}}, "StartQuery"
        )
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["executions"] == []

    def test_executions_get_results_clienterror_returns_empty(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        mock_logs.get_query_results.side_effect = ClientError(
            {"Error": {"Code": "Throttling"}}, "GetQueryResults"
        )
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["executions"] == []

    def test_executions_failure_status_buckets_failure(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        rows = [[
            {"field": "sessionId", "value": "sched-daily-T1"},
            {"field": "firstStartNs", "value": "1000000"},
            {"field": "lastEndNs", "value": "2000000"},
            {"field": "worstStatus", "value": "ERROR"},
        ]]
        mock_logs.get_query_results.return_value = {"status": "Complete", "results": rows}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        execs = json.loads(resp["body"])["executions"]
        assert execs[0]["status"] == "failure"

    def test_executions_falls_back_when_primary_empty(
        self, workspace_id, mock_jwt, _mock_viewer,
        mock_agents_table_for_sched, mock_logs
    ):
        """If primary query returns no rows, fallback runs and may return rows
        with sessionId not matching session_prefix."""
        mock_agents_table_for_sched.get_item.return_value = {"Item": _agent_in_ws(workspace_id)}
        # First call (primary) → empty; second call (fallback) → one row
        first = {"status": "Complete", "results": []}
        second_rows = [[
            {"field": "sessionId", "value": "stranger-id"},
            {"field": "firstStartNs", "value": "1000000"},
            {"field": "lastEndNs", "value": "2000000"},
            {"field": "worstStatus", "value": "OK"},
        ]]
        second = {"status": "Complete", "results": second_rows}
        mock_logs.get_query_results.side_effect = [first, second]
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/agents/agent1/schedules/agent-studio-agent1-daily/executions",
        ))
        assert resp["statusCode"] == 200
        execs = json.loads(resp["body"])["executions"]
        assert len(execs) == 1
        # Fallback rows have non-matching sid → scheduledTime is empty
        assert execs[0]["scheduledTime"] == ""

"""Tests for crud/runtime.py — AgentCore Control Plane passthrough."""
import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def aws_event_factory(user_id, workspace_id):
    def _build(path, method="GET", path_params=None, body=None):
        return {
            "httpMethod": method,
            "path": path,
            "resource": path.replace(
                workspace_id, "{wsId}"
            ).replace("agt-test", "{agentId}"),
            "pathParameters": path_params or {"wsId": workspace_id, "agentId": "agt-test"},
            "headers": {"Authorization": "Bearer test-token"},
            "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
            "body": json.dumps(body) if body else None,
            "isBase64Encoded": False,
            "queryStringParameters": None,
        }
    return _build


def test_get_runtime_returns_filtered_fields(mock_jwt, user_id, workspace_id, aws_event_factory):
    """GET /agents/{id}/runtime strips sensitive fields."""
    from crud.handler import app
    with patch("crud.runtime._get_control") as mock_control_factory, \
         patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {
            "agentId": "agt-test",
            "workspace_id": workspace_id,
            "created_by": user_id,
            "status": "active",
        }
        control = MagicMock()
        # Use real datetimes — boto3 returns naive datetime objects, not
        # strings. If _strip_sensitive / response helpers don't coerce,
        # json.dumps blows up with "Object of type datetime is not JSON
        # serializable" and the /runtime endpoint 500s in production. (Hit
        # live on 2026-04-18.)
        last_updated = dt.datetime(2026, 4, 18, 0, 0, 0)
        control.get_agent_runtime.return_value = {
            "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/agt-test",
            "agentRuntimeId": "agt-test",
            "agentRuntimeName": "TestAgent",
            "status": "READY",
            "lastUpdatedAt": last_updated,
            "createdAt": last_updated,
            "description": "desc",
            "executionRoleArn": "arn:aws:iam::123:role/secret",
            "agentRuntimeArtifact": {"code": {"s3": {"bucket": "b", "prefix": "p"}}},
            "agentRuntimeVersion": "4",
        }
        mock_control_factory.return_value = control

        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["status"] == "READY"
    assert data["agentRuntimeVersion"] == "4"
    # datetime fields must be ISO strings with UTC tz so browsers parse
    # them as UTC (not local) and render correctly in user's timezone.
    assert data["lastUpdatedAt"] == "2026-04-18T00:00:00+00:00"
    assert data["createdAt"] == "2026-04-18T00:00:00+00:00"
    assert "agentRuntimeArn" not in data
    assert "executionRoleArn" not in data
    assert "agentRuntimeArtifact" not in data


def test_get_runtime_returns_404_when_runtime_missing(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as mock_control_factory, \
         patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {
            "agentId": "agt-test",
            "workspace_id": workspace_id,
            "status": "active",
        }
        control = MagicMock()
        control.get_agent_runtime.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "not found"}},
            "GetAgentRuntime",
        )
        mock_control_factory.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 404


def test_get_runtime_denies_cross_workspace(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {
            "agentId": "agt-test",
            "workspace_id": "OTHER_WS",
            "status": "active",
        }
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 403


def test_list_versions_returns_sorted(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_control") as mock_control_factory, \
         patch("crud.runtime._get_agent_item") as mock_get_agent, \
         patch("crud.runtime.auth_check") as auth_mock:
        auth_mock.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        mock_get_agent.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_versions.return_value = {
            "agentRuntimes": [
                {"agentRuntimeVersion": "3", "status": "READY", "lastUpdatedAt": "2026-04-18T00:00:00Z"},
                {"agentRuntimeVersion": "2", "status": "READY", "lastUpdatedAt": "2026-04-17T00:00:00Z"},
                {"agentRuntimeVersion": "1", "status": "READY", "lastUpdatedAt": "2026-04-16T00:00:00Z"},
            ]
        }
        mock_control_factory.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/versions")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    versions = data.get("versions") if isinstance(data, dict) else data
    assert [v["agentRuntimeVersion"] for v in versions] == ["3", "2", "1"]
    assert "executionRoleArn" not in versions[0]


def test_list_endpoints(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_endpoints.return_value = {
            "runtimeEndpoints": [
                {"name": "DEFAULT", "liveVersion": "4", "status": "READY",
                 "createdAt": "2026-04-01", "lastUpdatedAt": "2026-04-18"},
                {"name": "staging", "liveVersion": "3", "targetVersion": None, "status": "READY",
                 "createdAt": "2026-04-10", "lastUpdatedAt": "2026-04-10"},
            ]
        }
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    ep = data.get("endpoints", data)
    names = [e["name"] for e in ep]
    assert "DEFAULT" in names and "staging" in names


def test_create_endpoint_requires_editor(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "3"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_update_endpoint_not_found_version(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.update_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Agent version 99 does not exist"}},
            "UpdateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={"version": "99"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404


# ---------------------------------------------------------------------------
# Lazy-init helpers + _to_json_safe + _strip_sensitive
# ---------------------------------------------------------------------------


def test_get_control_lazy_init():
    import crud.runtime as _rt
    _rt._control = None
    with patch("crud.runtime.boto3.client") as mock_b:
        mock_b.return_value = MagicMock()
        c1 = _rt._get_control()
        c2 = _rt._get_control()
        assert c1 is c2
        assert mock_b.call_count == 1


def test_get_agents_table_lazy_init():
    import crud.runtime as _rt
    _rt._agents_table = None
    with patch("crud.runtime.boto3.resource") as mock_r:
        mock_r.return_value.Table.return_value = MagicMock()
        t1 = _rt._get_agents_table()
        t2 = _rt._get_agents_table()
        assert t1 is t2


def test_get_agent_item_returns_item():
    import crud.runtime as _rt
    fake = MagicMock()
    fake.get_item.return_value = {"Item": {"agentId": "a"}}
    with patch("crud.runtime._get_agents_table", return_value=fake):
        out = _rt._get_agent_item("a")
    assert out == {"agentId": "a"}


def test_get_agent_item_missing_returns_none():
    import crud.runtime as _rt
    fake = MagicMock()
    fake.get_item.return_value = {}
    with patch("crud.runtime._get_agents_table", return_value=fake):
        assert _rt._get_agent_item("a") is None


def test_to_json_safe_aware_datetime_kept():
    import crud.runtime as _rt
    aware = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert _rt._to_json_safe(aware) == "2026-01-01T12:00:00+00:00"


def test_to_json_safe_naive_datetime_treated_as_utc():
    import crud.runtime as _rt
    naive = dt.datetime(2026, 1, 1, 12, 0)
    assert _rt._to_json_safe(naive).endswith("+00:00")


def test_to_json_safe_date_returns_iso():
    import crud.runtime as _rt
    d = dt.date(2026, 1, 1)
    assert _rt._to_json_safe(d) == "2026-01-01"


def test_to_json_safe_bytes_decoded():
    import crud.runtime as _rt
    assert _rt._to_json_safe(b"hi") == "hi"
    # Bytes with invalid utf-8 use replace
    assert _rt._to_json_safe(b"\xff\xfe") is not None


def test_to_json_safe_recurses_dict_list_tuple():
    import crud.runtime as _rt
    inp = {
        "d": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
        "l": [dt.date(2026, 1, 1), b"abc"],
        "t": (1, 2, 3),
    }
    out = _rt._to_json_safe(inp)
    assert out["d"].endswith("+00:00")
    assert out["l"] == ["2026-01-01", "abc"]
    assert out["t"] == [1, 2, 3]


def test_to_json_safe_passthrough_primitives():
    import crud.runtime as _rt
    assert _rt._to_json_safe("hello") == "hello"
    assert _rt._to_json_safe(123) == 123
    assert _rt._to_json_safe(None) is None


def test_strip_sensitive_filters_known_fields():
    import crud.runtime as _rt
    raw = {
        "status": "READY",
        "agentRuntimeArn": "arn:secret",
        "executionRoleArn": "arn:role",
        "agentRuntimeArtifact": {},
        "networkConfiguration": {},
        "protocolConfiguration": {},
        "filesystemConfigurations": [],
        "roleArn": "arn:secret",
        "ResponseMetadata": {},
        "agentRuntimeId": "a",
    }
    out = _rt._strip_sensitive(raw)
    assert "status" in out
    assert "agentRuntimeId" in out
    for f in [
        "agentRuntimeArn", "executionRoleArn", "agentRuntimeArtifact",
        "networkConfiguration", "protocolConfiguration", "filesystemConfigurations",
        "roleArn", "ResponseMetadata",
    ]:
        assert f not in out


# ---------------------------------------------------------------------------
# get_runtime additional paths
# ---------------------------------------------------------------------------


def test_get_runtime_auth_err(mock_jwt, workspace_id, aws_event_factory):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_runtime_invalid_agent_id_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/bad$id/runtime",
            path_params={"wsId": workspace_id, "agentId": "bad$id"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_runtime_agent_missing_returns_403(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item", return_value=None), \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_get_runtime_harness_branch(mock_jwt, user_id, workspace_id, aws_event_factory):
    """For runtime_type=harness, response is mapped to harness shape."""
    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {
            "agentId": "agt-test",
            "workspace_id": workspace_id,
            "runtime_type": "harness",
        }
        control = MagicMock()
        control.get_harness.return_value = {
            "harness": {
                "harnessId": "agt-test",
                "arn": "arn:aws:bedrock-agentcore:us-east-1:123:harness/agt-test",
                "status": "READY",
                "createdAt": dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                "updatedAt": dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
            }
        }
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["status"] == "READY"
    assert data["runtime_type"] == "harness"
    assert "agentRuntimeArn" not in data  # stripped


def test_get_runtime_other_clienterror_500(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.get_agent_runtime.side_effect = ClientError(
            {"Error": {"Code": "Throttling", "Message": "slow"}},
            "GetAgentRuntime",
        )
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/runtime")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# list_versions additional paths
# ---------------------------------------------------------------------------


def test_list_versions_auth_err(mock_jwt, workspace_id, aws_event_factory):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/versions")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_list_versions_invalid_agent_id_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/bad$id/versions",
            path_params={"wsId": workspace_id, "agentId": "bad$id"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_list_versions_cross_workspace_403(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other"}
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/versions")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_list_versions_runtime_not_found_404(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_versions.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "ListAgentRuntimeVersions",
        )
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/versions")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404


def test_list_versions_other_clienterror_500(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_versions.side_effect = ClientError(
            {"Error": {"Code": "Throttling"}},
            "ListAgentRuntimeVersions",
        )
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/versions")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_list_versions_unparseable_version_falls_to_zero(mock_jwt, user_id, workspace_id, aws_event_factory):
    """Versions that don't parse as int sort as 0 (no exception)."""
    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_versions.return_value = {
            "agentRuntimes": [
                {"agentRuntimeVersion": "abc", "status": "READY"},
                {"agentRuntimeVersion": "2", "status": "READY"},
                {"agentRuntimeVersion": None, "status": "READY"},
            ]
        }
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/versions")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    versions = json.loads(resp["body"])["versions"]
    # "2" parses → 2, "abc"/None → 0. Sorted reverse: 2, 0, 0
    assert versions[0]["agentRuntimeVersion"] == "2"


# ---------------------------------------------------------------------------
# list_endpoints additional paths
# ---------------------------------------------------------------------------


def test_list_endpoints_auth_err(mock_jwt, workspace_id, aws_event_factory):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_list_endpoints_invalid_id(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/bad$id/endpoints",
            path_params={"wsId": workspace_id, "agentId": "bad$id"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_list_endpoints_cross_workspace(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other"}
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_list_endpoints_runtime_not_found_404(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_endpoints.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "ListAgentRuntimeEndpoints",
        )
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404


def test_list_endpoints_other_clienterror_500(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.list_agent_runtime_endpoints.side_effect = ClientError(
            {"Error": {"Code": "Throttling"}},
            "ListAgentRuntimeEndpoints",
        )
        f.return_value = control
        event = aws_event_factory(f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints")
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# create_endpoint
# ---------------------------------------------------------------------------


def test_create_endpoint_invalid_id(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/bad$id/endpoints",
            method="POST",
            path_params={"wsId": workspace_id, "agentId": "bad$id"},
            body={"name": "staging", "version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_create_endpoint_cross_workspace(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other"}
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_create_endpoint_missing_name_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_create_endpoint_missing_version_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_create_endpoint_default_name_rejected(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "DEFAULT", "version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_create_endpoint_succeeds_202(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.create_agent_runtime_endpoint.return_value = {
            "name": "staging",
            "status": "CREATING",
            "agentRuntimeArn": "arn:secret",  # to verify stripping
        }
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 202
    data = json.loads(resp["body"])
    assert data["name"] == "staging"
    assert "agentRuntimeArn" not in data


def test_create_endpoint_validation_clienterror_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.create_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "ValidationException", "Message": "bad version"}},
            "CreateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "999"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_create_endpoint_conflict_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.create_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "ConflictException", "Message": "exists"}},
            "CreateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_create_endpoint_runtime_not_found_404(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.create_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "CreateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404


def test_create_endpoint_other_clienterror_500(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.create_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "InternalFailure"}},
            "CreateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints",
            method="POST",
            body={"name": "staging", "version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# update_endpoint
# ---------------------------------------------------------------------------


def test_update_endpoint_auth_err(mock_jwt, workspace_id, aws_event_factory):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={"version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_update_endpoint_invalid_id(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/bad$id/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "bad$id", "endpointName": "staging"},
            body={"version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_update_endpoint_cross_workspace(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other"}
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={"version": "1"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_update_endpoint_missing_version_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_update_endpoint_succeeds_202(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.update_agent_runtime_endpoint.return_value = {
            "name": "staging",
            "status": "UPDATING",
        }
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={"version": "2"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 202


def test_update_endpoint_validation_clienterror_400(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.update_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "ConflictException", "Message": "in progress"}},
            "UpdateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={"version": "2"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_update_endpoint_other_clienterror_500(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.update_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "Throttling"}},
            "UpdateAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="PUT",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
            body={"version": "2"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# delete_endpoint
# ---------------------------------------------------------------------------


def test_delete_endpoint_auth_err(mock_jwt, workspace_id, aws_event_factory):
    from crud.handler import app
    from shared.response import forbidden
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="DELETE",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_delete_endpoint_invalid_id(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/bad$id/endpoints/staging",
            method="DELETE",
            path_params={"wsId": workspace_id, "agentId": "bad$id", "endpointName": "staging"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_delete_endpoint_default_name_rejected(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/DEFAULT",
            method="DELETE",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "DEFAULT"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_delete_endpoint_cross_workspace(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": "other"}
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="DELETE",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 403


def test_delete_endpoint_succeeds(mock_jwt, user_id, workspace_id, aws_event_factory):
    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.delete_agent_runtime_endpoint.return_value = {}
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="DELETE",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["deleted"] == "staging"


def test_delete_endpoint_not_found_404(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.delete_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "DeleteAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="DELETE",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 404


def test_delete_endpoint_other_clienterror_500(mock_jwt, user_id, workspace_id, aws_event_factory):
    from botocore.exceptions import ClientError

    from crud.handler import app
    with patch("crud.runtime._get_control") as f, \
         patch("crud.runtime._get_agent_item") as ga, \
         patch("crud.runtime.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        ga.return_value = {"agentId": "agt-test", "workspace_id": workspace_id}
        control = MagicMock()
        control.delete_agent_runtime_endpoint.side_effect = ClientError(
            {"Error": {"Code": "Throttling"}},
            "DeleteAgentRuntimeEndpoint",
        )
        f.return_value = control
        event = aws_event_factory(
            f"/api/workspaces/{workspace_id}/agents/agt-test/endpoints/staging",
            method="DELETE",
            path_params={"wsId": workspace_id, "agentId": "agt-test", "endpointName": "staging"},
        )
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500

"""Tests for crud.mcp — MCP target discovery + workspace policy."""
import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_control():
    with patch("crud.mcp._get_control") as g:
        c = MagicMock()
        c.list_agent_runtimes.return_value = {"agentRuntimes": [], "nextToken": None}
        g.return_value = c
        yield c


@pytest.fixture
def mock_ws_table():
    with patch("crud.mcp._get_ws_table") as g:
        t = MagicMock()
        t.name = "test-ws"
        t.get_item.return_value = {"Item": {}}
        t.update_item.return_value = {}
        g.return_value = t
        yield t


@pytest.fixture(autouse=True)
def reset_caches():
    """Reset all module-level caches between tests."""
    import crud.mcp as mcp_mod
    mcp_mod._registry_cache["data"] = None
    mcp_mod._registry_cache["expires"] = 0
    mcp_mod._tool_manifests.clear()
    mcp_mod._tool_manifests_ttl.clear()
    yield


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
def _mock_admin(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "admin",
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
        "headers": {"Authorization": "Bearer tok", "Content-Type": "application/json"},
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query_params or {},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestRuntimeNameCandidates:
    def test_basic(self):
        from crud.mcp import _runtime_name_candidates
        out = _runtime_name_candidates("aws-pricing")
        assert "aws_pricing" in out
        assert "mcp_aws_pricing" in out

    def test_no_hyphen(self):
        from crud.mcp import _runtime_name_candidates
        out = _runtime_name_candidates("perplexity")
        assert out == {"perplexity", "mcp_perplexity"}


class TestIsAvailableInRegion:
    def test_no_availability_means_everywhere(self):
        from crud.mcp import _is_available_in_region
        assert _is_available_in_region({"name": "x"}) is True
        assert _is_available_in_region({"name": "x", "availability": []}) is True

    def test_match(self):
        from crud.mcp import _is_available_in_region
        assert _is_available_in_region({"availability": ["us-east-1"]}) is True

    def test_no_match(self):
        from crud.mcp import _is_available_in_region
        assert _is_available_in_region({"availability": ["eu-west-2"]}) is False


class TestFilterByPolicy:
    def test_all(self):
        from crud.mcp import _filter_by_policy
        targets = [{"name": "a"}, {"name": "b"}]
        assert _filter_by_policy(targets, {"mode": "all"}) == targets

    def test_allowlist(self):
        from crud.mcp import _filter_by_policy
        targets = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        out = _filter_by_policy(targets, {"mode": "allowlist", "allowedTargets": ["a", "c"]})
        assert {t["name"] for t in out} == {"a", "c"}

    def test_denylist(self):
        from crud.mcp import _filter_by_policy
        targets = [{"name": "a"}, {"name": "b"}, {"name": "c"}]
        out = _filter_by_policy(targets, {"mode": "denylist", "deniedTargets": ["b"]})
        assert {t["name"] for t in out} == {"a", "c"}

    def test_unknown_mode_returns_all(self):
        from crud.mcp import _filter_by_policy
        targets = [{"name": "a"}]
        assert _filter_by_policy(targets, {"mode": "weird"}) == targets


# ---------------------------------------------------------------------------
# Registry / runtime listing
# ---------------------------------------------------------------------------


class TestLoadRegistry:
    def test_loads_yaml_from_s3(self):
        with patch("crud.mcp.boto3.client") as bc:
            s3 = MagicMock()
            yaml_bytes = (
                b"remote_targets:\n"
                b"  - name: ctx7\n    enabled: true\n    description: 'Context7'\n"
                b"runtime_targets:\n"
                b"  - name: aws-pricing\n    enabled: true\n"
            )
            body = MagicMock()
            body.read.return_value = yaml_bytes
            s3.get_object.return_value = {"Body": body}
            bc.return_value = s3
            from crud.mcp import _load_registry
            data = _load_registry()
        assert "remote_targets" in data
        assert "runtime_targets" in data
        assert data["remote_targets"][0]["name"] == "ctx7"

    def test_failure_returns_empty(self):
        with patch("crud.mcp.boto3.client") as bc:
            s3 = MagicMock()
            s3.get_object.side_effect = Exception("nope")
            bc.return_value = s3
            from crud.mcp import _load_registry
            data = _load_registry()
        assert data == {"remote_targets": [], "runtime_targets": []}


class TestListDeployedRuntimes:
    def test_paginates(self, mock_control):
        mock_control.list_agent_runtimes.side_effect = [
            {
                "agentRuntimes": [
                    {"agentRuntimeName": "mcp_a", "status": "READY"},
                ],
                "nextToken": "tok-2",
            },
            {
                "agentRuntimes": [
                    {"agentRuntimeName": "mcp_b", "status": "CREATING"},
                ],
            },
        ]
        from crud.mcp import _list_deployed_runtimes
        out = _list_deployed_runtimes()
        assert "mcp_a" in out
        assert "mcp_b" in out
        assert mock_control.list_agent_runtimes.call_count == 2

    def test_failure_returns_empty(self, mock_control):
        mock_control.list_agent_runtimes.side_effect = Exception("boom")
        from crud.mcp import _list_deployed_runtimes
        assert _list_deployed_runtimes() == {}


# ---------------------------------------------------------------------------
# Build target list
# ---------------------------------------------------------------------------


class TestBuildTargetList:
    def test_skips_disabled_and_deprecated(self, mock_control):
        registry = {
            "remote_targets": [
                {"name": "r1", "enabled": True, "description": "d1"},
                {"name": "r2", "enabled": False, "description": "d2"},
                {"name": "r3", "enabled": True, "deprecated": True},
            ],
            "runtime_targets": [
                {"name": "rt1", "enabled": True},
            ],
        }
        with patch("crud.mcp._load_registry", return_value=registry):
            from crud.mcp import _build_target_list
            out = _build_target_list()
        names = [t["name"] for t in out]
        assert "r1" in names
        assert "r2" not in names
        assert "r3" not in names
        assert "rt1" in names

    def test_runtime_status_from_deployed(self, mock_control):
        registry = {
            "remote_targets": [],
            "runtime_targets": [
                {"name": "aws-pricing", "enabled": True, "category": "infra"},
                {"name": "ghost", "enabled": True},
            ],
        }
        mock_control.list_agent_runtimes.return_value = {
            "agentRuntimes": [
                {"agentRuntimeName": "mcp_aws_pricing", "status": "READY"},
            ],
        }
        with patch("crud.mcp._load_registry", return_value=registry):
            from crud.mcp import _build_target_list
            out = _build_target_list()
        by_name = {t["name"]: t for t in out}
        assert by_name["aws-pricing"]["status"] == "READY"
        assert by_name["ghost"]["status"] == "unavailable"

    def test_remote_always_ready(self, mock_control):
        registry = {
            "remote_targets": [
                {"name": "ctx7", "enabled": True, "description": "d", "category": "rag"},
            ],
            "runtime_targets": [],
        }
        with patch("crud.mcp._load_registry", return_value=registry):
            from crud.mcp import _build_target_list
            out = _build_target_list()
        assert out[0]["status"] == "READY"
        assert out[0]["type"] == "remote"


# ---------------------------------------------------------------------------
# GET /api/workspaces/{ws}/mcp/policy
# ---------------------------------------------------------------------------


class TestGetMcpPolicy:
    def test_default_policy(
        self, workspace_id, mock_jwt, _mock_editor, mock_ws_table
    ):
        # No mcpPolicy → default {"mode": "all", ...}
        mock_ws_table.get_item.return_value = {"Item": {}}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/mcp/policy"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["policy"]["mode"] == "all"

    def test_existing_policy_returned(
        self, workspace_id, mock_jwt, _mock_editor, mock_ws_table
    ):
        mock_ws_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "mcpPolicy": {
                    "mode": "allowlist",
                    "allowedTargets": ["aws-pricing"],
                    "deniedTargets": [],
                },
            }
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/mcp/policy"))
        data = json.loads(resp["body"])
        assert data["policy"]["mode"] == "allowlist"
        assert data["policy"]["allowedTargets"] == ["aws-pricing"]

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_ws_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/mcp/policy"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# PUT /api/workspaces/{ws}/mcp/policy  (admin only)
# ---------------------------------------------------------------------------


class TestUpdateMcpPolicy:
    def test_admin_updates(self, workspace_id, mock_jwt, _mock_admin, mock_ws_table):
        body = {"policy": {"mode": "allowlist", "allowedTargets": ["aws-pricing"]}}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/mcp/policy", body=body
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["policy"]["mode"] == "allowlist"
        # Update was called
        mock_ws_table.update_item.assert_called_once()

    def test_editor_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_ws_table
    ):
        body = {"policy": {"mode": "all"}}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/mcp/policy", body=body
        ))
        assert resp["statusCode"] == 403

    def test_invalid_mode(
        self, workspace_id, mock_jwt, _mock_admin, mock_ws_table
    ):
        body = {"policy": {"mode": "invalid"}}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/mcp/policy", body=body
        ))
        assert resp["statusCode"] == 400
        assert "mode" in json.loads(resp["body"])["error"]

    def test_missing_lists_added(
        self, workspace_id, mock_jwt, _mock_admin, mock_ws_table
    ):
        body = {"policy": {"mode": "all"}}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/mcp/policy", body=body
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["policy"]["allowedTargets"] == []
        assert data["policy"]["deniedTargets"] == []

    def test_ddb_failure(self, workspace_id, mock_jwt, _mock_admin, mock_ws_table):
        mock_ws_table.update_item.side_effect = Exception("ddb boom")
        body = {"policy": {"mode": "all"}}
        resp = _invoke(_apigw(
            "PUT", f"/api/workspaces/{workspace_id}/mcp/policy", body=body
        ))
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# GET /api/workspaces/{ws}/mcp/targets
# ---------------------------------------------------------------------------


class TestListAvailableTargets:
    def test_filtered_for_member(
        self, workspace_id, mock_jwt, _mock_editor, mock_ws_table
    ):
        targets = [
            {"name": "a", "type": "remote", "status": "READY"},
            {"name": "b", "type": "remote", "status": "READY"},
        ]
        with patch("crud.mcp._build_target_list", return_value=targets):
            mock_ws_table.get_item.return_value = {
                "Item": {"mcpPolicy": {"mode": "allowlist", "allowedTargets": ["a"]}}
            }
            resp = _invoke(_apigw(
                "GET", f"/api/workspaces/{workspace_id}/mcp/targets"
            ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        names = [t["name"] for t in data["items"]]
        assert names == ["a"]

    def test_show_all_admin_only(
        self, workspace_id, mock_jwt, _mock_admin, mock_ws_table
    ):
        targets = [{"name": "a"}, {"name": "b"}]
        with patch("crud.mcp._build_target_list", return_value=targets):
            mock_ws_table.get_item.return_value = {
                "Item": {"mcpPolicy": {"mode": "allowlist", "allowedTargets": ["a"]}}
            }
            resp = _invoke(_apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/mcp/targets",
                query_params={"all": "true"},
            ))
        # Admin sees ALL targets, unfiltered
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        names = [t["name"] for t in data["items"]]
        assert names == ["a", "b"]

    def test_show_all_editor_forbidden(
        self, workspace_id, mock_jwt, _mock_editor, mock_ws_table
    ):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/mcp/targets",
            query_params={"all": "true"},
        ))
        assert resp["statusCode"] == 403

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_ws_table
    ):
        resp = _invoke(_apigw(
            "GET", f"/api/workspaces/{workspace_id}/mcp/targets"
        ))
        assert resp["statusCode"] == 403

    def test_internal_error_on_unexpected_exception(
        self, workspace_id, mock_jwt, _mock_editor, mock_ws_table
    ):
        with patch("crud.mcp._build_target_list", side_effect=Exception("boom")):
            resp = _invoke(_apigw(
                "GET", f"/api/workspaces/{workspace_id}/mcp/targets"
            ))
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# GET /api/workspaces/{ws}/mcp/targets/{name}/tools
# ---------------------------------------------------------------------------


class TestGetTargetTools:
    def test_loads_from_s3(self, workspace_id, mock_jwt, _mock_editor):
        tools = [{"name": "search"}]
        with patch("crud.mcp.boto3.client") as bc:
            s3 = MagicMock()
            body = MagicMock()
            body.read.return_value = json.dumps(tools).encode()
            s3.get_object.return_value = {"Body": body}
            bc.return_value = s3
            resp = _invoke(_apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/mcp/targets/aws-pricing/tools",
            ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["tools"] == tools

    def test_no_manifest_returns_hint(self, workspace_id, mock_jwt, _mock_editor):
        with patch("crud.mcp.boto3.client") as bc:
            s3 = MagicMock()
            s3.get_object.side_effect = Exception("not found")
            bc.return_value = s3
            resp = _invoke(_apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/mcp/targets/missing/tools",
            ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["tools"] == []
        assert "deploy-mcp.sh" in data["hint"]

    def test_uses_cache(self, workspace_id, mock_jwt, _mock_editor):
        # First call hits S3, second hits cache
        tools = [{"name": "t1"}]
        with patch("crud.mcp.boto3.client") as bc:
            s3 = MagicMock()
            body = MagicMock()
            body.read.return_value = json.dumps(tools).encode()
            s3.get_object.return_value = {"Body": body}
            bc.return_value = s3
            r1 = _invoke(_apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/mcp/targets/x/tools",
            ))
            assert r1["statusCode"] == 200
            # Now patch S3 to fail — cached value should still be served
            s3.get_object.side_effect = Exception("should not be called")
            r2 = _invoke(_apigw(
                "GET",
                f"/api/workspaces/{workspace_id}/mcp/targets/x/tools",
            ))
            assert r2["statusCode"] == 200
            data = json.loads(r2["body"])
            assert data["tools"] == tools

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership
    ):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/mcp/targets/x/tools",
        ))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# Workspace policy helper
# ---------------------------------------------------------------------------


class TestGetWorkspacePolicy:
    def test_no_policy_returns_default(self, mock_ws_table):
        from crud.mcp import _get_workspace_policy
        mock_ws_table.get_item.return_value = {"Item": {}}
        policy = _get_workspace_policy("ws-x")
        assert policy["mode"] == "all"

    def test_returns_existing(self, mock_ws_table):
        from crud.mcp import _get_workspace_policy
        mock_ws_table.get_item.return_value = {
            "Item": {"mcpPolicy": {"mode": "denylist", "deniedTargets": ["x"]}}
        }
        policy = _get_workspace_policy("ws-x")
        assert policy["mode"] == "denylist"
        assert policy["deniedTargets"] == ["x"]

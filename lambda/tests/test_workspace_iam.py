"""Tests for crud/workspace_iam.py — workspace IAM role management,
and workspace deletion IAM cleanup (in crud/workspaces.py).
"""
import json
from unittest.mock import MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("WORKSPACE_BOUNDARY_ARN",
                       "arn:aws:iam::123456789012:policy/AgentStudioWorkspaceCeiling")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    # Force the module-level constants in workspace_iam to pick up reloaded config.
    import crud.workspace_iam as _mod
    importlib.reload(_mod)


def _event(method, path, body=None, query_params=None, path_params=None):
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": path_params or {},
        "queryStringParameters": query_params or {},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }


# ──────────────────────────────────────────────────────────
# POST /api/workspaces/{wsId}/role
# ──────────────────────────────────────────────────────────

class TestCreateWorkspaceRole:
    def test_creates_role_and_stores_in_ddb(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()

        # Workspace META exists, no roleArn yet.
        meta_item = {"workspaceId": workspace_id, "sk": "META", "name": "Test WS"}
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}

        # IAM role does not exist yet.
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        fake_iam.get_role.side_effect = fake_iam.exceptions.NoSuchEntityException()
        fake_iam.create_role.return_value = {
            "Role": {"Arn": f"arn:aws:iam::123456789012:role/AgentStudio-ws-{workspace_id[:20]}-us-east-1"}
        }
        fake_iam.put_role_policy.return_value = {}

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam._get_iam", return_value=fake_iam), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role",
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["created"] is True
        assert "roleArn" in data
        assert data["roleName"].startswith("AgentStudio-ws-")

        # Verify IAM create_role was called with boundary.
        create_call = fake_iam.create_role.call_args
        assert "PermissionsBoundary" in create_call.kwargs
        assert "AgentStudioWorkspaceCeiling" in create_call.kwargs["PermissionsBoundary"]

        # Verify DefaultMinimal policy was attached.
        put_policy_call = fake_iam.put_role_policy.call_args
        assert put_policy_call.kwargs["PolicyName"] == "DefaultMinimal"

    def test_idempotent_when_role_already_bound(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        existing_arn = f"arn:aws:iam::123456789012:role/AgentStudio-ws-{workspace_id[:20]}-us-east-1"
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": existing_arn, "roleName": f"AgentStudio-ws-{workspace_id[:20]}-us-east-1",
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role",
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["created"] is False
        assert data["roleArn"] == existing_arn

    def test_rejects_non_admin(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app
        from shared.response import forbidden

        with patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (None, None, None, forbidden())
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role",
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 403


# ──────────────────────────────────────────────────────────
# POST /api/workspaces/{wsId}/grant-mcp
# ──────────────────────────────────────────────────────────

class TestGrantMcp:
    def test_grants_targets_and_writes_policy(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()

        role_name = f"AgentStudio-ws-{workspace_id[:20]}-us-east-1"
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": f"arn:aws:iam::123456789012:role/{role_name}",
            "roleName": role_name,
        }
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            "ConditionalCheckFailedException", (Exception,), {}
        )

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam._get_iam", return_value=fake_iam), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/grant-mcp",
                        body={"targets": ["cloudwatch", "cloudtrail"]},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert "cloudtrail" in data["mcpGrants"]
        assert "cloudwatch" in data["mcpGrants"]
        assert "policySize" in data

        # Verify IAM put_role_policy was called with MCP-Access.
        put_call = fake_iam.put_role_policy.call_args
        assert put_call.kwargs["PolicyName"] == "MCP-Access"
        policy_doc = json.loads(put_call.kwargs["PolicyDocument"])
        actions = []
        for stmt in policy_doc["Statement"]:
            actions.extend(stmt["Action"])
        assert "cloudwatch:DescribeAlarms" in actions
        assert "cloudtrail:LookupEvents" in actions

    def test_rejects_unknown_targets(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": "arn:aws:iam::123456789012:role/test",
            "roleName": "test",
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/grant-mcp",
                        body={"targets": ["nonexistent-target"]},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 400
        assert "Unknown MCP targets" in json.loads(resp["body"])["error"]

    def test_rejects_when_no_role(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        meta_item = {"workspaceId": workspace_id, "sk": "META"}
        fake_table.get_item.return_value = {"Item": meta_item}

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/grant-mcp",
                        body={"targets": ["cloudwatch"]},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 400
        assert "no IAM role" in json.loads(resp["body"])["error"]

    def test_rejects_empty_targets(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        with patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/grant-mcp",
                        body={"targets": []},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 400


# ──────────────────────────────────────────────────────────
# POST /api/workspaces/{wsId}/revoke-mcp
# ──────────────────────────────────────────────────────────

class TestRevokeMcp:
    def test_revokes_targets_and_rebuilds_policy(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()

        role_name = f"AgentStudio-ws-{workspace_id[:20]}-us-east-1"
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": f"arn:aws:iam::123456789012:role/{role_name}",
            "roleName": role_name,
            "mcpGrants": ["cloudwatch", "cloudtrail", "iam"],
        }
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            "ConditionalCheckFailedException", (Exception,), {}
        )

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam._get_iam", return_value=fake_iam), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/revoke-mcp",
                        body={"targets": ["iam"]},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert "iam" not in data["mcpGrants"]
        assert "cloudwatch" in data["mcpGrants"]
        assert "cloudtrail" in data["mcpGrants"]

    def test_revoke_all_removes_policy(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})

        role_name = "test-role"
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": "arn:aws:iam::123456789012:role/test-role",
            "roleName": role_name,
            "mcpGrants": ["cloudwatch"],
        }
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            "ConditionalCheckFailedException", (Exception,), {}
        )

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam._get_iam", return_value=fake_iam), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/revoke-mcp",
                        body={"targets": ["cloudwatch"]},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["mcpGrants"] == []

        # When all grants removed, policy should be deleted (not put).
        fake_iam.delete_role_policy.assert_called_once_with(
            RoleName=role_name, PolicyName="MCP-Access"
        )


# ──────────────────────────────────────────────────────────
# GET /api/workspaces/{wsId}/permissions
# ──────────────────────────────────────────────────────────

class TestGetPermissions:
    def test_returns_simulation_results(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()

        role_arn = "arn:aws:iam::123456789012:role/test-role"
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": role_arn,
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        fake_iam.simulate_principal_policy.return_value = {
            "EvaluationResults": [
                {"EvalActionName": "cloudwatch:DescribeAlarms", "EvalDecision": "allowed"},
                {"EvalActionName": "logs:StartQuery", "EvalDecision": "implicitDeny"},
            ]
        }

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam._get_iam", return_value=fake_iam), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event("GET", f"/api/workspaces/{workspace_id}/permissions",
                        query_params={"actions": "cloudwatch:DescribeAlarms,logs:StartQuery"},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["hasRole"] is True
        assert data["roleArn"] == role_arn
        assert len(data["results"]) == 2
        assert data["results"][0]["action"] == "cloudwatch:DescribeAlarms"
        assert data["results"][0]["allowed"] is True
        assert data["results"][1]["allowed"] is False

    def test_returns_no_role_when_missing(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        meta_item = {"workspaceId": workspace_id, "sk": "META"}
        fake_table.get_item.return_value = {"Item": meta_item}

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event("GET", f"/api/workspaces/{workspace_id}/permissions",
                        query_params={"actions": "cloudwatch:DescribeAlarms"},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["hasRole"] is False

    def test_rejects_missing_actions_param(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": "arn:aws:iam::123456789012:role/test",
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event("GET", f"/api/workspaces/{workspace_id}/permissions",
                        query_params={},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 400

    def test_batches_large_action_lists(self, mock_jwt, user_id, workspace_id):
        """SimulatePrincipalPolicy allows max 25 actions per call — verify batching."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()

        role_arn = "arn:aws:iam::123456789012:role/test-role"
        meta_item = {"workspaceId": workspace_id, "sk": "META", "roleArn": role_arn}
        fake_table.get_item.return_value = {"Item": meta_item}

        # 30 actions → should be batched as 25 + 5.
        actions = [f"svc:Action{i}" for i in range(30)]

        def _simulate(**kwargs):
            return {
                "EvaluationResults": [
                    {"EvalActionName": a, "EvalDecision": "allowed"}
                    for a in kwargs["ActionNames"]
                ]
            }

        fake_iam.simulate_principal_policy.side_effect = _simulate

        with patch("crud.workspace_iam._get_table", return_value=fake_table), \
             patch("crud.workspace_iam._get_iam", return_value=fake_iam), \
             patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event("GET", f"/api/workspaces/{workspace_id}/permissions",
                        query_params={"actions": ",".join(actions)},
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["results"]) == 30
        # Two calls: 25 + 5
        assert fake_iam.simulate_principal_policy.call_count == 2


# ──────────────────────────────────────────────────────────
# Unit tests for helper functions
# ──────────────────────────────────────────────────────────

class TestBuildMcpPolicy:
    def test_merges_multiple_targets(self):
        from crud.workspace_iam import _build_mcp_policy
        policy = _build_mcp_policy(["cloudwatch", "cloudtrail"])
        assert policy is not None
        assert policy["Version"] == "2012-10-17"
        sids = {s["Sid"] for s in policy["Statement"]}
        assert "McpCloudWatch" in sids
        assert "McpCloudTrail" in sids

    def test_returns_none_for_no_iam_targets(self):
        from crud.workspace_iam import _build_mcp_policy
        # aws-knowledge is in _NO_IAM_TARGETS, not in MCP_IAM_POLICIES
        policy = _build_mcp_policy(["aws-knowledge"])
        assert policy is None

    def test_returns_none_for_empty_list(self):
        from crud.workspace_iam import _build_mcp_policy
        policy = _build_mcp_policy([])
        assert policy is None

    def test_deduplicates_targets(self):
        from crud.workspace_iam import _build_mcp_policy
        policy = _build_mcp_policy(["cloudwatch", "cloudwatch"])
        assert policy is not None
        sids = [s["Sid"] for s in policy["Statement"]]
        assert sids.count("McpCloudWatch") == 1


# ──────────────────────────────────────────────────────────
# Workspace deletion IAM cleanup (crud/workspaces.py)
# ──────────────────────────────────────────────────────────

class TestDeleteWorkspaceIamRole:
    """Verify _delete_workspace_iam_role follows correct IAM deletion order:
    1. Delete all inline policies
    2. Detach all managed policies
    3. Delete the role
    """

    def test_deletes_role_in_correct_order(self):
        from crud.workspaces import _delete_workspace_iam_role

        fake_iam = MagicMock()
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        fake_iam.list_role_policies.return_value = {
            "PolicyNames": ["DefaultMinimal", "MCP-Access"],
        }
        fake_iam.list_attached_role_policies.return_value = {
            "AttachedPolicies": [
                {"PolicyName": "SomeManaged", "PolicyArn": "arn:aws:iam::123456789012:policy/SomeManaged"},
            ],
        }

        call_order = []
        fake_iam.delete_role_policy.side_effect = lambda **kw: call_order.append(("delete_inline", kw["PolicyName"]))
        fake_iam.detach_role_policy.side_effect = lambda **kw: call_order.append(("detach_managed", kw["PolicyArn"]))
        fake_iam.delete_role.side_effect = lambda **kw: call_order.append(("delete_role", kw["RoleName"]))

        workspace_meta = {"roleName": "AgentStudio-ws-test12345-us-east-1"}

        with patch("crud.workspaces._get_iam_client", return_value=fake_iam):
            _delete_workspace_iam_role(workspace_meta)

        # Verify order: inline policies first, then managed policies, then delete role.
        assert call_order == [
            ("delete_inline", "DefaultMinimal"),
            ("delete_inline", "MCP-Access"),
            ("detach_managed", "arn:aws:iam::123456789012:policy/SomeManaged"),
            ("delete_role", "AgentStudio-ws-test12345-us-east-1"),
        ]

    def test_noop_when_no_role_name(self):
        from crud.workspaces import _delete_workspace_iam_role

        fake_iam = MagicMock()
        with patch("crud.workspaces._get_iam_client", return_value=fake_iam):
            _delete_workspace_iam_role({})
            _delete_workspace_iam_role({"roleName": ""})
            _delete_workspace_iam_role({"roleName": None})

        # IAM should never be called.
        fake_iam.list_role_policies.assert_not_called()
        fake_iam.delete_role.assert_not_called()

    def test_ignores_nosuchentity(self):
        from crud.workspaces import _delete_workspace_iam_role

        fake_iam = MagicMock()
        nse = type("NoSuchEntityException", (Exception,), {})
        fake_iam.exceptions.NoSuchEntityException = nse
        fake_iam.list_role_policies.side_effect = nse()

        workspace_meta = {"roleName": "AgentStudio-ws-gone-us-east-1"}

        with patch("crud.workspaces._get_iam_client", return_value=fake_iam):
            # Should not raise.
            _delete_workspace_iam_role(workspace_meta)

    def test_best_effort_on_other_errors(self):
        from crud.workspaces import _delete_workspace_iam_role

        fake_iam = MagicMock()
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        fake_iam.list_role_policies.side_effect = RuntimeError("AccessDenied")

        workspace_meta = {"roleName": "AgentStudio-ws-denied-us-east-1"}

        with patch("crud.workspaces._get_iam_client", return_value=fake_iam):
            # Should not raise — best-effort cleanup.
            _delete_workspace_iam_role(workspace_meta)

    def test_delete_endpoint_calls_cleanup(self, mock_jwt, user_id, workspace_id):
        """DELETE /api/workspaces/{wsId} should call _delete_workspace_iam_role."""
        from crud.handler import app

        fake_table = MagicMock()
        role_name = f"AgentStudio-ws-{workspace_id[:20]}-us-east-1"
        meta_item = {
            "workspaceId": workspace_id, "sk": "META",
            "roleArn": f"arn:aws:iam::123456789012:role/{role_name}",
            "roleName": role_name,
        }
        fake_table.get_item.return_value = {"Item": meta_item}
        # query returns the META item, then empty on next iteration
        fake_table.query.return_value = {
            "Items": [{"workspaceId": workspace_id, "sk": "META"}],
        }
        fake_table.batch_writer.return_value.__enter__ = MagicMock()
        fake_table.batch_writer.return_value.__exit__ = MagicMock(return_value=False)

        with patch("crud.workspaces._get_table", return_value=fake_table), \
             patch("crud.workspaces._delete_workspace_iam_role") as mock_cleanup, \
             patch("crud.workspaces.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "owner"}, None)
            ev = _event("DELETE", f"/api/workspaces/{workspace_id}",
                        path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        mock_cleanup.assert_called_once_with(meta_item)

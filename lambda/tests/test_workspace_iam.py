"""Tests for crud/workspace_iam.py — workspace IAM role management,
and workspace deletion IAM cleanup (in crud/workspaces.py).
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_platform_admin():
    """All workspace_iam endpoints require platform admin. Patch globally for these tests."""
    with patch("crud.workspace_iam.check_platform_admin") as mock:
        mock.return_value = ("test-user-id", True, None)
        yield mock


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv(
        "WORKSPACE_BOUNDARY_ARN", "arn:aws:iam::123456789012:policy/AgentStudioWorkspaceCeiling"
    )
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

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
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
            "workspaceId": workspace_id,
            "sk": "META",
            "roleArn": existing_arn,
            "roleName": f"AgentStudio-ws-{workspace_id[:20]}-us-east-1",
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["created"] is False
        assert data["roleArn"] == existing_arn

    def test_rejects_non_admin(self, mock_jwt, user_id, workspace_id):
        """POST /role is gated on check_platform_admin (Cognito group), not auth_check."""
        from crud.handler import app
        from shared.response import forbidden

        with patch("crud.workspace_iam.check_platform_admin") as ck:
            ck.return_value = (user_id, False, forbidden())
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
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
            "workspaceId": workspace_id,
            "sk": "META",
            "roleArn": f"arn:aws:iam::123456789012:role/{role_name}",
            "roleName": role_name,
        }
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            "ConditionalCheckFailedException", (Exception,), {}
        )

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudwatch", "cloudtrail"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert "cloudtrail" in data["mcpGrants"]
        assert "cloudwatch" in data["mcpGrants"]
        assert "policySize" in data

        # Verify IAM put_role_policy was called with WorkspaceGrants.
        put_call = fake_iam.put_role_policy.call_args
        assert put_call.kwargs["PolicyName"] == "WorkspaceGrants"
        policy_doc = json.loads(put_call.kwargs["PolicyDocument"])
        actions = []
        for stmt in policy_doc["Statement"]:
            actions.extend(stmt["Action"])
        # Registry uses wildcards (cloudwatch:Describe*) instead of explicit
        # actions; check for the expected service prefixes.
        assert any(a.startswith("cloudwatch:") for a in actions)
        assert "cloudtrail:LookupEvents" in actions

    def test_rejects_unknown_targets(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        meta_item = {
            "workspaceId": workspace_id,
            "sk": "META",
            "roleArn": "arn:aws:iam::123456789012:role/test",
            "roleName": "test",
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["nonexistent-target"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 400
        assert "Unknown MCP targets" in json.loads(resp["body"])["error"]

    def test_rejects_when_no_role(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        meta_item = {"workspaceId": workspace_id, "sk": "META"}
        fake_table.get_item.return_value = {"Item": meta_item}

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 400
        assert "no IAM role" in json.loads(resp["body"])["error"]

    def test_rejects_empty_targets(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        with patch("crud.workspace_iam.auth_check") as auth:
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": []},
                path_params={"wsId": workspace_id},
            )
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
            "workspaceId": workspace_id,
            "sk": "META",
            "roleArn": f"arn:aws:iam::123456789012:role/{role_name}",
            "roleName": role_name,
            "mcpGrants": ["cloudwatch", "cloudtrail", "iam"],
        }
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            "ConditionalCheckFailedException", (Exception,), {}
        )

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["iam"]},
                path_params={"wsId": workspace_id},
            )
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
            "workspaceId": workspace_id,
            "sk": "META",
            "roleArn": "arn:aws:iam::123456789012:role/test-role",
            "roleName": role_name,
            "mcpGrants": ["cloudwatch"],
        }
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = type(
            "ConditionalCheckFailedException", (Exception,), {}
        )

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "admin"}, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["mcpGrants"] == []

        # When all grants removed, policy should be deleted (not put).
        fake_iam.delete_role_policy.assert_called_once_with(RoleName=role_name, PolicyName="WorkspaceGrants")


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
            "workspaceId": workspace_id,
            "sk": "META",
            "roleArn": role_arn,
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        fake_iam.simulate_principal_policy.return_value = {
            "EvaluationResults": [
                {"EvalActionName": "cloudwatch:DescribeAlarms", "EvalDecision": "allowed"},
                {"EvalActionName": "logs:StartQuery", "EvalDecision": "implicitDeny"},
            ]
        }

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event(
                "GET",
                f"/api/workspaces/{workspace_id}/permissions",
                query_params={"actions": "cloudwatch:DescribeAlarms,logs:StartQuery"},
                path_params={"wsId": workspace_id},
            )
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

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event(
                "GET",
                f"/api/workspaces/{workspace_id}/permissions",
                query_params={"actions": "cloudwatch:DescribeAlarms"},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["hasRole"] is False

    def test_missing_actions_param_returns_role_probe(self, mock_jwt, user_id, workspace_id):
        """Empty actions=just probe role existence (200, hasRole=True, results=[])."""
        from crud.handler import app

        fake_table = MagicMock()
        meta_item = {
            "workspaceId": workspace_id,
            "sk": "META",
            "roleArn": "arn:aws:iam::123456789012:role/test",
        }
        fake_table.get_item.return_value = {"Item": meta_item}

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event(
                "GET",
                f"/api/workspaces/{workspace_id}/permissions",
                query_params={},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["hasRole"] is True
        assert data["results"] == []

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
                    {"EvalActionName": a, "EvalDecision": "allowed"} for a in kwargs["ActionNames"]
                ]
            }

        fake_iam.simulate_principal_policy.side_effect = _simulate

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event(
                "GET",
                f"/api/workspaces/{workspace_id}/permissions",
                query_params={"actions": ",".join(actions)},
                path_params={"wsId": workspace_id},
            )
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
        # SIDs come from mcp_iam_registry.yaml — capitalized target names without prefix
        assert "Cloudwatch" in sids
        assert "Cloudtrail" in sids

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
        assert sids.count("Cloudwatch") == 1


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
        fake_iam.delete_role_policy.side_effect = lambda **kw: call_order.append(
            ("delete_inline", kw["PolicyName"])
        )
        fake_iam.detach_role_policy.side_effect = lambda **kw: call_order.append(
            ("detach_managed", kw["PolicyArn"])
        )
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

    def test_role_not_admin_returns_forbidden(self, mock_jwt, user_id, workspace_id):
        """Authenticated but not in platform-admins → 403."""
        from crud.handler import app

        with patch("crud.workspace_iam.check_platform_admin") as ck:
            ck.return_value = (user_id, False, None)
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 403

    def test_role_missing_account_id_returns_500(self, mock_jwt, user_id, workspace_id):
        """ACCOUNT_ID is required to construct trust policy."""
        from crud.handler import app

        with patch("crud.workspace_iam.ACCOUNT_ID", ""):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 500
        assert "ACCOUNT_ID" in json.loads(resp["body"])["error"]

    def test_role_missing_boundary_arn_returns_500(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        with patch("crud.workspace_iam.WORKSPACE_BOUNDARY_ARN", ""):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 500
        assert "WORKSPACE_BOUNDARY_ARN" in json.loads(resp["body"])["error"]

    def test_role_workspace_not_found(self, mock_jwt, user_id, workspace_id):
        """No META row for workspace → 404."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_table.get_item.return_value = {}

        with patch("crud.workspace_iam._get_table", return_value=fake_table):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 404

    def test_role_existing_iam_role_is_bound(self, mock_jwt, user_id, workspace_id):
        """If create_role would conflict but get_role finds the role, reuse its ARN."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        meta_item = {"workspaceId": workspace_id, "sk": "META", "name": "WS"}
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_table.update_item.return_value = {}
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        # get_role succeeds → reuse the existing role's ARN.
        existing_arn = f"arn:aws:iam::123456789012:role/AgentStudio-ws-{workspace_id[:20]}-us-east-1"
        fake_iam.get_role.return_value = {"Role": {"Arn": existing_arn}}

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["roleArn"] == existing_arn
        # No create_role call when an existing role was bound.
        fake_iam.create_role.assert_not_called()

    def test_role_create_role_failure_returns_500(self, mock_jwt, user_id, workspace_id):
        """If create_role itself raises, surface the 500."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        meta_item = {"workspaceId": workspace_id, "sk": "META"}
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        fake_iam.get_role.side_effect = fake_iam.exceptions.NoSuchEntityException()
        fake_iam.create_role.side_effect = RuntimeError("boundary-attach-denied")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 500

    def test_role_put_default_minimal_failure_cleans_up(self, mock_jwt, user_id, workspace_id):
        """If put_role_policy fails after create_role, delete the orphan role."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        meta_item = {"workspaceId": workspace_id, "sk": "META"}
        fake_table.get_item.return_value = {"Item": meta_item}
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        fake_iam.get_role.side_effect = fake_iam.exceptions.NoSuchEntityException()
        fake_iam.create_role.return_value = {
            "Role": {"Arn": f"arn:aws:iam::123456789012:role/AgentStudio-ws-{workspace_id[:20]}-us-east-1"}
        }
        fake_iam.put_role_policy.side_effect = RuntimeError("policy-attach-failed")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 500
        # Cleanup attempt: delete_role called for the orphan.
        fake_iam.delete_role.assert_called_once()

    def test_role_put_default_minimal_failure_swallows_cleanup_error(self, mock_jwt, user_id, workspace_id):
        """delete_role can also raise — must not double-explode."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        fake_table.get_item.return_value = {"Item": {"workspaceId": workspace_id, "sk": "META"}}
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        fake_iam.get_role.side_effect = fake_iam.exceptions.NoSuchEntityException()
        fake_iam.create_role.return_value = {
            "Role": {"Arn": f"arn:aws:iam::123456789012:role/X-{workspace_id[:20]}-us-east-1"}
        }
        fake_iam.put_role_policy.side_effect = RuntimeError("attach-fail")
        fake_iam.delete_role.side_effect = RuntimeError("cleanup-also-fails")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        # Still a 500; the inner Exception swallows.
        assert resp["statusCode"] == 500

    def test_role_ddb_conditional_check_failure_returns_existing(self, mock_jwt, user_id, workspace_id):
        """If ConditionalCheckFailedException fires (race), refetch + return existing."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        ccf = type("ConditionalCheckFailedException", (Exception,), {})
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = ccf

        # First get_item: META without role. Second (refetch): META with role.
        responses = iter(
            [
                {"Item": {"workspaceId": workspace_id, "sk": "META"}},
                {
                    "Item": {
                        "workspaceId": workspace_id,
                        "sk": "META",
                        "roleArn": "arn:aws:iam::123456789012:role/X",
                        "roleName": "X",
                    }
                },
            ]
        )
        fake_table.get_item.side_effect = lambda **kw: next(responses)
        fake_table.update_item.side_effect = ccf()

        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})
        fake_iam.get_role.side_effect = fake_iam.exceptions.NoSuchEntityException()
        fake_iam.create_role.return_value = {
            "Role": {"Arn": f"arn:aws:iam::123456789012:role/AgentStudio-ws-{workspace_id[:20]}-us-east-1"}
        }
        fake_iam.put_role_policy.return_value = {}

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event("POST", f"/api/workspaces/{workspace_id}/role", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["created"] is False
        assert data["roleArn"] == "arn:aws:iam::123456789012:role/X"

    def test_delete_endpoint_calls_cleanup(self, mock_jwt, user_id, workspace_id):
        """DELETE /api/workspaces/{wsId} should call _delete_workspace_iam_role."""
        from crud.handler import app

        fake_table = MagicMock()
        role_name = f"AgentStudio-ws-{workspace_id[:20]}-us-east-1"
        meta_item = {
            "workspaceId": workspace_id,
            "sk": "META",
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

        with (
            patch("crud.workspaces._get_table", return_value=fake_table),
            patch("crud.workspaces._delete_workspace_iam_role") as mock_cleanup,
            patch("crud.workspaces.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "owner"}, None)
            ev = _event("DELETE", f"/api/workspaces/{workspace_id}", path_params={"wsId": workspace_id})
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 200
        mock_cleanup.assert_called_once_with(meta_item)


# ──────────────────────────────────────────────────────────
# grant_mcp / revoke_mcp — additional edge cases
# ──────────────────────────────────────────────────────────


class TestGrantMcpExtra:
    def test_not_admin_returns_forbidden(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        with patch("crud.workspace_iam.check_platform_admin") as ck:
            ck.return_value = (user_id, False, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 403

    def test_admin_err_propagates(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app
        from shared.response import forbidden

        with patch("crud.workspace_iam.check_platform_admin") as ck:
            ck.return_value = (user_id, False, forbidden())
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 403

    def test_targets_must_be_list(self, mock_jwt, user_id, workspace_id):
        """`targets` must be a list, not e.g. a string."""
        from crud.handler import app

        ev = _event(
            "POST",
            f"/api/workspaces/{workspace_id}/grant-mcp",
            body={"targets": "cloudwatch"},
            path_params={"wsId": workspace_id},
        )
        resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 400

    def test_workspace_not_found(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_table.get_item.return_value = {}
        with patch("crud.workspace_iam._get_table", return_value=fake_table):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 404

    def test_concurrent_modification_returns_400(self, mock_jwt, user_id, workspace_id):
        """ConditionalCheckFailedException on update → 400."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        ccf = type("ConditionalCheckFailedException", (Exception,), {})
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = ccf
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
                "roleName": "X",
                "mcpGrants": ["cloudwatch"],
            }
        }
        fake_table.update_item.side_effect = ccf()

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudtrail"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 400
        assert "Concurrent" in json.loads(resp["body"])["error"]

    def test_iam_write_failure_rolls_back_ddb(self, mock_jwt, user_id, workspace_id):
        """If put_role_policy fails, roll back the DDB grants change."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        ccf = type("ConditionalCheckFailedException", (Exception,), {})
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = ccf
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
                "roleName": "X",
                "mcpGrants": [],
            }
        }
        # First update_item: succeed (DDB). Second update_item: rollback.
        fake_table.update_item.return_value = {}
        fake_iam.put_role_policy.side_effect = RuntimeError("iam-write-failed")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())

        assert resp["statusCode"] == 500
        # Two update_items: original + rollback.
        assert fake_table.update_item.call_count == 2

    def test_iam_write_failure_swallows_rollback_error(self, mock_jwt, user_id, workspace_id):
        """Rollback update can also fail; must not double-explode."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        ccf = type("ConditionalCheckFailedException", (Exception,), {})
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = ccf
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
                "roleName": "X",
                "mcpGrants": [],
            }
        }
        # First call succeeds (the main update); second call (rollback) raises.
        update_iter = iter([{}, RuntimeError("rollback-fail")])

        def _update(**kw):
            v = next(update_iter)
            if isinstance(v, Exception):
                raise v
            return v

        fake_table.update_item.side_effect = _update
        fake_iam.put_role_policy.side_effect = RuntimeError("iam-write-fail")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/grant-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 500


class TestRevokeMcpExtra:
    def test_not_admin_returns_forbidden(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        with patch("crud.workspace_iam.check_platform_admin") as ck:
            ck.return_value = (user_id, False, None)
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["iam"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 403

    def test_admin_err_propagates(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app
        from shared.response import forbidden

        with patch("crud.workspace_iam.check_platform_admin") as ck:
            ck.return_value = (user_id, False, forbidden())
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["iam"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 403

    def test_empty_targets(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        ev = _event(
            "POST",
            f"/api/workspaces/{workspace_id}/revoke-mcp",
            body={"targets": []},
            path_params={"wsId": workspace_id},
        )
        resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 400

    def test_targets_must_be_list(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        ev = _event(
            "POST",
            f"/api/workspaces/{workspace_id}/revoke-mcp",
            body={"targets": "cloudwatch"},
            path_params={"wsId": workspace_id},
        )
        resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 400

    def test_workspace_not_found(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_table.get_item.return_value = {}
        with patch("crud.workspace_iam._get_table", return_value=fake_table):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 404

    def test_no_role_returns_400(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
            }
        }
        with patch("crud.workspace_iam._get_table", return_value=fake_table):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 400

    def test_concurrent_modification_returns_400(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        ccf = type("ConditionalCheckFailedException", (Exception,), {})
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = ccf
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
                "roleName": "X",
                "mcpGrants": ["cloudwatch"],
            }
        }
        fake_table.update_item.side_effect = ccf()

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["cloudwatch"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 400

    def test_iam_write_failure_rolls_back_ddb(self, mock_jwt, user_id, workspace_id):
        """If write fails, attempt DDB rollback (best-effort)."""
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        ccf = type("ConditionalCheckFailedException", (Exception,), {})
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = ccf
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
                "roleName": "X",
                "mcpGrants": ["cloudwatch", "iam"],
            }
        }
        fake_table.update_item.return_value = {}
        # _write_mcp_policy: if mcpGrants becomes only ["cloudwatch"], it's
        # still a non-empty IAM-policy target → put_role_policy is called.
        fake_iam.put_role_policy.side_effect = RuntimeError("iam-fail")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["iam"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 500
        # Two update_items: original + rollback.
        assert fake_table.update_item.call_count == 2

    def test_iam_write_failure_swallows_rollback_error(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        ccf = type("ConditionalCheckFailedException", (Exception,), {})
        fake_table.meta.client.exceptions.ConditionalCheckFailedException = ccf
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
                "roleName": "X",
                "mcpGrants": ["cloudwatch", "iam"],
            }
        }
        update_iter = iter([{}, RuntimeError("rollback-fail")])

        def _update(**kw):
            v = next(update_iter)
            if isinstance(v, Exception):
                raise v
            return v

        fake_table.update_item.side_effect = _update
        fake_iam.put_role_policy.side_effect = RuntimeError("iam-fail")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
        ):
            ev = _event(
                "POST",
                f"/api/workspaces/{workspace_id}/revoke-mcp",
                body={"targets": ["iam"]},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 500


# ──────────────────────────────────────────────────────────
# get_permissions — error paths
# ──────────────────────────────────────────────────────────


class TestGetPermissionsExtra:
    def test_member_denied_falls_back_to_admin(self, mock_jwt, user_id, workspace_id):
        """auth_check err but caller is platform admin → still allowed."""
        from crud.handler import app
        from shared.response import forbidden as _f

        fake_table = MagicMock()
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
            }
        }

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam.auth_check") as auth,
            patch("crud.workspace_iam.check_platform_admin") as ck,
        ):
            auth.return_value = (None, None, None, _f())
            # First check_platform_admin (autouse fixture) returns admin=True for create_workspace_role,
            # but we override here for the inline check.
            ck.return_value = (user_id, True, None)
            ev = _event(
                "GET",
                f"/api/workspaces/{workspace_id}/permissions",
                query_params={},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 200

    def test_member_denied_and_not_admin_returns_forbidden(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app
        from shared.response import forbidden as _f

        with (
            patch("crud.workspace_iam.auth_check") as auth,
            patch("crud.workspace_iam.check_platform_admin") as ck,
        ):
            auth.return_value = (None, None, None, _f())
            ck.return_value = (user_id, False, None)
            ev = _event(
                "GET",
                f"/api/workspaces/{workspace_id}/permissions",
                query_params={"actions": "x:Y"},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 403

    def test_workspace_not_found(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_table.get_item.return_value = {}
        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event(
                "GET", f"/api/workspaces/{workspace_id}/permissions", path_params={"wsId": workspace_id}
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 404

    def test_simulate_failure_returns_500(self, mock_jwt, user_id, workspace_id):
        from crud.handler import app

        fake_table = MagicMock()
        fake_iam = MagicMock()
        fake_table.get_item.return_value = {
            "Item": {
                "workspaceId": workspace_id,
                "sk": "META",
                "roleArn": "arn:aws:iam::123456789012:role/X",
            }
        }
        fake_iam.simulate_principal_policy.side_effect = RuntimeError("boom")

        with (
            patch("crud.workspace_iam._get_table", return_value=fake_table),
            patch("crud.workspace_iam._get_iam", return_value=fake_iam),
            patch("crud.workspace_iam.auth_check") as auth,
        ):
            auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
            ev = _event(
                "GET",
                f"/api/workspaces/{workspace_id}/permissions",
                query_params={"actions": "iam:GetRole"},
                path_params={"wsId": workspace_id},
            )
            resp = app.resolve(ev, MagicMock())
        assert resp["statusCode"] == 500


# ──────────────────────────────────────────────────────────
# _write_mcp_policy direct tests
# ──────────────────────────────────────────────────────────


class TestWriteMcpPolicy:
    def test_returns_zero_and_deletes_when_no_iam_targets(self):
        """When merged is None, we delete the WorkspaceGrants policy."""
        from crud.workspace_iam import _write_mcp_policy

        fake_iam = MagicMock()
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})

        with patch("crud.workspace_iam._get_iam", return_value=fake_iam):
            size = _write_mcp_policy("role-A", [])
        assert size == 0
        fake_iam.delete_role_policy.assert_called_once_with(
            RoleName="role-A",
            PolicyName="WorkspaceGrants",
        )

    def test_swallows_no_such_entity_when_deleting(self):
        """If the policy isn't there, NoSuchEntityException is swallowed."""
        from crud.workspace_iam import _write_mcp_policy

        fake_iam = MagicMock()
        nse = type("NoSuchEntityException", (Exception,), {})
        fake_iam.exceptions.NoSuchEntityException = nse
        fake_iam.delete_role_policy.side_effect = nse()

        with patch("crud.workspace_iam._get_iam", return_value=fake_iam):
            size = _write_mcp_policy("role-A", ["aws-knowledge"])  # no-IAM target
        assert size == 0

    def test_writes_policy_returns_doc_size(self):
        from crud.workspace_iam import _write_mcp_policy

        fake_iam = MagicMock()
        with patch("crud.workspace_iam._get_iam", return_value=fake_iam):
            size = _write_mcp_policy("role-B", ["cloudwatch"])
        assert size > 0
        fake_iam.put_role_policy.assert_called_once()
        kwargs = fake_iam.put_role_policy.call_args.kwargs
        assert kwargs["RoleName"] == "role-B"
        assert kwargs["PolicyName"] == "WorkspaceGrants"


# ──────────────────────────────────────────────────────────
# lazy boto3 init
# ──────────────────────────────────────────────────────────


def test_get_table_initializes_lazily(monkeypatch):
    monkeypatch.setattr("crud.workspace_iam._table", None)
    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table
    with patch("crud.workspace_iam.boto3.resource", return_value=fake_resource) as br:
        from crud.workspace_iam import _get_table

        out = _get_table()
        out2 = _get_table()
    assert out is fake_table and out2 is fake_table
    assert br.call_count == 1


def test_get_iam_initializes_lazily(monkeypatch):
    monkeypatch.setattr("crud.workspace_iam._iam", None)
    fake_client = MagicMock()
    with patch("crud.workspace_iam.boto3.client", return_value=fake_client) as bc:
        from crud.workspace_iam import _get_iam

        c = _get_iam()
        c2 = _get_iam()
    assert c is fake_client and c2 is fake_client
    assert bc.call_count == 1


# ──────────────────────────────────────────────────────────
# trust + default-minimal policy builders (smoke)
# ──────────────────────────────────────────────────────────


def test_build_trust_policy_shape():
    from crud.workspace_iam import _build_trust_policy

    p = _build_trust_policy()
    assert p["Version"] == "2012-10-17"
    assert p["Statement"][0]["Action"] == "sts:AssumeRole"
    assert "aws:SourceAccount" in p["Statement"][0]["Condition"]["StringEquals"]


def test_build_default_minimal_policy_shape():
    from crud.workspace_iam import _build_default_minimal_policy

    p = _build_default_minimal_policy()
    assert p["Version"] == "2012-10-17"
    sids = {s["Sid"] for s in p["Statement"]}
    # Some core SIDs we expect.
    for required in ("Bedrock", "AgentCoreRuntime", "AgentCoreMemory", "Observability"):
        assert required in sids

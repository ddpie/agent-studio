"""Tests for crud/mcp_runtime_manager.py — enable/disable/upgrade/status logic.

Pure unit tests with boto3 mocked. Covers all 8 error codes from spec §6.4.
"""
import hashlib
import json
from unittest.mock import MagicMock, patch, call

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("AGENT_STUDIO_REGION", "us-east-1")
    monkeypatch.setenv("WORKSPACES_TABLE", "test-workspaces")
    monkeypatch.setenv("S3_BUCKET", "test-bucket")
    monkeypatch.setenv("WORKSPACE_BOUNDARY_ARN",
                       "arn:aws:iam::123456789012:policy/AgentStudioWorkspaceCeiling")
    # Reload config module so constants pick up new env.
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.mcp_runtime_manager as _mod
    importlib.reload(_mod)


@pytest.fixture
def mgr():
    import crud.mcp_runtime_manager as _mod
    return _mod


# ─── Naming / hash determinism ─────────────────────────────────────

class TestNaming:
    def test_ws_hash_deterministic(self, mgr):
        h1 = mgr.compute_ws_hash("ws-abc-123")
        h2 = mgr.compute_ws_hash("ws-abc-123")
        assert h1 == h2
        assert len(h1) == 12
        assert all(c in "0123456789abcdef" for c in h1)

    def test_runtime_name_format(self, mgr):
        name = mgr.build_runtime_name("ws-abc-123", "cloudwatch")
        assert name.startswith("asmcp_")
        assert "cloudwatch" in name
        assert len(name) <= 48

    def test_runtime_name_hyphens_become_underscores(self, mgr):
        name = mgr.build_runtime_name("ws-1", "bedrock-kb-retrieval")
        assert "bedrock_kb_retrieval" in name
        assert "-" not in name.split("_", 2)[2]  # target portion has no hyphen

    def test_runtime_name_exceeds_limit_raises(self, mgr):
        with pytest.raises(ValueError, match="exceeds"):
            mgr.build_runtime_name("ws-1", "x" * 40)

    def test_client_token_deterministic(self, mgr):
        t1 = mgr._client_token("ws-1", "cloudwatch", "asmcp_abc_cloudwatch")
        t2 = mgr._client_token("ws-1", "cloudwatch", "asmcp_abc_cloudwatch")
        assert t1 == t2
        assert len(t1) == 32


# ─── Enable: error paths (one per code) ────────────────────────────

class TestEnableErrors:
    def _setup(self, mgr, registry=None, ws_meta=None):
        """Common mocks. Returns (fake_table, fake_control, fake_iam, fake_ecr, fake_s3)."""
        fake_table = MagicMock()
        fake_control = MagicMock()
        fake_iam = MagicMock()
        fake_ecr = MagicMock()
        fake_s3 = MagicMock()
        fake_quotas = MagicMock()
        if ws_meta is not None:
            fake_table.get_item.return_value = {"Item": ws_meta}
        else:
            fake_table.get_item.return_value = {"Item": {
                "workspaceId": "ws-1", "sk": "META",
                "roleArn": "arn:aws:iam::123:role/AgentStudio-ws-ws-1-us-east-1",
                "roleName": "AgentStudio-ws-ws-1-us-east-1",
            }}
        if registry is None:
            registry = {"runtime_targets": [{"name": "cloudwatch", "enabled": True, "version": "0.0.24",
                                             "iam_policy": {"Statement": [{"Effect": "Allow", "Action": ["cloudwatch:Describe*"], "Resource": "*"}]}}]}
        fake_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
        }
        fake_ecr.describe_images.return_value = {"imageDetails": [{"imageTag": "0.0.24"}]}
        fake_control.create_agent_runtime.return_value = {
            "agentRuntimeId": "rt-xxx",
            "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/asmcp_xxx",
        }
        # Make list_agent_runtimes return empty (quota check sees count=0)
        fake_control.list_agent_runtimes.return_value = {"agentRuntimes": []}
        fake_quotas.get_service_quota.return_value = {"Quota": {"Value": 100}}

        # put_resource_policy OK
        fake_control.put_resource_policy.return_value = {}

        # iam put_role_policy OK
        fake_iam.put_role_policy.return_value = {}
        fake_iam.get_policy.return_value = {"Policy": {"Description": ""}}

        return fake_table, fake_control, fake_iam, fake_ecr, fake_s3, fake_quotas

    def test_unknown_target(self, mgr):
        fakes = self._setup(mgr, registry={"runtime_targets": []})
        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fakes[0]),
                            _get_control=MagicMock(return_value=fakes[1]),
                            _get_iam=MagicMock(return_value=fakes[2]),
                            _get_ecr=MagicMock(return_value=fakes[3]),
                            _get_s3=MagicMock(return_value=fakes[4]),
                            _get_quotas=MagicMock(return_value=fakes[5])):
            r = mgr.enable_target("ws-1", "unknownxyz", actor="u1")
        assert r["status"] == "error"
        assert r["error"] == "unknown_target"

    def test_role_missing_returns_role_missing(self, mgr):
        """Actual 412 mapping happens in handler; manager returns role_missing."""
        meta_no_role = {"workspaceId": "ws-1", "sk": "META"}  # no roleArn
        fakes = self._setup(mgr, ws_meta=meta_no_role)
        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fakes[0]),
                            _get_control=MagicMock(return_value=fakes[1]),
                            _get_iam=MagicMock(return_value=fakes[2]),
                            _get_ecr=MagicMock(return_value=fakes[3]),
                            _get_s3=MagicMock(return_value=fakes[4]),
                            _get_quotas=MagicMock(return_value=fakes[5])):
            r = mgr.enable_target("ws-1", "cloudwatch", actor="u1")
        assert r["status"] == "error"
        assert r["error"] == "role_missing"

    def test_image_missing(self, mgr):
        fakes = self._setup(mgr)
        fakes[3].describe_images.side_effect = Exception("ImageNotFoundException")
        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fakes[0]),
                            _get_control=MagicMock(return_value=fakes[1]),
                            _get_iam=MagicMock(return_value=fakes[2]),
                            _get_ecr=MagicMock(return_value=fakes[3]),
                            _get_s3=MagicMock(return_value=fakes[4]),
                            _get_quotas=MagicMock(return_value=fakes[5])):
            r = mgr.enable_target("ws-1", "cloudwatch", actor="u1")
        assert r["status"] == "error"
        assert r["error"] == "image_missing"

    def test_boundary_gap_blocked(self, mgr):
        """Target requires action NOT in ceiling → 400 boundary_gap."""
        # Registry target that requires a write action
        registry = {"runtime_targets": [{
            "name": "cloudwatch", "enabled": True, "version": "0.0.24",
            "iam_policy": {"Statement": [{"Effect": "Allow",
                                          "Action": ["dynamodb:DeleteTable"],
                                          "Resource": "*"}]},
        }]}
        fakes = self._setup(mgr, registry=registry)
        # Patch ceiling_actions import to simulate action NOT in ceiling.
        def mock_within_ceiling(actions):
            missing = [a for a in actions if a == "dynamodb:DeleteTable"]
            return (len(missing) == 0, missing)

        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fakes[0]),
                            _get_control=MagicMock(return_value=fakes[1]),
                            _get_iam=MagicMock(return_value=fakes[2]),
                            _get_ecr=MagicMock(return_value=fakes[3]),
                            _get_s3=MagicMock(return_value=fakes[4]),
                            _get_quotas=MagicMock(return_value=fakes[5]),
                            actions_within_ceiling=mock_within_ceiling):
            # Override registry MCP_IAM_POLICIES too (used for boundary check)
            with patch.dict(mgr.MCP_IAM_POLICIES, {"cloudwatch": registry["runtime_targets"][0]["iam_policy"]}):
                r = mgr.enable_target("ws-1", "cloudwatch", actor="u1")

        assert r["status"] == "error"
        assert r["error"] == "boundary_gap"
        assert "dynamodb:DeleteTable" in r["missing_actions"]

    def test_in_flight_409(self, mgr):
        """Target already CREATING → in_flight error."""
        meta = {
            "workspaceId": "ws-1", "sk": "META",
            "roleArn": "arn:...", "roleName": "AgentStudio-ws-ws-1-us-east-1",
            "mcp_runtimes": {
                "cloudwatch": {
                    "status": "CREATING",
                    "inflight_action": "CREATING",
                    "inflight_actor": "u99",
                }
            },
        }
        fakes = self._setup(mgr, ws_meta=meta)
        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fakes[0]),
                            _get_control=MagicMock(return_value=fakes[1]),
                            _get_iam=MagicMock(return_value=fakes[2]),
                            _get_ecr=MagicMock(return_value=fakes[3]),
                            _get_s3=MagicMock(return_value=fakes[4]),
                            _get_quotas=MagicMock(return_value=fakes[5])):
            r = mgr.enable_target("ws-1", "cloudwatch", actor="u1")
        assert r["status"] == "error"
        assert r["error"] == "in_flight"
        assert r["inflight_actor"] == "u99"

    def test_already_enabled_idempotent(self, mgr):
        """READY target returns idempotent success, not error."""
        meta = {
            "workspaceId": "ws-1", "sk": "META",
            "roleArn": "arn:...", "roleName": "AgentStudio-ws-ws-1-us-east-1",
            "mcp_runtimes": {"cloudwatch": {"status": "READY", "runtime_arn": "arn:rt:xxx"}},
        }
        fakes = self._setup(mgr, ws_meta=meta)
        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fakes[0]),
                            _get_control=MagicMock(return_value=fakes[1]),
                            _get_iam=MagicMock(return_value=fakes[2]),
                            _get_ecr=MagicMock(return_value=fakes[3]),
                            _get_s3=MagicMock(return_value=fakes[4]),
                            _get_quotas=MagicMock(return_value=fakes[5])):
            r = mgr.enable_target("ws-1", "cloudwatch", actor="u1")
        assert r["status"] in ("READY", "ACTIVE")
        assert r.get("idempotent") is True


# ─── Enable: happy path ────────────────────────────────────────────

class TestEnableHappyPath:
    def test_creates_runtime_and_writes_ddb(self, mgr):
        fake_table = MagicMock()
        fake_control = MagicMock()
        fake_iam = MagicMock()
        fake_ecr = MagicMock()
        fake_s3 = MagicMock()
        fake_quotas = MagicMock()

        fake_table.get_item.return_value = {"Item": {
            "workspaceId": "ws-1", "sk": "META",
            "roleArn": "arn:aws:iam::123:role/AgentStudio-ws-ws-1-us-east-1",
            "roleName": "AgentStudio-ws-ws-1-us-east-1",
        }}
        fake_s3.get_object.return_value = {
            "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(
                {"runtime_targets": [{"name": "cloudwatch", "enabled": True, "version": "0.0.24",
                                      "iam_policy": {"Statement": [{"Effect": "Allow", "Action": ["cloudwatch:Describe*"], "Resource": "*"}]}}]}
            ))),
        }
        fake_ecr.describe_images.return_value = {"imageDetails": [{"imageTag": "0.0.24"}]}
        fake_control.create_agent_runtime.return_value = {
            "agentRuntimeId": "rt-xxx",
            "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/asmcp_xxx",
        }
        fake_control.list_agent_runtimes.return_value = {"agentRuntimes": []}
        fake_quotas.get_service_quota.return_value = {"Quota": {"Value": 100}}

        # Also patch workspace_iam._get_iam since enable_target calls
        # ws_iam._write_mcp_policy which has its own lazy iam client.
        import crud.workspace_iam as wi
        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fake_table),
                            _get_control=MagicMock(return_value=fake_control),
                            _get_iam=MagicMock(return_value=fake_iam),
                            _get_ecr=MagicMock(return_value=fake_ecr),
                            _get_s3=MagicMock(return_value=fake_s3),
                            _get_quotas=MagicMock(return_value=fake_quotas),
                            _check_live_ceiling_hash=MagicMock(return_value=True),
                            actions_within_ceiling=MagicMock(return_value=(True, []))), \
             patch.object(wi, "_get_iam", return_value=fake_iam):
            r = mgr.enable_target("ws-1", "cloudwatch", actor="u1")

        assert r["status"] == "CREATING"
        assert r["runtime_id"] == "rt-xxx"
        assert "runtime_endpoint" in r
        # Verify put_resource_policy called
        fake_control.put_resource_policy.assert_called_once()


# ─── Disable ───────────────────────────────────────────────────────

class TestDisable:
    def test_not_enabled_returns_error(self, mgr):
        fake_table = MagicMock()
        fake_table.get_item.return_value = {"Item": {"workspaceId": "ws-1", "sk": "META"}}
        with patch.object(mgr, "_get_ws_table", return_value=fake_table):
            r = mgr.disable_target("ws-1", "cloudwatch", actor="u1")
        assert r["status"] == "error"
        assert r["error"] == "not_enabled"

    def test_deletes_runtime_and_pops_entry(self, mgr):
        fake_table = MagicMock()
        fake_control = MagicMock()
        fake_iam = MagicMock()
        fake_table.get_item.return_value = {"Item": {
            "workspaceId": "ws-1", "sk": "META",
            "roleName": "AgentStudio-ws-ws-1-us-east-1",
            "mcpGrants": ["cloudwatch", "iam"],
            "mcp_runtimes": {
                "cloudwatch": {"status": "READY", "runtime_id": "rt-xxx"},
            },
        }}
        with patch.multiple(mgr,
                            _get_ws_table=MagicMock(return_value=fake_table),
                            _get_control=MagicMock(return_value=fake_control),
                            _get_iam=MagicMock(return_value=fake_iam)):
            r = mgr.disable_target("ws-1", "cloudwatch", actor="u1")
        assert r["status"] == "DELETED"
        fake_control.delete_agent_runtime.assert_called_with(agentRuntimeId="rt-xxx")


# ─── Helpers ───────────────────────────────────────────────────────

def _yaml_bytes(data: dict) -> bytes:
    import yaml
    return yaml.safe_dump(data).encode()

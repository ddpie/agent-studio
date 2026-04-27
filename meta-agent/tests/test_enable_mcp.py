"""Tests for tools.enable_mcp — per-workspace MCP enable flow."""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock, patch


# ─── Mock strands + config before importing tools ──────────────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
sys.modules["strands"] = _mock_strands

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "REGION": "us-east-1",
    "S3_BUCKET": "test-bucket",
    "ACCOUNT_ID": "123456789012",
    "AGENTS_TABLE": "agent-studio-agents",
    "MODEL_ID": "anthropic.claude-sonnet-4-5-v1:0",
    "PERMISSION_TIER_ROLES": {"basic": "arn:aws:iam::123:role/basic"},
    "DEFAULT_PERMISSION_TIER": "basic",
    "AGENT_ROLE_ARN": "arn:aws:iam::123:role/AgentStudioMetaAgent-us-east-1",
    "SCHEDULER_TARGET_ROLE_ARN": "arn:aws:iam::123:role/SchedulerTarget",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::123:role/AgentStudioSubAgent-basic-us-east-1",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "base/sub-agent-deployment.zip",
    "SCHEDULER_TARGET_ROLE_NAME": "AgentStudioSchedulerTargetRole-us-east-1",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


def _parse(s: str) -> dict:
    return json.loads(s)


def _yaml_bytes(data: dict) -> bytes:
    import yaml
    return yaml.safe_dump(data).encode()


# ─── Tests ─────────────────────────────────────────────────────────

class TestEnableMcpGuards:
    def test_unknown_target(self):
        from tools import enable_mcp as em
        # Registry has no matching target → unknown_target
        with patch.object(em, "_s3") as s3_factory, \
             patch.object(em, "_scope") as scope:
            scope.current_workspace.return_value = "ws-1"
            scope.current_caller.return_value = "u-1"
            s3_mock = MagicMock()
            s3_mock.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(
                    {"runtime_targets": []}
                ))),
            }
            s3_factory.return_value = s3_mock
            r = _parse(em.enable_mcp("missing-target"))
        assert r["status"] == "error"
        assert r["error"] == "unknown_target"

    def test_requires_env_blocks(self):
        from tools import enable_mcp as em
        registry = {"runtime_targets": [
            {"name": "foo", "enabled": True, "version": "1", "requires_env": ["GITHUB_TOKEN"]}
        ]}
        with patch.object(em, "_s3") as s3_factory, \
             patch.object(em, "_scope") as scope:
            scope.current_workspace.return_value = "ws-1"
            scope.current_caller.return_value = "u-1"
            s3_mock = MagicMock()
            s3_mock.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            s3_factory.return_value = s3_mock
            r = _parse(em.enable_mcp("foo"))
        assert r["status"] == "error"
        assert r["error"] == "env_missing"

    def test_role_missing(self):
        from tools import enable_mcp as em
        registry = {"runtime_targets": [
            {"name": "foo", "enabled": True, "version": "1", "sensitivity": "low"}
        ]}
        with patch.object(em, "_s3") as s3_factory, \
             patch.object(em, "_ws_table") as tbl_factory, \
             patch.object(em, "_scope") as scope:
            scope.current_workspace.return_value = "ws-1"
            scope.current_caller.return_value = "u-1"
            scope.require_role.return_value = None
            s3_mock = MagicMock()
            s3_mock.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            s3_factory.return_value = s3_mock
            tbl = MagicMock()
            tbl.get_item.return_value = {"Item": {"workspaceId": "ws-1", "sk": "META"}}  # no roleArn
            tbl_factory.return_value = tbl
            r = _parse(em.enable_mcp("foo"))
        assert r["status"] == "error"
        assert r["error"] == "role_missing"

    def test_in_flight_returns_error(self):
        from tools import enable_mcp as em
        registry = {"runtime_targets": [
            {"name": "foo", "enabled": True, "version": "1", "sensitivity": "low"}
        ]}
        with patch.object(em, "_s3") as s3_factory, \
             patch.object(em, "_ws_table") as tbl_factory, \
             patch.object(em, "_scope") as scope:
            scope.current_workspace.return_value = "ws-1"
            scope.current_caller.return_value = "u-1"
            scope.require_role.return_value = None
            s3_mock = MagicMock()
            s3_mock.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            s3_factory.return_value = s3_mock
            tbl = MagicMock()
            tbl.get_item.return_value = {"Item": {
                "workspaceId": "ws-1", "sk": "META",
                "roleArn": "arn:...", "roleName": "role-xx",
                "mcp_runtimes": {"foo": {"status": "CREATING", "inflight_action": "CREATING"}},
            }}
            tbl_factory.return_value = tbl
            r = _parse(em.enable_mcp("foo"))
        assert r["status"] == "error"
        assert r["error"] == "in_flight"

    def test_already_ready_idempotent(self):
        from tools import enable_mcp as em
        registry = {"runtime_targets": [
            {"name": "foo", "enabled": True, "version": "1", "sensitivity": "low"}
        ]}
        with patch.object(em, "_s3") as s3_factory, \
             patch.object(em, "_ws_table") as tbl_factory, \
             patch.object(em, "_scope") as scope:
            scope.current_workspace.return_value = "ws-1"
            scope.current_caller.return_value = "u-1"
            scope.require_role.return_value = None
            s3_mock = MagicMock()
            s3_mock.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            s3_factory.return_value = s3_mock
            tbl = MagicMock()
            tbl.get_item.return_value = {"Item": {
                "workspaceId": "ws-1", "sk": "META",
                "roleArn": "arn:...", "roleName": "role-xx",
                "mcp_runtimes": {"foo": {"status": "READY", "runtime_name": "asmcp_xx_foo"}},
            }}
            tbl_factory.return_value = tbl
            r = _parse(em.enable_mcp("foo"))
        assert r["status"] == "READY"
        assert r.get("idempotent") is True


class TestResolveMcpEndpoints:
    """create_agent._resolve_mcp_endpoints reads DDB, not list_agent_runtimes."""

    def test_runtime_not_ready_raises(self):
        from tools import create_agent as ca
        registry = {"runtime_targets": [], "remote_targets": []}
        with patch("boto3.client") as boto_client, \
             patch("boto3.resource") as boto_resource, \
             patch("tools._scope.current_workspace", return_value="ws-1"):
            s3 = MagicMock()
            s3.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            boto_client.return_value = s3
            ddb = MagicMock()
            tbl = MagicMock()
            tbl.get_item.return_value = {"Item": {"mcp_runtimes": {}}}
            ddb.Table.return_value = tbl
            boto_resource.return_value = ddb
            try:
                ca._resolve_mcp_endpoints(["cloudwatch"])
                assert False, "expected ValueError"
            except ValueError as e:
                assert "cloudwatch" in str(e)
                assert "READY" in str(e) or "enable" in str(e).lower()

    def test_runtime_ready_returns_endpoint(self):
        from tools import create_agent as ca
        registry = {"runtime_targets": [], "remote_targets": []}
        with patch("boto3.client") as boto_client, \
             patch("boto3.resource") as boto_resource, \
             patch("tools._scope.current_workspace", return_value="ws-1"):
            s3 = MagicMock()
            s3.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            boto_client.return_value = s3
            ddb = MagicMock()
            tbl = MagicMock()
            tbl.get_item.return_value = {"Item": {"mcp_runtimes": {
                "cloudwatch": {
                    "status": "READY",
                    "runtime_name": "asmcp_abc_cloudwatch",
                    "runtime_arn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/asmcp_abc_cloudwatch",
                    "runtime_endpoint": "https://bedrock-agentcore...",
                }
            }}}
            ddb.Table.return_value = tbl
            boto_resource.return_value = ddb
            endpoints = ca._resolve_mcp_endpoints(["cloudwatch"])
        assert len(endpoints) == 1
        assert endpoints[0]["type"] == "runtime"
        assert endpoints[0]["runtime_name"] == "asmcp_abc_cloudwatch"
        assert endpoints[0]["runtime_arn"].endswith("asmcp_abc_cloudwatch")
        assert endpoints[0]["auth"] == "runtime"

    def test_remote_target_passes_through(self):
        from tools import create_agent as ca
        registry = {"remote_targets": [
            {"name": "aws-api", "enabled": True,
             "endpoint": "https://aws-mcp.us-east-1.api.aws/mcp", "auth": "sigv4"}
        ]}
        with patch("boto3.client") as boto_client:
            s3 = MagicMock()
            s3.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            boto_client.return_value = s3
            endpoints = ca._resolve_mcp_endpoints(["aws-api"])
        assert len(endpoints) == 1
        assert endpoints[0]["type"] == "remote"
        assert endpoints[0]["url"] == "https://aws-mcp.us-east-1.api.aws/mcp"
        assert endpoints[0]["auth"] == "aws-mcp"

"""Tests for validate_agent MCP readiness check (Phase 3 T3.2)."""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock, patch


# NOTE: We do NOT mock the `strands` module here — validate_agent imports
# Agent from strands and strands.models, both real packages available in
# the test env. Mocking would break the real imports.

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "REGION": "us-east-1",
    "S3_BUCKET": "test-bucket",
    "ACCOUNT_ID": "123456789012",
    "AGENTS_TABLE": "agent-studio-agents",
    "MODEL_ID": "anthropic.claude-sonnet-4-5-v1:0",
    "PERMISSION_TIER_ROLES": {"basic": "arn:aws:iam::123:role/basic"},
    "DEFAULT_PERMISSION_TIER": "basic",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::123:role/SubAgent",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "base/sub-agent-deployment.zip",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


def _yaml_bytes(data: dict) -> bytes:
    import yaml
    return yaml.safe_dump(data).encode()


def _parse(result: str) -> dict:
    return json.loads(result)


def _valid_base_kwargs() -> dict:
    return dict(
        agent_name="TestAgent",
        system_prompt="You are a test agent.",
        description="A test agent.",
        tool_definitions="",
        tool_names="",
        welcome_message="Hi",
        permission_tier="basic",
    )


class TestMcpReadiness:
    def _call(self, mcp_targets_csv: str, mcp_runtimes: dict, registry_remote: list = None):
        from tools import validate_agent as va
        from tools.validate_agent import validate_agent
        registry = {"remote_targets": registry_remote or [], "runtime_targets": []}

        with patch.object(va, "current_workspace", return_value="ws-1") if hasattr(va, "current_workspace") else patch("tools._scope.current_workspace", return_value="ws-1"), \
             patch("boto3.resource") as boto_res, \
             patch("boto3.client") as boto_client:
            tbl = MagicMock()
            tbl.get_item.return_value = {"Item": {"mcp_runtimes": mcp_runtimes}}
            ddb = MagicMock()
            ddb.Table.return_value = tbl
            boto_res.return_value = ddb

            s3 = MagicMock()
            s3.get_object.return_value = {
                "Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry))),
            }
            boto_client.return_value = s3

            kwargs = _valid_base_kwargs()
            # Embed mcp_targets via staging_key path (simpler) — fake s3.get_object
            # OR call directly via the raw function with mcp_targets. validate_agent
            # doesn't take mcp_targets as kwarg — only via staging_key. For the test
            # we fake it by monkey-patching the local behavior.
            # Simpler: call directly with staging_key pattern.
            staged = {**kwargs, "mcp_targets": mcp_targets_csv.split(",") if mcp_targets_csv else []}
            # Stage via S3: make get_object return staged JSON when Key matches "staging/…"
            def get_object_side_effect(**kw):
                key = kw.get("Key", "")
                if key.startswith("staging/") or key.endswith("stage.json"):
                    return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(staged).encode()))}
                # default: registry yaml
                return {"Body": MagicMock(read=MagicMock(return_value=_yaml_bytes(registry)))}
            s3.get_object.side_effect = get_object_side_effect

            resp = validate_agent(staging_key="staging/test.json")
            return _parse(resp)

    def test_target_not_enabled_is_error(self):
        result = self._call(
            mcp_targets_csv="cloudwatch",
            mcp_runtimes={},  # nothing enabled
        )
        assert result["valid"] is False
        assert any("cloudwatch" in e and "not enabled" in e for e in result["errors"])

    def test_target_creating_is_error_with_hint(self):
        result = self._call(
            mcp_targets_csv="cloudwatch",
            mcp_runtimes={"cloudwatch": {"status": "CREATING"}},
        )
        assert result["valid"] is False
        # Error should mention status AND provisioning/wait
        combined = " ".join(result["errors"]).lower()
        assert "cloudwatch" in combined
        assert "creating" in combined.lower() or "provision" in combined or "wait" in combined

    def test_target_failed_is_error_with_retry_hint(self):
        result = self._call(
            mcp_targets_csv="cloudwatch",
            mcp_runtimes={"cloudwatch": {"status": "FAILED", "last_error": "image pull timeout"}},
        )
        assert result["valid"] is False
        combined = " ".join(result["errors"]).lower()
        assert "failed" in combined
        assert "retry" in combined or "disabl" in combined

    def test_target_ready_passes(self):
        result = self._call(
            mcp_targets_csv="cloudwatch",
            mcp_runtimes={"cloudwatch": {"status": "READY"}},
        )
        # No MCP-specific errors (other validations might still fire but not this)
        mcp_errors = [e for e in result["errors"] if "cloudwatch" in e.lower()]
        assert mcp_errors == []

    def test_remote_target_always_ok(self):
        """aws-api et al don't need a per-workspace runtime."""
        result = self._call(
            mcp_targets_csv="aws-api",
            mcp_runtimes={},
            registry_remote=[{"name": "aws-api", "enabled": True,
                              "endpoint": "https://aws-mcp.us-east-1.api.aws/mcp",
                              "auth": "sigv4"}],
        )
        mcp_errors = [e for e in result["errors"] if "aws-api" in e.lower()]
        assert mcp_errors == []

    def test_mixed_ok_and_broken(self):
        """Ready + broken together: ready is fine, broken errors."""
        result = self._call(
            mcp_targets_csv="cloudwatch,iam",
            mcp_runtimes={
                "cloudwatch": {"status": "READY"},
                "iam": {"status": "FAILED", "last_error": "boom"},
            },
        )
        assert result["valid"] is False
        cloudwatch_errors = [e for e in result["errors"] if "cloudwatch" in e.lower() and "not" in e.lower()]
        iam_errors = [e for e in result["errors"] if "iam" in e.lower() and "FAILED" in e]
        assert cloudwatch_errors == []
        assert len(iam_errors) >= 1

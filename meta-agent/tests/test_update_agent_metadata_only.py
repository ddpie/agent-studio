"""Regression test: update_agent always redeploys.

Since 5d0e86d the metadata-only shortcut was removed — every update_agent
call now triggers a full repackage + update_agent_runtime. This test
verifies that a description-only change still succeeds (it exercises the
full redeploy path, not a metadata-only fallback).
"""
import json
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands
_mock_strands_models = sys.modules.get("strands.models") or types.ModuleType("strands.models")
if not hasattr(_mock_strands_models, "BedrockModel"):
    _mock_strands_models.BedrockModel = MagicMock
sys.modules["strands.models"] = _mock_strands_models

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "MODEL_ID": "mock-model",
    "ACCOUNT_ID": "000",
    "S3_BUCKET": "b",
    "AGENT_ROLE_ARN": "arn:aws:iam::000:role/r",
    "AGENTS_TABLE": "agent-studio-agents",
    "TOOLS_TABLE": "agent-studio-tools",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
    "BASE_DEPLOYMENT_KEY": "k",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "k",
    "SCHEDULER_TARGET_ROLE_ARN": "arn:aws:iam::000:role/sched",
    "PERMISSION_TIER_ROLES": {"readonly": "arn:aws:iam::000:role/r"},
    "DEFAULT_PERMISSION_TIER": "readonly",
    "MCP_GATEWAY_URL": "",
    "CODE_INTERPRETER_ID": "",
    "BROWSER_ID": "",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


from tools import update_agent as _ua_mod  # noqa: E402


AGENT_ID = "DataAnalyst-bCBR743Mvj"
WS_ID = "ws-1"
USER_ID = "user-1"


def _existing_metadata(tools=("s3_read", "generate_chart")):
    return {
        "agent_id": AGENT_ID,
        "name": "DataAnalyst",
        "display_name": "DataAnalyst",
        "description": "old description",
        "model_id": "mock-model",
        "system_prompt": "original prompt",
        "tools": list(tools),
        "tool_definitions": "",
        "welcome_message": "",
        "suggestions": [],
        "template_id": "data_analyst",
        "mcp_targets": [],
        "skills": [],
    }


def test_update_agent_description_only_triggers_full_redeploy():
    """Description-only update must succeed and trigger a full redeploy
    (no metadata-only shortcut exists any more)."""
    meta = _existing_metadata()

    class _FakeTable:
        def __init__(self):
            self.update_calls: list[dict] = []
        def update_item(self, **kwargs):
            self.update_calls.append(kwargs)

    fake_table = _FakeTable()
    fake_s3 = MagicMock()
    fake_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(meta).encode("utf-8")),
    }

    def _boto3_client(service, **_):
        if service == "s3":
            return fake_s3
        return MagicMock()

    ddb_resource = MagicMock()
    ddb_resource.Table = MagicMock(return_value=fake_table)

    with patch("tools.update_agent.boto3.client", side_effect=_boto3_client), \
         patch("tools.update_agent.boto3.resource", return_value=ddb_resource), \
         patch("tools._scope.ensure_agent_in_workspace",
               return_value=({"agentId": AGENT_ID, "workspace_id": WS_ID, "agentName": "DataAnalyst"}, None)), \
         patch("tools.update_agent.validate_agent_files",
               return_value={"valid": True, "errors": []}), \
         patch("tools.update_agent.build_deployment_package_v2", return_value=b"fake-zip"), \
         patch("tools.update_agent.upload_deployment", return_value=f"agents/{AGENT_ID}/deployment.zip"), \
         patch("tools.update_agent._get_agent_role_arn", return_value="arn:aws:iam::000:role/r"), \
         patch("tools.update_agent.build_skill_prompt_section", return_value=""), \
         patch("tools.update_agent.get_base_guidelines", return_value=""), \
         patch("tools._scope.current_creator_language", return_value="en"), \
         patch("threading.Thread") as mock_thread:

        out = json.loads(_ua_mod.update_agent(
            agent_id=AGENT_ID,
            agent_name="DataAnalyst",
            description="new description",
        ))

    assert "error" not in out, f"unexpected error: {out}"
    assert out.get("action") == "redeployed"
    assert out.get("needs_redeploy") is True
    assert out.get("status") == "QUEUED"
    assert mock_thread.called, "expected background redeploy thread to be started"
    assert fake_table.update_calls, "expected a DDB update_item call"
    last = fake_table.update_calls[-1]
    assert "description = :desc" in last["UpdateExpression"]

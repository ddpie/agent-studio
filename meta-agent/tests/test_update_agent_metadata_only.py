"""Regression test: update_agent must not crash on metadata-only updates.

Caught 2026-04-24 when sync_agent_skill → update_agent hand-off raised
``UnboundLocalError: local variable 'tool_names_list' referenced before
assignment``. tool_names_list was initialized only inside the
``if needs_redeploy:`` branch but referenced unconditionally at the end
of the function when building the DDB update expression.

This test stands up the minimum mocks needed to drive update_agent
through a description-only change — no system_prompt, no tool_names,
no template_id — so needs_redeploy stays False and the function takes
the path that previously crashed.
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


def test_update_agent_metadata_only_does_not_crash_on_tool_names_list():
    """Description-only update must succeed; prior to the fix this raised
    UnboundLocalError because tool_names_list wasn't initialized before
    the final DDB update_item built its expression."""
    meta = _existing_metadata()

    class _FakeTable:
        """DDB table stub — drives both agents + metadata update paths."""
        def __init__(self):
            self.update_calls: list[dict] = []
        def update_item(self, **kwargs):
            self.update_calls.append(kwargs)

    fake_table = _FakeTable()
    fake_s3 = MagicMock()
    # metadata.json round-trip
    fake_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps(meta).encode("utf-8")),
    }

    def _boto3_client(service, **_):
        if service == "s3":
            return fake_s3
        return MagicMock()

    ddb_resource = MagicMock()
    ddb_resource.Table = MagicMock(return_value=fake_table)

    # ensure_agent_in_workspace is imported lazily inside update_agent,
    # so we patch it on tools._scope (the import source) rather than on
    # tools.update_agent (the call site — the name isn't bound there at
    # module load time).
    with patch("tools.update_agent.boto3.client", side_effect=_boto3_client), \
         patch("tools.update_agent.boto3.resource", return_value=ddb_resource), \
         patch("tools._scope.ensure_agent_in_workspace",
               return_value=({"agentId": AGENT_ID, "workspace_id": WS_ID, "agentName": "DataAnalyst"}, None)):
        out = json.loads(_ua_mod.update_agent(
            agent_id=AGENT_ID,
            agent_name="DataAnalyst",
            description="new description",
        ))

    # Previously raised UnboundLocalError — if it returns at all the fix
    # is working. Assert the result shape is consistent with a metadata-
    # only update (no redeploy triggered).
    assert "error" not in out, f"unexpected error: {out}"
    assert out.get("action") == "metadata_updated"
    assert out.get("needs_redeploy") is False
    # DDB update must have fired for description but must NOT include
    # tool_names on a metadata-only path (initialized to [] and skipped
    # via `if tool_names_list:`).
    assert fake_table.update_calls, "expected a DDB update_item call"
    last = fake_table.update_calls[-1]
    assert "description = :desc" in last["UpdateExpression"]
    assert ":tn" not in last.get("ExpressionAttributeValues", {})

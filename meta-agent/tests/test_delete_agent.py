"""Tests for delete_agent / restore_agent / purge_agent."""

import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Module stubs ───────────────────────────────────────────────────────────
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
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "AGENT_ROLE_ARN": "arn:aws:iam::000:role/r",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
    "SUB_AGENT_BASE_DEPLOYMENT_KEY": "base/agent-deployment.zip",
    "SUB_AGENT_ROLE_ARN": "arn:aws:iam::000:role/sub",
    "PERMISSION_TIER_ROLES": {"readonly": "arn:aws:iam::000:role/sub"},
    "DEFAULT_PERMISSION_TIER": "readonly",
    "TOOLS_TABLE": "agent-studio-tools",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope

    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-1", raising=False)


def _grant_membership(
    monkeypatch, role="admin", agent_status="active", agent_workspace="ws-1", agent_name="myAgent"
):
    """Configure scope so ensure_agent_in_workspace passes for given role."""
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": role}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    agents_table = MagicMock()
    agents_table.get_item.return_value = {
        "Item": {
            "agentId": "a-1",
            "workspace_id": agent_workspace,
            "status": agent_status,
            "agentName": agent_name,
        }
    }
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)


# ── delete_agent (archive) ────────────────────────────────────────────────


def test_delete_agent_denies_non_admin(monkeypatch):
    """ROLE_ADMIN required to archive."""
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="editor")

    out = json.loads(mod.delete_agent("a-1"))
    assert "error" in out
    assert "admin" in out["error"].lower() or "permission" in out["error"].lower()


def test_delete_agent_rejects_already_archived(monkeypatch):
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin", agent_status="archived")

    out = json.loads(mod.delete_agent("a-1"))
    assert out["error"] == "Agent is already archived"


def test_delete_agent_archive_happy_path(monkeypatch):
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin")

    monkeypatch.setattr(mod, "delete_runtime", lambda aid: None)

    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.delete_agent("a-1"))

    assert out["action"] == "archived"
    assert out["status"] == "archived"
    fake_table.update_item.assert_called_once()
    update_kwargs = fake_table.update_item.call_args.kwargs
    assert update_kwargs["Key"] == {"agentId": "a-1"}
    assert update_kwargs["ExpressionAttributeValues"][":val"] == "archived"


def test_delete_agent_swallows_benign_runtime_errors(monkeypatch):
    """AccessDeniedException / ResourceNotFoundException = placeholder agent."""
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin")

    monkeypatch.setattr(mod, "delete_runtime", MagicMock(side_effect=Exception("ResourceNotFoundException")))

    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.delete_agent("a-1"))

    assert out["action"] == "archived"
    assert "note" in out
    assert "never deployed" in out["note"]


def test_delete_agent_propagates_non_benign_runtime_errors(monkeypatch):
    """Other exceptions abort with error JSON."""
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin")

    monkeypatch.setattr(mod, "delete_runtime", MagicMock(side_effect=Exception("ThrottlingException: rate")))

    out = json.loads(mod.delete_agent("a-1"))
    assert "error" in out
    assert "Failed to delete runtime" in out["error"]


# ── restore_agent ─────────────────────────────────────────────────────────


def test_restore_agent_denies_non_admin(monkeypatch):
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="editor", agent_status="archived")

    out = json.loads(mod.restore_agent("a-1"))
    assert "error" in out
    assert "admin" in out["error"].lower() or "permission" in out["error"].lower()


def test_restore_agent_rejects_active_agent(monkeypatch):
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin", agent_status="active")

    out = json.loads(mod.restore_agent("a-1"))
    assert out["error"] == "Agent is not archived, cannot restore"


def test_restore_agent_rejects_when_no_zip(monkeypatch):
    """When neither agentId nor name path has deployment.zip, error."""
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin", agent_status="archived")

    fake_s3 = MagicMock()
    fake_s3.head_object.side_effect = Exception("NoSuchKey")

    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.client", return_value=fake_s3), patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.restore_agent("a-1"))

    assert "error" in out
    assert "deployment.zip" in out["error"]


def test_restore_agent_happy_path(monkeypatch):
    """Recreates runtime, copies metadata, swaps DDB record."""
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin", agent_status="archived")

    fake_s3 = MagicMock()
    fake_s3.head_object.return_value = {}
    body = MagicMock()
    body.read.return_value = json.dumps({"description": "restored"}).encode()
    fake_s3.get_object.return_value = {"Body": body}

    monkeypatch.setattr(mod, "create_runtime", lambda *a, **kw: {"agent_id": "rt-NEW"})
    monkeypatch.setattr(mod, "wait_for_ready", lambda aid: "READY")

    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.client", return_value=fake_s3), patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.restore_agent("a-1"))

    assert out["action"] == "restored"
    assert out["new_agent_id"] == "rt-NEW"
    assert out["old_agent_id"] == "a-1"
    assert out["status"] == "READY"
    # DDB old record deleted, new record put
    fake_table.delete_item.assert_called_once_with(Key={"agentId": "a-1"})
    fake_table.put_item.assert_called_once()
    new_item = fake_table.put_item.call_args.kwargs["Item"]
    assert new_item["agentId"] == "rt-NEW"
    assert new_item["status"] == "active"


# ── purge_agent ───────────────────────────────────────────────────────────


def test_purge_agent_requires_owner(monkeypatch):
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="admin", agent_status="archived")

    out = json.loads(mod.purge_agent("a-1"))
    assert "error" in out
    # admin < owner
    assert "owner" in out["error"].lower() or "permission" in out["error"].lower()


def test_purge_agent_rejects_active(monkeypatch):
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="owner", agent_status="active")

    out = json.loads(mod.purge_agent("a-1"))
    assert "error" in out
    assert "archived" in out["error"]


def test_purge_agent_happy_path(monkeypatch):
    """Deletes S3 prefix + DDB record."""
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="owner", agent_status="archived", agent_name="myAgent")

    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.return_value = {
        "Contents": [{"Key": "agents/a-1/file1"}, {"Key": "agents/a-1/file2"}],
    }

    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.client", return_value=fake_s3), patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.purge_agent("a-1"))

    assert out["action"] == "purged"
    assert out["status"] == "permanently_deleted"
    # delete_object should be called for each file in both prefixes
    assert fake_s3.delete_object.call_count >= 2
    fake_table.delete_item.assert_called_once_with(Key={"agentId": "a-1"})


def test_purge_agent_tolerates_s3_errors(monkeypatch):
    """S3 listing errors are swallowed; DDB cleanup still runs."""
    from tools import delete_agent as mod

    _grant_membership(monkeypatch, role="owner", agent_status="archived")

    fake_s3 = MagicMock()
    fake_s3.list_objects_v2.side_effect = Exception("AccessDenied")

    fake_table = MagicMock()
    fake_resource = MagicMock()
    fake_resource.Table.return_value = fake_table

    with patch("boto3.client", return_value=fake_s3), patch("boto3.resource", return_value=fake_resource):
        out = json.loads(mod.purge_agent("a-1"))

    assert out["action"] == "purged"
    fake_table.delete_item.assert_called_once()

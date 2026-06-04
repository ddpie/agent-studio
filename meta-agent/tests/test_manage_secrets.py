"""Tests for set_agent_secrets / list_agent_secrets / delete_agent_secret."""
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
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-1", raising=False)


def _grant_membership(monkeypatch, role="admin", agent_workspace="ws-1"):
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": role}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    agents_table = MagicMock()
    agents_table.get_item.return_value = {
        "Item": {"agentId": "a-1", "workspace_id": agent_workspace},
    }
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)


# ── Pure helper tests ─────────────────────────────────────────────────────

def test_validate_key_rejects_empty():
    from tools.manage_secrets import _validate_key
    assert _validate_key("") == "key is required"


def test_validate_key_rejects_too_long():
    from tools.manage_secrets import _validate_key
    err = _validate_key("A" * 65)
    assert "64 characters" in err


def test_validate_key_rejects_lowercase():
    from tools.manage_secrets import _validate_key
    err = _validate_key("api_key")
    assert "[A-Z0-9_]" in err


def test_validate_key_rejects_dash():
    from tools.manage_secrets import _validate_key
    err = _validate_key("MY-KEY")
    assert "[A-Z0-9_]" in err


def test_validate_key_accepts_valid():
    from tools.manage_secrets import _validate_key
    assert _validate_key("API_KEY_1") == ""
    assert _validate_key("FEISHU_USER_ACCESS_TOKEN") == ""


def test_secret_path_layout():
    from tools.manage_secrets import _secret_path
    assert _secret_path("ws-1", "a-1") == "agent-studio/ws-1/a-1"
    assert _secret_path("ws-1", "a-1", "FOO") == "agent-studio/ws-1/a-1/FOO"


# ── set_agent_secrets ─────────────────────────────────────────────────────

def test_set_agent_secrets_denies_non_admin(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="editor")  # editor < admin

    out = json.loads(mod.set_agent_secrets("a-1", '{"K": "v"}'))
    assert "error" in out
    assert "admin" in out["error"].lower() or "permission" in out["error"].lower()


def test_set_agent_secrets_invalid_json(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    out = json.loads(mod.set_agent_secrets("a-1", "not-json{"))
    assert out["error"] == "Invalid JSON for secrets"


def test_set_agent_secrets_rejects_non_dict(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    out = json.loads(mod.set_agent_secrets("a-1", '["a", "b"]'))
    assert "error" in out
    assert "non-empty JSON object" in out["error"]


def test_set_agent_secrets_rejects_empty_dict(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    out = json.loads(mod.set_agent_secrets("a-1", "{}"))
    assert "error" in out


def test_set_agent_secrets_happy_path(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    fake_sm = MagicMock()
    # Make exceptions accessible like real boto3 client
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.set_agent_secrets("a-1", '{"API_KEY": "xyz"}'))

    assert out["status"] == "saved"
    assert out["saved"] == ["API_KEY"]
    assert out["errors"] == []
    fake_sm.put_secret_value.assert_called_once()
    call_kwargs = fake_sm.put_secret_value.call_args.kwargs
    assert call_kwargs["SecretId"] == "agent-studio/ws-1/a-1/API_KEY"
    assert call_kwargs["SecretString"] == "xyz"


def test_set_agent_secrets_creates_when_not_found(monkeypatch):
    """If put fails with ResourceNotFoundException, falls through to create."""
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    fake_sm = MagicMock()
    rnf = type("ResourceNotFoundException", (Exception,), {})
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = rnf
    fake_sm.put_secret_value.side_effect = rnf("not found")

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.set_agent_secrets("a-1", '{"NEW_KEY": "value"}'))

    assert out["status"] == "saved"
    fake_sm.create_secret.assert_called_once()


def test_set_agent_secrets_validates_keys(monkeypatch):
    """Invalid keys end up in errors, not saved."""
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    fake_sm = MagicMock()
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.set_agent_secrets(
            "a-1", '{"GOOD_KEY": "v", "bad-key": "v"}',
        ))

    assert "GOOD_KEY" in out["saved"]
    assert any(e["key"] == "bad-key" for e in out["errors"])
    assert out["status"] == "partial"


def test_set_agent_secrets_rejects_long_value(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    fake_sm = MagicMock()
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )

    huge = "x" * 5000
    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.set_agent_secrets(
            "a-1", json.dumps({"BIG": huge}),
        ))
    assert out["status"] == "failed"
    assert out["errors"][0]["error"].startswith("value exceeds")


def test_set_agent_secrets_rejects_non_string_value(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    fake_sm = MagicMock()
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.set_agent_secrets("a-1", '{"K": 123}'))

    assert out["status"] == "failed"
    assert out["errors"][0]["key"] == "K"


# ── list_agent_secrets ────────────────────────────────────────────────────

def test_list_agent_secrets_denies_viewer(monkeypatch):
    """list_agent_secrets requires editor or higher."""
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="viewer")

    out = json.loads(mod.list_agent_secrets("a-1"))
    assert "error" in out
    assert "permission" in out["error"].lower() or "editor" in out["error"].lower()


def test_list_agent_secrets_happy_path(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="editor")

    prefix = "agent-studio/ws-1/a-1/"
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {"SecretList": [
            {"Name": prefix + "API_KEY"},
            {"Name": prefix + "DB_PASSWORD"},
            {"Name": prefix + "nested/skip"},  # has slash → skip
            {"Name": "agent-studio/other-ws/a-1/SKIP_ME"},  # different prefix
        ]},
    ]
    fake_sm = MagicMock()
    fake_sm.get_paginator.return_value = paginator

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.list_agent_secrets("a-1"))

    assert out["agent_id"] == "a-1"
    assert out["keys"] == ["API_KEY", "DB_PASSWORD"]


def test_list_agent_secrets_handles_failure(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="editor")

    fake_sm = MagicMock()
    fake_sm.get_paginator.side_effect = Exception("boom")

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.list_agent_secrets("a-1"))

    assert "error" in out
    assert "list_secrets failed" in out["error"]


# ── delete_agent_secret ───────────────────────────────────────────────────

def test_delete_agent_secret_denies_editor(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="editor")  # need admin

    out = json.loads(mod.delete_agent_secret("a-1", "API_KEY"))
    assert "error" in out
    assert "admin" in out["error"].lower() or "permission" in out["error"].lower()


def test_delete_agent_secret_validates_key(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    out = json.loads(mod.delete_agent_secret("a-1", "bad key"))
    assert "error" in out
    assert "[A-Z0-9_]" in out["error"]


def test_delete_agent_secret_happy_path(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    fake_sm = MagicMock()
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.delete_agent_secret("a-1", "API_KEY"))

    assert out["status"] == "deleted"
    assert out["key"] == "API_KEY"
    fake_sm.delete_secret.assert_called_once()
    kwargs = fake_sm.delete_secret.call_args.kwargs
    assert kwargs["SecretId"] == "agent-studio/ws-1/a-1/API_KEY"
    assert kwargs["ForceDeleteWithoutRecovery"] is True


def test_delete_agent_secret_not_found(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    rnf = type("ResourceNotFoundException", (Exception,), {})
    fake_sm = MagicMock()
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = rnf
    fake_sm.delete_secret.side_effect = rnf("nope")

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.delete_agent_secret("a-1", "API_KEY"))

    assert "error" in out
    assert "not found" in out["error"].lower()


def test_delete_agent_secret_other_error(monkeypatch):
    from tools import manage_secrets as mod
    _grant_membership(monkeypatch, role="admin")

    fake_sm = MagicMock()
    fake_sm.exceptions = MagicMock()
    fake_sm.exceptions.ResourceNotFoundException = type(
        "ResourceNotFoundException", (Exception,), {}
    )
    fake_sm.delete_secret.side_effect = Exception("AccessDenied")

    with patch("boto3.client", return_value=fake_sm):
        out = json.loads(mod.delete_agent_secret("a-1", "API_KEY"))

    assert "error" in out
    assert "delete failed" in out["error"]

"""Tests for crud/meta_agent.py — synthetic AgentCard endpoint."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    """Patch META_AGENT_ARN at the module level instead of reloading.

    Reloading crud.meta_agent + crud.handler rebuilds the global app object,
    which orphans every other router that was already include_router'd at
    import time — that broke 95 unrelated tests across test_mcp.py / etc.
    """
    arn = "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/agentStudioMeta-Test1"
    import crud.meta_agent as _m

    monkeypatch.setattr(_m, "META_AGENT_ARN", arn)


def _card_event(workspace_id: str):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{workspace_id}/meta-agent/agent-card",
        "resource": "/api/workspaces/{wsId}/meta-agent/agent-card",
        "pathParameters": {"wsId": workspace_id},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_get_meta_agent_card_returns_synthetic_card(mock_jwt, user_id, workspace_id):
    """Synthesizes an A2A-shape card from GetAgentRuntime metadata."""
    import crud.handler as _h

    app = _h.app
    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = {
        "agentRuntimeName": "agentStudioMeta",
        "agentRuntimeId": "agentStudioMeta-Test1",
        "agentRuntimeVersion": "42",
        "status": "READY",
        "agentRuntimeArn": "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/agentStudioMeta-Test1",
    }
    with (
        patch("crud.meta_agent._get_control", return_value=fake_control),
        patch("crud.meta_agent.auth_check") as auth,
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(_card_event(workspace_id), MagicMock())

    assert resp["statusCode"] == 200
    card = json.loads(resp["body"])
    assert card["name"] == "agentStudioMeta"
    assert card["version"] == "42"
    # A2A-shape fields
    assert "url" in card
    assert card["url"].startswith("https://bedrock-agentcore.us-east-1.amazonaws.com/")
    assert "skills" in card
    assert isinstance(card["skills"], list)
    assert len(card["skills"]) > 0
    # Must expose the runtime ARN so the UI can copy it.
    assert card["runtimeArn"].endswith("/agentStudioMeta-Test1")


def test_get_meta_agent_card_400_when_arn_missing(mock_jwt, user_id, workspace_id, monkeypatch):
    """Empty META_AGENT_ARN → 400 with actionable message."""
    import crud.handler as _h
    import crud.meta_agent as _m

    # Override the module-level constant for just this test (autouse fixture
    # already set a non-empty value; we override here without reload).
    monkeypatch.setattr(_m, "META_AGENT_ARN", "")

    with patch("crud.meta_agent.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_card_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 400


def test_get_meta_agent_card_requires_auth():
    """Unauth caller → forbidden."""
    import crud.handler as _h
    from shared.response import forbidden

    with patch("crud.meta_agent.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = _h.app.resolve(
            _card_event("any-ws"),
            MagicMock(),
        )
    assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


def _status_event(workspace_id: str):
    return {
        "httpMethod": "GET",
        "path": f"/api/workspaces/{workspace_id}/meta-agent/status",
        "resource": "/api/workspaces/{wsId}/meta-agent/status",
        "pathParameters": {"wsId": workspace_id},
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": None,
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_get_status_returns_runtime_status(mock_jwt, user_id, workspace_id):
    from datetime import datetime, timezone

    import crud.handler as _h

    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = {
        "status": "READY",
        "lastUpdatedAt": datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc),
    }
    with (
        patch("crud.meta_agent._get_control", return_value=fake_control),
        patch("crud.meta_agent.auth_check") as auth,
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_status_event(workspace_id), MagicMock())

    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["status"] == "READY"
    assert data["lastUpdated"] is not None


def test_get_status_falls_back_to_created_at(mock_jwt, user_id, workspace_id):
    from datetime import datetime, timezone

    import crud.handler as _h

    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = {
        "status": "READY",
        # No lastUpdatedAt → fallback to createdAt
        "createdAt": datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc),
    }
    with (
        patch("crud.meta_agent._get_control", return_value=fake_control),
        patch("crud.meta_agent.auth_check") as auth,
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["lastUpdated"] is not None


def test_get_status_unknown_status_when_missing_keys(mock_jwt, user_id, workspace_id):
    """If status missing from runtime metadata, use 'UNKNOWN' and null lastUpdated."""
    import crud.handler as _h

    fake_control = MagicMock()
    fake_control.get_agent_runtime.return_value = {}
    with (
        patch("crud.meta_agent._get_control", return_value=fake_control),
        patch("crud.meta_agent.auth_check") as auth,
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["status"] == "UNKNOWN"
    assert data["lastUpdated"] is None


def test_get_status_resource_not_found(mock_jwt, user_id, workspace_id):
    """ResourceNotFoundException → 200 with status NOT_FOUND."""
    from botocore.exceptions import ClientError

    import crud.handler as _h

    fake_control = MagicMock()
    fake_control.get_agent_runtime.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "no runtime"}},
        "GetAgentRuntime",
    )
    with (
        patch("crud.meta_agent._get_control", return_value=fake_control),
        patch("crud.meta_agent.auth_check") as auth,
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["status"] == "NOT_FOUND"
    assert data["lastUpdated"] is None


def test_get_status_other_clienterror_returns_500(mock_jwt, user_id, workspace_id):
    """Generic ClientError → 500."""
    from botocore.exceptions import ClientError

    import crud.handler as _h

    fake_control = MagicMock()
    fake_control.get_agent_runtime.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetAgentRuntime",
    )
    with (
        patch("crud.meta_agent._get_control", return_value=fake_control),
        patch("crud.meta_agent.auth_check") as auth,
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 500


def test_get_status_when_arn_not_configured(mock_jwt, user_id, workspace_id, monkeypatch):
    monkeypatch.setenv("META_AGENT_ARN", "")
    import importlib

    import shared.config as _cfg

    importlib.reload(_cfg)
    import crud.meta_agent as _m

    importlib.reload(_m)
    import crud.handler as _h

    importlib.reload(_h)
    with patch("crud.meta_agent.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_status_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 200
    data = json.loads(resp["body"])
    assert data["status"] == "NOT_CONFIGURED"
    assert data["lastUpdated"] is None


def test_get_status_requires_auth():
    import crud.handler as _h
    from shared.response import forbidden

    with patch("crud.meta_agent.auth_check") as auth:
        auth.return_value = (None, None, None, forbidden())
        resp = _h.app.resolve(_status_event("any-ws"), MagicMock())
    assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# Card endpoint additional coverage
# ---------------------------------------------------------------------------


def test_get_card_clienterror_returns_500(mock_jwt, user_id, workspace_id):
    from botocore.exceptions import ClientError

    import crud.handler as _h

    fake_control = MagicMock()
    fake_control.get_agent_runtime.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
        "GetAgentRuntime",
    )
    with (
        patch("crud.meta_agent._get_control", return_value=fake_control),
        patch("crud.meta_agent.auth_check") as auth,
    ):
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = _h.app.resolve(_card_event(workspace_id), MagicMock())
    assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# Lazy initialization + helpers
# ---------------------------------------------------------------------------


def test_get_control_lazy_initializes(monkeypatch):
    import crud.meta_agent as _m

    _m._control = None
    sentinel = object()
    monkeypatch.setattr(_m.boto3, "client", lambda *a, **kw: sentinel)
    assert _m._get_control() is sentinel
    # Cached on second call
    assert _m._get_control() is sentinel


def test_extract_runtime_id_handles_missing_slash():
    """ARN without runtime/<id> structure should produce empty id."""
    from crud.meta_agent import _extract_runtime_id

    assert _extract_runtime_id("arn:aws:bedrock-agentcore:us-east-1:000:runtime/myId") == "myId"
    # Single segment
    assert _extract_runtime_id("nothing") == ""


def test_build_invocation_url_encodes_arn():
    from crud.meta_agent import _build_invocation_url

    arn = "arn:aws:bedrock-agentcore:us-east-1:000:runtime/myId"
    url = _build_invocation_url(arn, "us-east-1")
    # ARN colons and slashes are URL-encoded
    assert "%3A" in url  # ":" encoded
    assert "%2F" in url  # "/" encoded
    assert url.startswith("https://bedrock-agentcore.us-east-1.amazonaws.com/")
    assert url.endswith("/invocations")

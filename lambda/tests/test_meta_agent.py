"""Tests for crud/meta_agent.py — synthetic AgentCard endpoint."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv(
        "META_AGENT_ARN",
        "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/agentStudioMeta-Test1",
    )
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    # META_AGENT_ARN is captured at module-import time in crud.meta_agent;
    # reload both meta_agent and handler so the router re-binds.
    import crud.meta_agent as _m
    importlib.reload(_m)
    import crud.handler as _h
    importlib.reload(_h)


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
    with patch("crud.meta_agent._get_control", return_value=fake_control), \
         patch("crud.meta_agent.auth_check") as auth:
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
    monkeypatch.setenv("META_AGENT_ARN", "")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    # Reload meta_agent so its module-level import of META_AGENT_ARN picks up the empty value
    import crud.meta_agent as _m
    importlib.reload(_m)
    # crud.handler caches the router reference; re-register to use the reloaded router
    import crud.handler as _h
    importlib.reload(_h)

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

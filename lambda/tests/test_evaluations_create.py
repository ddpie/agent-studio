"""Tests: workspace creation triggers OnlineEvaluationConfig creation."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "arn:aws:iam::000000000000:role/test-evaluator")
    monkeypatch.setenv("SPANS_LOG_GROUP", "aws/spans")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)


@pytest.fixture
def ws_event():
    return {
        "httpMethod": "POST",
        "path": "/api/workspaces",
        "resource": "/api/workspaces",
        "pathParameters": None,
        "headers": {"Authorization": "Bearer test-token"},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "body": json.dumps({"name": "Test WS"}),
        "isBase64Encoded": False,
        "queryStringParameters": None,
    }


def test_create_workspace_fires_eval_config(mock_jwt, user_id, ws_event):
    """POST /workspaces triggers create_eval_config_for_workspace."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.name = "test-workspaces"
    fake_table.meta.client.transact_write_items.return_value = {}
    with patch("crud.workspaces._get_table", return_value=fake_table), \
         patch("crud.evaluations.create_eval_config_for_workspace") as mock_fire:
        mock_fire.return_value = "agentstudio_ws_abc"
        resp = app.resolve(ws_event, MagicMock())

    assert resp["statusCode"] in (200, 201)
    mock_fire.assert_called_once()
    # Accept either positional or keyword; assert the ws id was passed
    body = json.loads(resp["body"])
    expected_ws_id = body["workspaceId"]
    args = mock_fire.call_args.args
    kwargs = mock_fire.call_args.kwargs
    passed = kwargs.get("workspace_id") or (args[0] if args else None)
    assert passed == expected_ws_id


def test_eval_config_failure_does_not_block_workspace_create(mock_jwt, user_id, ws_event):
    """If eval config provisioning raises, workspace create still returns 201."""
    from crud.handler import app
    fake_table = MagicMock()
    fake_table.name = "test-workspaces"
    fake_table.meta.client.transact_write_items.return_value = {}
    with patch("crud.workspaces._get_table", return_value=fake_table), \
         patch("crud.evaluations.create_eval_config_for_workspace",
               side_effect=RuntimeError("boom")):
        resp = app.resolve(ws_event, MagicMock())
    assert resp["statusCode"] in (200, 201)


def test_create_eval_config_idempotent():
    """If eval config already exists (repeat workspace create on retry),
    don't fail — detect existing and return its name."""
    from botocore.exceptions import ClientError
    from crud.evaluations import create_eval_config_for_workspace

    with patch("crud.evaluations._get_control") as mock_c:
        client = MagicMock()
        client.create_online_evaluation_config.side_effect = ClientError(
            {"Error": {"Code": "ConflictException", "Message": "already exists"}},
            "CreateOnlineEvaluationConfig",
        )
        mock_c.return_value = client
        name = create_eval_config_for_workspace(workspace_id="abc-123")
    assert name.startswith("agentstudio_ws_")


def test_create_eval_config_no_role_returns_empty(monkeypatch):
    """If EVALUATOR_ROLE_ARN is empty, skip creation and return ''."""
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    # Also reload evaluations to pick up the empty value
    import crud.evaluations as _ev
    importlib.reload(_ev)
    name = _ev.create_eval_config_for_workspace(workspace_id="abc-123")
    assert name == ""

"""Tests for lambda/crud/chat.py."""

import json
import uuid
from unittest.mock import MagicMock, patch


def _event(ws_id: str, method: str, path_tail: str, *, path_params=None, body=None):
    """Build an APIGW event. ``path_tail`` starts after /api/workspaces/{ws}."""
    full_path = f"/api/workspaces/{ws_id}{path_tail}"
    pp = {"wsId": ws_id, **(path_params or {})}
    return {
        "httpMethod": method,
        "path": full_path,
        "resource": full_path,
        "pathParameters": pp,
        "queryStringParameters": None,
        "headers": {"Authorization": "Bearer tok", "x-origin-verify": ""},
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "isBase64Encoded": False,
    }


def _fake_s3_client():
    """S3 client mock with NoSuchKey exception surface."""
    client = MagicMock()
    client.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
    return client


def _stable_ids():
    return str(uuid.uuid4()), str(uuid.uuid4())


def test_put_session_persists_under_caller_path():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "PUT",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
        body={
            "id": sess_id,
            "agentKey": "agt1",
            "title": "hi",
            "messages": [{"role": "user", "content": "hello", "timestamp": 1}],
        },
    )

    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200, resp["body"]
    fake.put_object.assert_called_once()
    kwargs = fake.put_object.call_args.kwargs
    assert kwargs["Key"] == f"chat/{workspace_id}/{user_id}/agt1/sessions/{sess_id}.json"
    assert kwargs["Bucket"] == "test-assets"


def test_put_session_ignores_tampered_identity_fields():
    """Caller cannot poison id/agentKey by putting foreign values in the body."""
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "PUT",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
        body={
            "id": "evil-id",
            "agentKey": "someone-elses-agent",
            "title": "poison",
            "messages": [{"role": "user", "content": "x", "timestamp": 1}],
        },
    )

    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    kwargs = fake.put_object.call_args.kwargs
    # Key is built from URL path + JWT user, never from the body
    assert kwargs["Key"] == f"chat/{workspace_id}/{user_id}/agt1/sessions/{sess_id}.json"
    written = json.loads(kwargs["Body"].decode("utf-8"))
    assert written["id"] == sess_id
    assert written["agentKey"] == "agt1"


def test_get_session_404_when_missing():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    fake.get_object.side_effect = fake.exceptions.NoSuchKey("not here")
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "GET",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 404


def test_get_session_returns_body():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex
    session_body = {
        "id": sess_id,
        "agentKey": "agt1",
        "title": "hi",
        "messages": [{"role": "user", "content": "hello", "timestamp": 1}],
        "createdAt": 1000,
        "updatedAt": 1001,
    }
    fake.get_object.return_value = {
        "Body": MagicMock(read=MagicMock(return_value=json.dumps(session_body).encode("utf-8"))),
    }

    event = _event(
        workspace_id,
        "GET",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["id"] == sess_id
    get_key = fake.get_object.call_args.kwargs["Key"]
    assert get_key == f"chat/{workspace_id}/{user_id}/agt1/sessions/{sess_id}.json"


def test_list_sessions_sorted_and_summarised():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()

    older = {
        "id": "s1",
        "agentKey": "agt1",
        "title": "old",
        "createdAt": 1,
        "updatedAt": 1,
        "messages": [{"role": "user", "content": "first message", "timestamp": 1}],
    }
    newer = {
        "id": "s2",
        "agentKey": "agt1",
        "title": "new",
        "createdAt": 2,
        "updatedAt": 2,
        "messages": [{"role": "user", "content": "second", "timestamp": 2}],
    }
    paginator = MagicMock()
    paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": f"chat/{workspace_id}/{user_id}/agt1/sessions/s1.json"},
                {"Key": f"chat/{workspace_id}/{user_id}/agt1/sessions/s2.json"},
            ]
        }
    ]
    fake.get_paginator.return_value = paginator

    def _get(Bucket, Key):
        body = older if Key.endswith("s1.json") else newer
        return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(body).encode("utf-8")))}

    fake.get_object.side_effect = _get

    event = _event(
        workspace_id,
        "GET",
        "/chat/agents/agt1/sessions",
        path_params={"agentKey": "agt1"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    items = json.loads(resp["body"])["items"]
    assert [s["id"] for s in items] == ["s2", "s1"]
    assert items[0]["preview"] == "second"
    assert items[1]["messageCount"] == 1


def test_delete_session_calls_delete_object():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "DELETE",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 200
    kwargs = fake.delete_object.call_args.kwargs
    assert kwargs["Key"] == f"chat/{workspace_id}/{user_id}/agt1/sessions/{sess_id}.json"


def test_non_member_gets_forbidden():
    """Non-members are short-circuited by auth_check before S3 is touched."""
    from crud.handler import app

    fake = _fake_s3_client()
    workspace_id = str(uuid.uuid4())

    event = _event(
        workspace_id,
        "GET",
        "/chat/agents/agt1/sessions",
        path_params={"agentKey": "agt1"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        from shared.response import forbidden

        auth.return_value = (None, None, None, forbidden())
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 403
    fake.get_paginator.assert_not_called()


def test_viewer_cannot_write():
    """auth_check is invoked with min_role='editor' on PUT — simulate its rejection."""
    from crud.handler import app

    fake = _fake_s3_client()
    workspace_id = str(uuid.uuid4())
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "PUT",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
        body={"messages": []},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        from shared.response import forbidden

        def _auth(event, min_role="viewer", ws_id=""):
            if min_role == "editor":
                return (None, None, None, forbidden())
            return ("u", ws_id, {"role": "viewer"}, None)

        auth.side_effect = _auth
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 403
    fake.put_object.assert_not_called()


def test_put_rejects_oversized_body():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex

    huge_content = "x" * (4 * 1024 * 1024 + 1000)
    event = _event(
        workspace_id,
        "PUT",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
        body={"messages": [{"role": "user", "content": huge_content, "timestamp": 1}]},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())

    assert resp["statusCode"] == 400
    fake.put_object.assert_not_called()


# ---------------------------------------------------------------------------
# Lazy initializer
# ---------------------------------------------------------------------------


def test_get_s3_lazy_initializes(monkeypatch):
    """Cover the `if _s3 is None: _s3 = boto3.client(...)` path."""
    import crud.chat as chat_mod

    chat_mod._s3 = None
    sentinel = object()
    monkeypatch.setattr(chat_mod.boto3, "client", lambda *a, **kw: sentinel)
    result = chat_mod._get_s3()
    assert result is sentinel
    # Cached on second call
    assert chat_mod._get_s3() is sentinel


# ---------------------------------------------------------------------------
# Invalid IDs
# ---------------------------------------------------------------------------


def test_list_invalid_agent_key():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()

    event = _event(
        workspace_id,
        "GET",
        "/chat/agents/bad agent!/sessions",
        path_params={"agentKey": "bad agent!"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_list_paginator_exception_returns_500():
    """An exception during paginator iteration → 500."""
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    fake.get_paginator.side_effect = Exception("boom")

    event = _event(
        workspace_id,
        "GET",
        "/chat/agents/agt1/sessions",
        path_params={"agentKey": "agt1"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_list_skips_unreadable_session():
    """When one session in S3 is unreadable, list keeps going."""
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()

    paginator = MagicMock()
    paginator.paginate.return_value = [
        {
            "Contents": [
                {"Key": f"chat/{workspace_id}/{user_id}/agt1/sessions/good.json"},
                {"Key": f"chat/{workspace_id}/{user_id}/agt1/sessions/bad.json"},
            ]
        }
    ]
    fake.get_paginator.return_value = paginator

    good = {
        "id": "good",
        "agentKey": "agt1",
        "title": "g",
        "createdAt": 1,
        "updatedAt": 2,
        "messages": [{"role": "user", "content": "ok"}],
    }

    def _get(Bucket, Key):
        if Key.endswith("good.json"):
            return {"Body": MagicMock(read=MagicMock(return_value=json.dumps(good).encode()))}
        raise Exception("unreadable")

    fake.get_object.side_effect = _get

    event = _event(
        workspace_id,
        "GET",
        "/chat/agents/agt1/sessions",
        path_params={"agentKey": "agt1"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 200
    items = json.loads(resp["body"])["items"]
    assert len(items) == 1
    assert items[0]["id"] == "good"


def test_get_session_invalid_agent_key():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()

    event = _event(
        workspace_id,
        "GET",
        "/chat/agents/bad key!/sessions/sess1",
        path_params={"agentKey": "bad key!", "sessionId": "sess1"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_get_session_other_exception():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    fake.get_object.side_effect = Exception("network")

    event = _event(
        workspace_id,
        "GET",
        "/chat/agents/agt1/sessions/sess1",
        path_params={"agentKey": "agt1", "sessionId": "sess1"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "viewer"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_put_session_invalid_id():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()

    event = _event(
        workspace_id,
        "PUT",
        "/chat/agents/bad!/sessions/sess1",
        path_params={"agentKey": "bad!", "sessionId": "sess1"},
        body={"messages": []},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_put_session_body_not_object():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex

    # Build a custom event where body is a JSON list (not a dict)
    event = _event(
        workspace_id,
        "PUT",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
    )
    event["body"] = json.dumps(["array", "not", "object"])

    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400
    assert "object" in json.loads(resp["body"])["error"].lower()


def test_put_session_messages_not_array():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "PUT",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
        body={"messages": "not-an-array"},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400
    assert "messages" in json.loads(resp["body"])["error"].lower()


def test_put_session_s3_failure():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    fake.put_object.side_effect = Exception("boom")
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "PUT",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
        body={"messages": [{"role": "user", "content": "x"}]},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_delete_session_invalid_agent_key():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "DELETE",
        f"/chat/agents/bad!/sessions/{sess_id}",
        path_params={"agentKey": "bad!", "sessionId": sess_id},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 400


def test_delete_session_s3_failure():
    from crud.handler import app

    user_id, workspace_id = _stable_ids()
    fake = _fake_s3_client()
    fake.delete_object.side_effect = Exception("boom")
    sess_id = uuid.uuid4().hex

    event = _event(
        workspace_id,
        "DELETE",
        f"/chat/agents/agt1/sessions/{sess_id}",
        path_params={"agentKey": "agt1", "sessionId": sess_id},
    )
    with patch("crud.chat._get_s3", return_value=fake), patch("crud.chat.auth_check") as auth:
        auth.return_value = (user_id, workspace_id, {"role": "editor"}, None)
        resp = app.resolve(event, MagicMock())
    assert resp["statusCode"] == 500


def test_summary_uses_first_non_empty_message():
    """_summary should pick the first non-empty content as preview."""
    from crud.chat import _summary

    sess = {
        "id": "s1",
        "agentKey": "a",
        "title": "t",
        "createdAt": 1,
        "updatedAt": 2,
        "messages": [
            {"role": "user", "content": ""},
            {"role": "user", "content": "  "},  # blank
            {"role": "user", "content": "real preview"},
        ],
    }
    summary = _summary(sess)
    assert summary["preview"] == "real preview"
    assert summary["messageCount"] == 3

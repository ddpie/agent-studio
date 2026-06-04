"""Tests for crud.kiro_key — per-workspace Kiro API key management."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Env / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_env(monkeypatch):
    monkeypatch.setenv("ORIGIN_VERIFY_VALUE", "")
    monkeypatch.setenv(
        "META_AGENT_ARN",
        "arn:aws:bedrock-agentcore:us-east-1:123456789012:agent-runtime/meta-X",
    )
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.kiro_key as _mod
    importlib.reload(_mod)


@pytest.fixture(autouse=True)
def reset_caches():
    """Wipe per-process caches between tests."""
    import crud.kiro_key as mod
    mod._identity_cache.clear()
    mod._usage_cache.clear()
    yield
    mod._identity_cache.clear()
    mod._usage_cache.clear()


@pytest.fixture
def mock_sm():
    with patch("crud.kiro_key._get_sm") as g:
        s = MagicMock()
        # Set up exception classes
        s.exceptions.ResourceNotFoundException = type(
            "ResourceNotFoundException", (Exception,), {}
        )
        s.exceptions.ResourceExistsException = type(
            "ResourceExistsException", (Exception,), {}
        )
        g.return_value = s
        yield s


@pytest.fixture
def mock_cognito():
    with patch("crud.kiro_key._get_cognito") as g:
        c = MagicMock()
        g.return_value = c
        yield c


@pytest.fixture
def mock_agentcore():
    with patch("crud.kiro_key._get_agentcore") as g:
        a = MagicMock()
        g.return_value = a
        yield a


@pytest.fixture
def _mock_admin(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "admin",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "viewer",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    with patch("shared.middleware.get_membership", return_value=None):
        yield


def _apigw(method, path, body=None, query_params=None):
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "requestContext": {
            "stage": "test",
            "requestId": "req-1",
            "identity": {"sourceIp": "127.0.0.1"},
        },
        "headers": {
            "Authorization": "Bearer X",
            "Content-Type": "application/json",
        },
        "body": json.dumps(body) if body else None,
        "queryStringParameters": query_params or {},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_secret_name_format(self):
        from crud.kiro_key import _secret_name
        assert _secret_name("ws1") == "agent-studio/workspaces/ws1/kiro-api-key"

    def test_region_from_describe_default(self):
        from crud.kiro_key import _region_from_describe
        assert _region_from_describe({}) == "us-east-1"

    def test_region_from_describe_us_east_1(self):
        from crud.kiro_key import _region_from_describe
        info = {"Tags": [{"Key": "kiroRegion", "Value": "us-east-1"}]}
        assert _region_from_describe(info) == "us-east-1"

    def test_region_from_describe_eu_central_1(self):
        from crud.kiro_key import _region_from_describe
        info = {"Tags": [{"Key": "kiroRegion", "Value": "eu-central-1"}]}
        assert _region_from_describe(info) == "eu-central-1"

    def test_region_from_describe_disallowed_falls_to_default(self):
        from crud.kiro_key import _region_from_describe
        info = {"Tags": [{"Key": "kiroRegion", "Value": "ap-south-1"}]}
        assert _region_from_describe(info) == "us-east-1"

    def test_region_from_describe_other_tag_ignored(self):
        from crud.kiro_key import _region_from_describe
        info = {"Tags": [{"Key": "purpose", "Value": "kiro-api-key"}]}
        assert _region_from_describe(info) == "us-east-1"

    def test_region_from_describe_no_tags(self):
        from crud.kiro_key import _region_from_describe
        assert _region_from_describe({"Tags": None}) == "us-east-1"

    def test_describe_returns_response(self, mock_sm):
        from crud.kiro_key import _describe
        mock_sm.describe_secret.return_value = {"ARN": "arn:..."}
        assert _describe("ws1") == {"ARN": "arn:..."}

    def test_describe_returns_none_on_not_found(self, mock_sm):
        from crud.kiro_key import _describe
        mock_sm.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DescribeSecret"
        )
        assert _describe("ws1") is None

    def test_describe_raises_on_other_error(self, mock_sm):
        from crud.kiro_key import _describe
        mock_sm.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError"}}, "DescribeSecret"
        )
        with pytest.raises(ClientError):
            _describe("ws1")


# ---------------------------------------------------------------------------
# _resolve_user_label
# ---------------------------------------------------------------------------


class TestResolveUserLabel:
    def test_empty_user_id_returns_input(self):
        from crud.kiro_key import _resolve_user_label
        assert _resolve_user_label("") == ""

    def test_no_pool_returns_input(self, monkeypatch):
        import crud.kiro_key as mod
        monkeypatch.setattr(mod, "COGNITO_USER_POOL_ID", "")
        assert mod._resolve_user_label("user-1") == "user-1"

    def test_returns_name_email_format(self, mock_cognito):
        import crud.kiro_key as mod
        mock_cognito.admin_get_user.return_value = {
            "UserAttributes": [
                {"Name": "name", "Value": "Alice"},
                {"Name": "email", "Value": "a@b.com"},
            ]
        }
        assert mod._resolve_user_label("user-1") == "Alice (a@b.com)"

    def test_returns_email_when_no_name(self, mock_cognito):
        import crud.kiro_key as mod
        mock_cognito.admin_get_user.return_value = {
            "UserAttributes": [{"Name": "email", "Value": "a@b.com"}]
        }
        assert mod._resolve_user_label("user-1") == "a@b.com"

    def test_returns_name_when_no_email(self, mock_cognito):
        import crud.kiro_key as mod
        mock_cognito.admin_get_user.return_value = {
            "UserAttributes": [{"Name": "name", "Value": "Alice"}]
        }
        assert mod._resolve_user_label("user-1") == "Alice"

    def test_falls_back_to_user_id_on_failure(self, mock_cognito):
        import crud.kiro_key as mod
        mock_cognito.admin_get_user.side_effect = Exception("cognito boom")
        assert mod._resolve_user_label("user-1") == "user-1"

    def test_caches_result(self, mock_cognito):
        import crud.kiro_key as mod
        mock_cognito.admin_get_user.return_value = {
            "UserAttributes": [{"Name": "email", "Value": "a@b.com"}]
        }
        assert mod._resolve_user_label("user-1") == "a@b.com"
        assert mod._resolve_user_label("user-1") == "a@b.com"
        # Cached on second call
        assert mock_cognito.admin_get_user.call_count == 1

    def test_empty_attrs_falls_back_to_user_id(self, mock_cognito):
        import crud.kiro_key as mod
        mock_cognito.admin_get_user.return_value = {"UserAttributes": []}
        assert mod._resolve_user_label("user-1") == "user-1"


# ---------------------------------------------------------------------------
# Lazy client init
# ---------------------------------------------------------------------------


class TestLazyClientInit:
    def test_get_sm_caches(self, monkeypatch):
        import crud.kiro_key as mod
        mod._sm = None
        sentinel = MagicMock()
        with patch("boto3.client", return_value=sentinel) as bc:
            assert mod._get_sm() is sentinel
            # Second call uses cached client
            assert mod._get_sm() is sentinel
            bc.assert_called_once()
        mod._sm = None

    def test_get_cognito_caches(self):
        import crud.kiro_key as mod
        mod._cognito = None
        sentinel = MagicMock()
        with patch("boto3.client", return_value=sentinel) as bc:
            assert mod._get_cognito() is sentinel
            assert mod._get_cognito() is sentinel
            bc.assert_called_once()
        mod._cognito = None

    def test_get_agentcore_caches(self):
        import crud.kiro_key as mod
        mod._agentcore = None
        sentinel = MagicMock()
        with patch("boto3.client", return_value=sentinel) as bc:
            assert mod._get_agentcore() is sentinel
            assert mod._get_agentcore() is sentinel
            bc.assert_called_once()
        mod._agentcore = None


# ---------------------------------------------------------------------------
# GET /kiro-key
# ---------------------------------------------------------------------------


class TestGetKiroKey:
    def test_returns_unconfigured_when_no_secret(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm
    ):
        mock_sm.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DescribeSecret"
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["configured"] is False
        assert body["lastUpdated"] is None
        assert body["updatedBy"] is None
        assert body["region"] == "us-east-1"

    def test_returns_configured(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm, mock_cognito
    ):
        ts = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
        mock_sm.describe_secret.return_value = {
            "Tags": [
                {"Key": "updatedBy", "Value": "user-X"},
                {"Key": "kiroRegion", "Value": "eu-central-1"},
            ],
            "LastChangedDate": ts,
        }
        mock_cognito.admin_get_user.return_value = {
            "UserAttributes": [{"Name": "email", "Value": "x@y.com"}]
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["configured"] is True
        assert body["lastUpdated"] == ts.isoformat()
        assert body["updatedBy"] == "x@y.com"
        assert body["region"] == "eu-central-1"

    def test_returns_configured_uses_created_date_fallback(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm
    ):
        ts = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)
        mock_sm.describe_secret.return_value = {
            "Tags": [],
            "CreatedDate": ts,
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["configured"] is True
        assert body["lastUpdated"] == ts.isoformat()
        assert body["updatedBy"] is None

    def test_returns_500_on_describe_other_error(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm
    ):
        mock_sm.describe_secret.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError"}}, "DescribeSecret"
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 500

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_sm
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# PUT /kiro-key
# ---------------------------------------------------------------------------


class TestPutKiroKey:
    def test_creates_when_secret_does_not_exist(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm, mock_cognito
    ):
        mock_sm.put_secret_value.side_effect = mock_sm.exceptions.ResourceNotFoundException()
        mock_cognito.admin_get_user.return_value = {"UserAttributes": []}

        body = {"apiKey": "kiro-key-12345", "region": "us-east-1"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["configured"] is True
        assert data["region"] == "us-east-1"
        mock_sm.create_secret.assert_called_once()

    def test_overwrites_existing_secret(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        mock_sm.put_secret_value.return_value = {}
        body = {"apiKey": "new-key", "region": "us-east-1"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 200
        mock_sm.put_secret_value.assert_called_once()
        # tag_resource is called by _put_and_tag
        mock_sm.tag_resource.assert_called()

    def test_create_race_falls_back_to_put(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        # First call to put_secret_value: secret doesn't exist
        # create_secret: someone else got there first
        # second call to put_secret_value: succeeds
        mock_sm.put_secret_value.side_effect = [
            mock_sm.exceptions.ResourceNotFoundException(),
            None,
        ]
        mock_sm.create_secret.side_effect = mock_sm.exceptions.ResourceExistsException()

        body = {"apiKey": "new-key"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 200
        assert mock_sm.put_secret_value.call_count == 2

    def test_missing_api_key(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        body = {}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 400
        assert "apiKey" in json.loads(resp["body"])["error"]

    def test_empty_api_key(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        body = {"apiKey": "   "}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 400

    def test_api_key_too_long(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        body = {"apiKey": "x" * 5000}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 400
        assert "too long" in json.loads(resp["body"])["error"]

    def test_invalid_region_rejected(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        body = {"apiKey": "valid-key", "region": "ap-south-1"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 400
        assert "region" in json.loads(resp["body"])["error"]

    def test_default_region_when_not_specified(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        mock_sm.put_secret_value.return_value = {}
        body = {"apiKey": "valid-key"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["region"] == "us-east-1"

    def test_invalidates_usage_cache(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        import crud.kiro_key as mod
        mod._usage_cache[workspace_id] = (1.0, {"cached": True})
        mock_sm.put_secret_value.return_value = {}
        body = {"apiKey": "valid-key"}
        _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert workspace_id not in mod._usage_cache

    def test_viewer_forbidden(self, workspace_id, mock_jwt, _mock_viewer, mock_sm):
        body = {"apiKey": "valid-key"}
        resp = _invoke(_apigw("PUT", f"/api/workspaces/{workspace_id}/kiro-key", body=body))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# DELETE /kiro-key
# ---------------------------------------------------------------------------


class TestDeleteKiroKey:
    def test_delete_success(self, workspace_id, mock_jwt, _mock_admin, mock_sm):
        mock_sm.delete_secret.return_value = {}
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["configured"] is False
        mock_sm.delete_secret.assert_called_once()

    def test_delete_idempotent_on_not_found(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        mock_sm.delete_secret.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "DeleteSecret"
        )
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["configured"] is False

    def test_delete_other_error_returns_500(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        mock_sm.delete_secret.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError"}}, "DeleteSecret"
        )
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 500

    def test_delete_invalidates_cache(
        self, workspace_id, mock_jwt, _mock_admin, mock_sm
    ):
        import crud.kiro_key as mod
        mod._usage_cache[workspace_id] = (1.0, {"cached": True})
        mock_sm.delete_secret.return_value = {}
        _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert workspace_id not in mod._usage_cache

    def test_viewer_forbidden(self, workspace_id, mock_jwt, _mock_viewer, mock_sm):
        resp = _invoke(_apigw("DELETE", f"/api/workspaces/{workspace_id}/kiro-key"))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# _fetch_key_and_region
# ---------------------------------------------------------------------------


class TestFetchKeyAndRegion:
    def test_returns_key_and_region(self, mock_sm):
        from crud.kiro_key import _fetch_key_and_region
        mock_sm.get_secret_value.return_value = {"SecretString": "  my-key  "}
        mock_sm.describe_secret.return_value = {
            "Tags": [{"Key": "kiroRegion", "Value": "eu-central-1"}]
        }
        result = _fetch_key_and_region("ws1")
        assert result == ("my-key", "eu-central-1")

    def test_none_when_secret_not_found(self, mock_sm):
        from crud.kiro_key import _fetch_key_and_region
        mock_sm.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue"
        )
        assert _fetch_key_and_region("ws1") is None

    def test_other_error_propagates(self, mock_sm):
        from crud.kiro_key import _fetch_key_and_region
        mock_sm.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError"}}, "GetSecretValue"
        )
        with pytest.raises(ClientError):
            _fetch_key_and_region("ws1")

    def test_describe_failure_falls_back_to_default_region(self, mock_sm):
        from crud.kiro_key import _fetch_key_and_region
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        mock_sm.describe_secret.side_effect = Exception("describe boom")
        assert _fetch_key_and_region("ws1") == ("key", "us-east-1")


# ---------------------------------------------------------------------------
# _invoke_runtime_for_usage
# ---------------------------------------------------------------------------


class TestInvokeRuntimeForUsage:
    def test_raises_without_meta_arn(self, monkeypatch):
        import crud.kiro_key as mod
        monkeypatch.setattr(mod, "META_AGENT_ARN", "")
        with pytest.raises(RuntimeError, match="META_AGENT_ARN"):
            mod._invoke_runtime_for_usage("k", "us-east-1")

    def test_raises_on_empty_response(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        mock_agentcore.invoke_agent_runtime.return_value = {"response": None}
        with pytest.raises(RuntimeError, match="empty"):
            _invoke_runtime_for_usage("k", "us-east-1")

    def test_parses_double_encoded_sse(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        # Inner JSON, then JSON-encoded as a string
        inner = json.dumps({"__usage": {"tier": "POWER", "currentUsage": 5.0}})
        outer = json.dumps(inner)
        sse_body = f"data: {outer}\n\n"
        body = MagicMock()
        body.read.return_value = sse_body.encode("utf-8")
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        result = _invoke_runtime_for_usage("k", "us-east-1")
        assert result == {"__usage": {"tier": "POWER", "currentUsage": 5.0}}

    def test_parses_single_encoded_sse(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        sse_body = 'data: {"__usage": {"tier": "POWER"}}\n\n'
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        result = _invoke_runtime_for_usage("k", "us-east-1")
        assert result == {"__usage": {"tier": "POWER"}}

    def test_returns_last_frame(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        sse_body = (
            'data: {"__progress": "starting"}\n'
            'data: {"__usage": {"tier": "POWER"}}\n'
        )
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        result = _invoke_runtime_for_usage("k", "us-east-1")
        assert result == {"__usage": {"tier": "POWER"}}

    def test_raises_on_unparseable(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        body = MagicMock()
        body.read.return_value = "not-sse-format"
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        with pytest.raises(RuntimeError, match="unparseable"):
            _invoke_runtime_for_usage("k", "us-east-1")

    def test_skips_invalid_sse_lines(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        sse_body = (
            "data: not-json\n"
            "ignored line\n"
            "data:\n"
            'data: {"__usage": {"tier": "POWER"}}\n'
        )
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        result = _invoke_runtime_for_usage("k", "us-east-1")
        assert result == {"__usage": {"tier": "POWER"}}

    def test_handles_inner_string_decode_failure(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        # Outer JSON contains a string that's not valid JSON
        sse_body = 'data: "plain-non-json-string"\n'
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        with pytest.raises(RuntimeError, match="unparseable"):
            _invoke_runtime_for_usage("k", "us-east-1")

    def test_response_bytes_decoded(self, mock_agentcore):
        from crud.kiro_key import _invoke_runtime_for_usage
        sse_body = b'data: {"__usage": {"tier": "POWER"}}\n'
        # body returned directly as bytes (no .read()), test the bytes branch
        mock_agentcore.invoke_agent_runtime.return_value = {"response": sse_body}
        result = _invoke_runtime_for_usage("k", "us-east-1")
        assert result == {"__usage": {"tier": "POWER"}}


# ---------------------------------------------------------------------------
# GET /kiro-key/usage
# ---------------------------------------------------------------------------


class TestGetKiroUsage:
    def test_returns_unconfigured_when_no_secret(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm
    ):
        mock_sm.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "GetSecretValue"
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["configured"] is False

    def test_returns_unconfigured_when_secret_empty(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm
    ):
        mock_sm.get_secret_value.return_value = {"SecretString": ""}
        mock_sm.describe_secret.return_value = {"Tags": []}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["configured"] is False

    def test_serves_from_cache_when_fresh(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm
    ):
        import crud.kiro_key as mod
        import time as _t
        mod._usage_cache[workspace_id] = (_t.time(), {"configured": True, "tier": "CACHED"})
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["tier"] == "CACHED"
        # SM not consulted
        mock_sm.get_secret_value.assert_not_called()

    def test_skips_stale_cache(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm, mock_agentcore
    ):
        import crud.kiro_key as mod
        # Stale: more than TTL ago
        mod._usage_cache[workspace_id] = (1.0, {"configured": True, "tier": "STALE"})
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        mock_sm.describe_secret.return_value = {"Tags": []}
        sse_body = 'data: {"__usage": {"tier": "FRESH", "currentUsage": 1, "usageLimit": 100}}\n'
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["tier"] == "FRESH"

    def test_returns_500_when_fetch_key_fails(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm
    ):
        mock_sm.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError"}}, "GetSecretValue"
        )
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 500

    def test_soft_error_on_runtime_failure(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm, mock_agentcore
    ):
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        mock_sm.describe_secret.return_value = {"Tags": []}
        mock_agentcore.invoke_agent_runtime.side_effect = Exception("boom")
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        # Soft error returns 200 with error key
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["configured"] is True
        assert data["error"] == "runtime_invoke_failed"

    def test_runtime_returns_error_frame(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm, mock_agentcore
    ):
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        mock_sm.describe_secret.return_value = {"Tags": []}
        sse_body = 'data: {"__error": "kiro_unauthorized"}\n'
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["error"] == "kiro_unauthorized"

    def test_returns_full_usage_payload(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm, mock_agentcore
    ):
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        mock_sm.describe_secret.return_value = {
            "Tags": [{"Key": "kiroRegion", "Value": "us-east-1"}]
        }
        usage = {
            "tier": "POWER",
            "currentUsage": 200.5,
            "usageLimit": 10000,
            "resetsOn": "2026-05-01",
            "overagesEnabled": True,
            "overageRate": 0.04,
            "overageUsed": 0.0,
            "currency": "USD",
        }
        sse_body = f'data: {json.dumps({"__usage": usage})}\n'
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["tier"] == "POWER"
        assert data["currentUsage"] == 200.5
        assert data["currency"] == "USD"
        assert "fetchedAt" in data

    def test_caches_response(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm, mock_agentcore
    ):
        import crud.kiro_key as mod
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        mock_sm.describe_secret.return_value = {"Tags": []}
        sse_body = 'data: {"__usage": {"tier": "POWER"}}\n'
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert workspace_id in mod._usage_cache

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_sm
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 403

    def test_usage_data_defaults(
        self, workspace_id, mock_jwt, _mock_viewer, mock_sm, mock_agentcore
    ):
        mock_sm.get_secret_value.return_value = {"SecretString": "key"}
        mock_sm.describe_secret.return_value = {"Tags": []}
        # Empty __usage dict
        sse_body = 'data: {"__usage": {}}\n'
        body = MagicMock()
        body.read.return_value = sse_body
        mock_agentcore.invoke_agent_runtime.return_value = {"response": body}
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/kiro-key/usage"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["tier"] == ""
        assert data["currency"] == "USD"
        assert data["overageUsed"] == 0.0
        assert data["overagesEnabled"] is False

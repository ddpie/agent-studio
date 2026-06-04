"""Tests for shared.auth module — JWT verification, membership, permissions."""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from jose import jwt as jose_jwt

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("COGNITO_USER_POOL_ID", "us-east-1_TestPool")
os.environ.setdefault("COGNITO_CLIENT_ID", "test-client-id")
os.environ.setdefault("WORKSPACES_TABLE", "test-workspaces")

import shared.auth as auth_module
from shared.auth import (
    ROLE_LEVEL,
    _fetch_jwks,
    _get_signing_key,
    _get_ws_table,
    check_permission,
    get_membership,
    is_platform_admin,
    verify_jwt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_KID = "test-kid-123"
FAKE_ISSUER = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool"
FAKE_AUDIENCE = "test-client-id"

FAKE_JWKS = {
    "keys": [
        {"kid": FAKE_KID, "kty": "RSA", "n": "fake-n", "e": "AQAB"},
        {"kid": "other-kid", "kty": "RSA", "n": "other-n", "e": "AQAB"},
    ]
}


def _make_valid_claims(sub="user-123", **overrides):
    claims = {
        "sub": sub,
        "email": "user@example.com",
        "token_use": "id",
        "iss": FAKE_ISSUER,
        "aud": FAKE_AUDIENCE,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return claims


# ---------------------------------------------------------------------------
# _fetch_jwks
# ---------------------------------------------------------------------------


class TestFetchJwks:
    def test_fetches_from_cognito_endpoint(self):
        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps(FAKE_JWKS).encode()
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        with patch("shared.auth.urllib.request.urlopen", return_value=fake_response) as mock_open:
            # Clear cache
            auth_module._jwks_cache = None
            result = _fetch_jwks()

        assert result == FAKE_JWKS
        assert auth_module._jwks_cache == FAKE_JWKS
        expected_url = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_TestPool/.well-known/jwks.json"
        mock_open.assert_called_once_with(expected_url, timeout=5)


# ---------------------------------------------------------------------------
# _get_signing_key
# ---------------------------------------------------------------------------


class TestGetSigningKey:
    def setup_method(self):
        auth_module._jwks_cache = FAKE_JWKS

    def teardown_method(self):
        auth_module._jwks_cache = None

    def test_finds_matching_kid_from_cache(self):
        token_header = {"kid": FAKE_KID, "alg": "RS256"}
        with patch("shared.auth.jwt.get_unverified_header", return_value=token_header):
            key = _get_signing_key("fake-token")
        assert key["kid"] == FAKE_KID

    def test_refreshes_jwks_when_kid_not_in_cache(self):
        new_kid = "rotated-kid"
        new_jwks = {"keys": [{"kid": new_kid, "kty": "RSA", "n": "new-n", "e": "AQAB"}]}

        token_header = {"kid": new_kid, "alg": "RS256"}

        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps(new_jwks).encode()
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("shared.auth.jwt.get_unverified_header", return_value=token_header),
            patch("shared.auth.urllib.request.urlopen", return_value=fake_response),
        ):
            key = _get_signing_key("fake-token")

        assert key["kid"] == new_kid

    def test_raises_when_kid_not_found_after_refresh(self):
        token_header = {"kid": "nonexistent-kid", "alg": "RS256"}

        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps(FAKE_JWKS).encode()
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        with (
            patch("shared.auth.jwt.get_unverified_header", return_value=token_header),
            patch("shared.auth.urllib.request.urlopen", return_value=fake_response),
        ):
            from jose import JWTError

            with pytest.raises(JWTError, match="Signing key not found"):
                _get_signing_key("fake-token")


# ---------------------------------------------------------------------------
# verify_jwt
# ---------------------------------------------------------------------------


class TestVerifyJwt:
    def setup_method(self):
        auth_module._jwks_cache = FAKE_JWKS

    def teardown_method(self):
        auth_module._jwks_cache = None

    def test_valid_token_returns_claims(self):
        claims = _make_valid_claims()
        with (
            patch("shared.auth._get_signing_key", return_value=FAKE_JWKS["keys"][0]),
            patch("shared.auth.jwt.decode", return_value=claims),
        ):
            result = verify_jwt("valid-token")
        assert result["sub"] == "user-123"
        assert result["token_use"] == "id"

    def test_expired_token_raises_valueerror(self):
        with (
            patch("shared.auth._get_signing_key", return_value=FAKE_JWKS["keys"][0]),
            patch("shared.auth.jwt.decode", side_effect=jose_jwt.ExpiredSignatureError("expired")),
        ):
            # jose raises JWTError subclass for expired tokens
            # but our code patches via jose JWTError catch
            pass

        # The actual flow: jwt.decode raises JWTError (parent), verify_jwt catches and raises ValueError
        from jose import JWTError

        with (
            patch("shared.auth._get_signing_key", return_value=FAKE_JWKS["keys"][0]),
            patch("shared.auth.jwt.decode", side_effect=JWTError("Token is expired")),
        ):
            with pytest.raises(ValueError, match="Authentication failed"):
                verify_jwt("expired-token")

    def test_invalid_signature_raises_valueerror(self):
        from jose import JWTError

        with (
            patch("shared.auth._get_signing_key", return_value=FAKE_JWKS["keys"][0]),
            patch("shared.auth.jwt.decode", side_effect=JWTError("Signature verification failed")),
        ):
            with pytest.raises(ValueError, match="Authentication failed"):
                verify_jwt("bad-sig-token")

    def test_wrong_token_use_raises_valueerror(self):
        claims = _make_valid_claims(token_use="access")
        with (
            patch("shared.auth._get_signing_key", return_value=FAKE_JWKS["keys"][0]),
            patch("shared.auth.jwt.decode", return_value=claims),
        ):
            with pytest.raises(ValueError, match="Not an id token"):
                verify_jwt("access-token")

    def test_missing_token_use_raises_valueerror(self):
        claims = _make_valid_claims()
        del claims["token_use"]
        with (
            patch("shared.auth._get_signing_key", return_value=FAKE_JWKS["keys"][0]),
            patch("shared.auth.jwt.decode", return_value=claims),
        ):
            with pytest.raises(ValueError, match="Not an id token"):
                verify_jwt("no-token-use")

    def test_missing_sub_claim_still_returns(self):
        """verify_jwt does not validate sub presence — it just returns claims."""
        claims = _make_valid_claims()
        del claims["sub"]
        claims["token_use"] = "id"
        with (
            patch("shared.auth._get_signing_key", return_value=FAKE_JWKS["keys"][0]),
            patch("shared.auth.jwt.decode", return_value=claims),
        ):
            result = verify_jwt("no-sub-token")
        assert "sub" not in result

    def test_empty_token_raises_valueerror(self):
        from jose import JWTError

        with patch("shared.auth._get_signing_key", side_effect=JWTError("Not enough segments")):
            with pytest.raises(ValueError, match="Authentication failed"):
                verify_jwt("")

    def test_malformed_token_raises_valueerror(self):
        from jose import JWTError

        with patch("shared.auth._get_signing_key", side_effect=JWTError("Invalid token")):
            with pytest.raises(ValueError, match="Authentication failed"):
                verify_jwt("not.a.jwt.at.all")


# ---------------------------------------------------------------------------
# get_membership
# ---------------------------------------------------------------------------


class TestGetMembership:
    def test_user_is_member(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "workspaceId": "ws-1",
                "sk": "MEMBER#user-1",
                "userId": "user-1",
                "role": "editor",
            }
        }
        with patch("shared.auth._get_ws_table", return_value=mock_table):
            result = get_membership("ws-1", "user-1")

        assert result is not None
        assert result["role"] == "editor"
        assert result["userId"] == "user-1"
        mock_table.get_item.assert_called_once_with(
            Key={"workspaceId": "ws-1", "sk": "MEMBER#user-1"},
            ConsistentRead=True,
        )

    def test_user_not_member_returns_none(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}  # No "Item" key
        with patch("shared.auth._get_ws_table", return_value=mock_table):
            result = get_membership("ws-1", "nonexistent-user")

        assert result is None

    def test_owner_membership(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "workspaceId": "ws-1",
                "sk": "MEMBER#owner-1",
                "userId": "owner-1",
                "role": "owner",
            }
        }
        with patch("shared.auth._get_ws_table", return_value=mock_table):
            result = get_membership("ws-1", "owner-1")

        assert result["role"] == "owner"

    def test_viewer_membership(self):
        mock_table = MagicMock()
        mock_table.get_item.return_value = {
            "Item": {
                "workspaceId": "ws-1",
                "sk": "MEMBER#viewer-1",
                "userId": "viewer-1",
                "role": "viewer",
            }
        }
        with patch("shared.auth._get_ws_table", return_value=mock_table):
            result = get_membership("ws-1", "viewer-1")

        assert result["role"] == "viewer"

    def test_cross_workspace_returns_none(self):
        """User is member of ws-1 but query for ws-2 returns None."""
        mock_table = MagicMock()
        mock_table.get_item.return_value = {}
        with patch("shared.auth._get_ws_table", return_value=mock_table):
            result = get_membership("ws-2", "user-1")

        assert result is None
        mock_table.get_item.assert_called_once_with(
            Key={"workspaceId": "ws-2", "sk": "MEMBER#user-1"},
            ConsistentRead=True,
        )


# ---------------------------------------------------------------------------
# check_permission
# ---------------------------------------------------------------------------


class TestCheckPermission:
    def test_owner_has_all_permissions(self):
        member = {"role": "owner"}
        assert check_permission(member, "viewer") is True
        assert check_permission(member, "editor") is True
        assert check_permission(member, "admin") is True
        assert check_permission(member, "owner") is True

    def test_admin_cannot_act_as_owner(self):
        member = {"role": "admin"}
        assert check_permission(member, "viewer") is True
        assert check_permission(member, "editor") is True
        assert check_permission(member, "admin") is True
        assert check_permission(member, "owner") is False

    def test_editor_permissions(self):
        member = {"role": "editor"}
        assert check_permission(member, "viewer") is True
        assert check_permission(member, "editor") is True
        assert check_permission(member, "admin") is False
        assert check_permission(member, "owner") is False

    def test_viewer_only_viewer(self):
        member = {"role": "viewer"}
        assert check_permission(member, "viewer") is True
        assert check_permission(member, "editor") is False
        assert check_permission(member, "admin") is False
        assert check_permission(member, "owner") is False

    def test_none_member_always_denied(self):
        assert check_permission(None, "viewer") is False
        assert check_permission(None, "editor") is False
        assert check_permission(None, "admin") is False
        assert check_permission(None, "owner") is False

    def test_unknown_role_denied(self):
        member = {"role": "superuser"}
        assert check_permission(member, "viewer") is False
        assert check_permission(member, "editor") is False

    def test_missing_role_key_denied(self):
        member = {"userId": "user-1"}
        assert check_permission(member, "viewer") is False

    def test_unknown_min_role_denied(self):
        """If min_role is unrecognized, threshold is 99 — always denied."""
        member = {"role": "owner"}
        assert check_permission(member, "superadmin") is False

    def test_empty_role_string_denied(self):
        member = {"role": ""}
        assert check_permission(member, "viewer") is False


# ---------------------------------------------------------------------------
# is_platform_admin
# ---------------------------------------------------------------------------


class TestIsPlatformAdmin:
    def test_user_in_platform_admins_group(self):
        claims = {"cognito:groups": ["platform-admins", "other-group"]}
        assert is_platform_admin(claims) is True

    def test_user_not_in_platform_admins_group(self):
        claims = {"cognito:groups": ["regular-users"]}
        assert is_platform_admin(claims) is False

    def test_no_groups_claim(self):
        claims = {}
        assert is_platform_admin(claims) is False

    def test_groups_is_none(self):
        claims = {"cognito:groups": None}
        assert is_platform_admin(claims) is False

    def test_empty_groups_list(self):
        claims = {"cognito:groups": []}
        assert is_platform_admin(claims) is False

    def test_only_platform_admins_group(self):
        claims = {"cognito:groups": ["platform-admins"]}
        assert is_platform_admin(claims) is True


# ---------------------------------------------------------------------------
# ROLE_LEVEL ordering
# ---------------------------------------------------------------------------


class TestRoleLevel:
    def test_role_hierarchy(self):
        assert ROLE_LEVEL["viewer"] < ROLE_LEVEL["editor"]
        assert ROLE_LEVEL["editor"] < ROLE_LEVEL["admin"]
        assert ROLE_LEVEL["admin"] < ROLE_LEVEL["owner"]

    def test_all_roles_present(self):
        assert set(ROLE_LEVEL.keys()) == {"viewer", "editor", "admin", "owner"}


# ---------------------------------------------------------------------------
# _get_ws_table (singleton)
# ---------------------------------------------------------------------------


class TestGetWsTable:
    def test_creates_table_resource(self):
        auth_module._ws_table = None
        mock_resource = MagicMock()
        mock_table = MagicMock()
        mock_resource.Table.return_value = mock_table

        with patch("shared.auth.boto3.resource", return_value=mock_resource) as mock_boto:
            result = _get_ws_table()

        assert result == mock_table
        mock_boto.assert_called_once_with("dynamodb", region_name="us-east-1")
        mock_resource.Table.assert_called_once_with("test-workspaces")

        # Cleanup
        auth_module._ws_table = None

    def test_reuses_cached_table(self):
        mock_table = MagicMock()
        auth_module._ws_table = mock_table

        with patch("shared.auth.boto3.resource") as mock_boto:
            result = _get_ws_table()

        assert result == mock_table
        mock_boto.assert_not_called()

        # Cleanup
        auth_module._ws_table = None

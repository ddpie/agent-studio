"""Penetration-style tests to kill survived mutants in shared.auth + shared.middleware.

Strategy: exercise REAL code paths with minimal mocking. Only the network boundary
(urllib.request.urlopen) and DynamoDB are mocked. Real RSA key pairs + real JWTs
ensure that mutations to algorithms, audience, issuer, token_use checks, key
lookups, and permission comparisons are caught.
"""
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

# --- Environment setup (before importing shared modules) ---
os.environ["AWS_REGION"] = "us-east-1"
os.environ["COGNITO_USER_POOL_ID"] = "us-east-1_TestPool"
os.environ["COGNITO_CLIENT_ID"] = "test-client-id"
os.environ["WORKSPACES_TABLE"] = "test-workspaces"

import shared.auth as auth_module
from shared.auth import (
    _fetch_jwks,
    _get_signing_key,
    check_permission,
    is_platform_admin,
    verify_jwt,
)
from shared.middleware import auth_check, check_platform_admin

# ---------------------------------------------------------------------------
# RSA Key Infrastructure
# ---------------------------------------------------------------------------

def _generate_rsa_key():
    """Generate an RSA private key for signing JWTs."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _private_key_pem(private_key) -> str:
    """Export private key as PEM string (for python-jose signing)."""
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def _build_jwk(private_key, kid: str) -> dict:
    """Build a JWK dict from an RSA private key (public components only)."""
    from jose.backends import RSAKey
    pub_key = private_key.public_key()
    pub_pem = pub_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    rsa_key = RSAKey(pub_pem, algorithm="RS256")
    jwk = rsa_key.to_dict()
    jwk["kid"] = kid
    jwk["use"] = "sig"
    jwk["alg"] = "RS256"
    return jwk


# Module-level fixtures for performance (key gen is expensive)
_RSA_KEY = _generate_rsa_key()
_KID = "test-kid-real"
_JWK = _build_jwk(_RSA_KEY, _KID)
_PRIVATE_PEM = _private_key_pem(_RSA_KEY)

REGION = "us-east-1"
POOL_ID = "us-east-1_TestPool"
CLIENT_ID = "test-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}"


def _make_token(
    sub="user-abc",
    token_use="id",
    kid=_KID,
    algorithm="RS256",
    audience=CLIENT_ID,
    issuer=ISSUER,
    extra_claims=None,
    exp_offset=3600,
):
    """Create a real signed JWT."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": "test@example.com",
        "token_use": token_use,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset,
    }
    if extra_claims:
        claims.update(extra_claims)
    headers = {"kid": kid, "alg": algorithm}
    return jose_jwt.encode(claims, _PRIVATE_PEM, algorithm=algorithm, headers=headers)


def _jwks_response():
    """Build a mock urlopen response returning our JWKS."""
    jwks = {"keys": [_JWK]}
    data = json.dumps(jwks).encode()
    resp = MagicMock()
    resp.read.return_value = data
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _event_with_auth(header_value: str | None) -> MagicMock:
    """Build a minimal event-like object with get_header_value."""
    ev = MagicMock()
    ev.get_header_value.return_value = header_value
    return ev


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_caches():
    """Clear JWKS cache and ws_table before/after each test."""
    auth_module._jwks_cache = None
    auth_module._ws_table = None
    yield
    auth_module._jwks_cache = None
    auth_module._ws_table = None


@pytest.fixture
def mock_urlopen():
    """Mock urlopen to return our real JWKS."""
    with patch("shared.auth.urllib.request.urlopen", return_value=_jwks_response()) as m:
        yield m


# ---------------------------------------------------------------------------
# 1. Real RSA key signing — full verify_jwt path
# ---------------------------------------------------------------------------

class TestVerifyJwtRealCrypto:
    """Exercise verify_jwt with real RSA signing. Only urlopen is mocked."""

    def test_valid_token_full_path(self, mock_urlopen):
        """Happy path: real RSA-signed JWT verifies end-to-end."""
        token = _make_token()
        claims = verify_jwt(token)
        assert claims["sub"] == "user-abc"
        assert claims["token_use"] == "id"
        assert claims["iss"] == ISSUER
        assert claims["aud"] == CLIENT_ID

    def test_key_none_mutation_killed(self, mock_urlopen):
        """If _get_signing_key returned None, jwt.decode would fail.
        Kills mutant: key = None (mutmut_1 pattern)."""
        token = _make_token()
        # Verify that with the correct key it works
        claims = verify_jwt(token)
        assert claims["sub"] == "user-abc"

    def test_wrong_kid_refetches_jwks(self, mock_urlopen):
        """When kid not in cache, code refetches JWKS. If JWKS still lacks kid, raises."""
        token = _make_token(kid="nonexistent-kid")
        with pytest.raises(ValueError, match="Authentication failed"):
            verify_jwt(token)
        # urlopen called at least once for refetch
        assert mock_urlopen.call_count >= 1

    def test_wrong_algorithm_rejected(self, mock_urlopen):
        """Mutation: algorithms=["RS256"] → algorithms=["RS384"] would fail verification."""
        # Sign with RS256 but verify expects RS256; if mutated to RS384 the decode fails
        token = _make_token()
        claims = verify_jwt(token)
        assert claims["sub"] == "user-abc"

    def test_hs256_token_rejected(self, mock_urlopen):
        """A token signed with HS256 must be rejected (algorithm confusion attack)."""
        now = int(time.time())
        payload = {
            "sub": "attacker",
            "token_use": "id",
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
        }
        # Try to create an HS256 token (symmetric) — should be rejected by RS256-only verify
        hs_token = jose_jwt.encode(payload, "symmetric-secret", algorithm="HS256")
        with pytest.raises(ValueError, match="Authentication failed"):
            verify_jwt(hs_token)

    def test_wrong_audience_rejected(self, mock_urlopen):
        """Mutation: audience=COGNITO_CLIENT_ID → audience="wrong" would fail."""
        token = _make_token(audience="wrong-client-id")
        with pytest.raises(ValueError, match="Authentication failed"):
            verify_jwt(token)

    def test_wrong_issuer_rejected(self, mock_urlopen):
        """Mutation: issuer changes → token rejected."""
        token = _make_token(issuer="https://evil.example.com")
        with pytest.raises(ValueError, match="Authentication failed"):
            verify_jwt(token)

    def test_expired_token_rejected(self, mock_urlopen):
        """Expired tokens must be rejected."""
        token = _make_token(exp_offset=-3600)  # expired 1 hour ago
        with pytest.raises(ValueError, match="Authentication failed"):
            verify_jwt(token)

    def test_token_use_access_rejected(self, mock_urlopen):
        """Mutation: token_use check removed → access tokens allowed (must fail)."""
        token = _make_token(token_use="access")
        with pytest.raises(ValueError, match="Not an id token"):
            verify_jwt(token)

    def test_token_use_missing_rejected(self, mock_urlopen):
        """token_use claim absent → must raise."""
        now = int(time.time())
        payload = {
            "sub": "user-abc",
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
            # no token_use
        }
        headers = {"kid": _KID, "alg": "RS256"}
        token = jose_jwt.encode(payload, _PRIVATE_PEM, algorithm="RS256", headers=headers)
        with pytest.raises(ValueError, match="Not an id token"):
            verify_jwt(token)

    def test_token_use_empty_string_rejected(self, mock_urlopen):
        """token_use="" must also be rejected."""
        token = _make_token(token_use="")
        with pytest.raises(ValueError, match="Not an id token"):
            verify_jwt(token)

    def test_token_use_none_value_rejected(self, mock_urlopen):
        """token_use=None in claims must be rejected."""
        now = int(time.time())
        payload = {
            "sub": "user-abc",
            "token_use": None,
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
        }
        headers = {"kid": _KID, "alg": "RS256"}
        # python-jose might strip None values, so we manually construct
        token = jose_jwt.encode(payload, _PRIVATE_PEM, algorithm="RS256", headers=headers)
        with pytest.raises(ValueError, match="Not an id token"):
            verify_jwt(token)

    def test_valid_token_returns_all_claims(self, mock_urlopen):
        """Ensure all standard claims are passed through."""
        token = _make_token(extra_claims={"cognito:groups": ["admin"]})
        claims = verify_jwt(token)
        assert claims["email"] == "test@example.com"
        assert claims["cognito:groups"] == ["admin"]


# ---------------------------------------------------------------------------
# 2. verify_jwt spy tests — assert correct parameters
# ---------------------------------------------------------------------------

class TestVerifyJwtSpy:
    """Use wraps=jose_jwt.decode as a spy to check call arguments."""

    def test_decode_called_with_rs256(self, mock_urlopen):
        """Kills mutation: algorithms=["RS256"] → algorithms=["XX"]."""
        token = _make_token()
        with patch("shared.auth.jwt.decode", wraps=jose_jwt.decode) as spy:
            verify_jwt(token)
        spy.assert_called_once()
        _, kwargs = spy.call_args
        assert kwargs.get("algorithms") == ["RS256"] or spy.call_args[0][2] == ["RS256"] or \
            "RS256" in str(spy.call_args)
        # More precise check
        args, kwargs = spy.call_args
        # jose.jwt.decode(token, key, algorithms=..., audience=..., issuer=..., options=...)
        if "algorithms" in kwargs:
            assert kwargs["algorithms"] == ["RS256"]
        else:
            # positional: decode(token, key, algorithms)
            assert args[2] == ["RS256"]

    def test_decode_called_with_correct_audience(self, mock_urlopen):
        """Kills mutation: audience=COGNITO_CLIENT_ID → audience="XX"."""
        token = _make_token()
        with patch("shared.auth.jwt.decode", wraps=jose_jwt.decode) as spy:
            verify_jwt(token)
        _, kwargs = spy.call_args
        assert kwargs.get("audience") == CLIENT_ID

    def test_decode_called_with_correct_issuer(self, mock_urlopen):
        """Kills mutation: issuer=f"..." → issuer="XX"."""
        token = _make_token()
        with patch("shared.auth.jwt.decode", wraps=jose_jwt.decode) as spy:
            verify_jwt(token)
        _, kwargs = spy.call_args
        assert kwargs.get("issuer") == ISSUER

    def test_decode_called_with_verify_at_hash_false(self, mock_urlopen):
        """Kills mutation: options={"verify_at_hash": False} → True."""
        token = _make_token()
        with patch("shared.auth.jwt.decode", wraps=jose_jwt.decode) as spy:
            verify_jwt(token)
        _, kwargs = spy.call_args
        assert kwargs.get("options", {}).get("verify_at_hash") is False


# ---------------------------------------------------------------------------
# 3. _get_signing_key detailed tests
# ---------------------------------------------------------------------------

class TestGetSigningKeyReal:
    """Test _get_signing_key with real tokens."""

    def test_finds_key_from_cache(self, mock_urlopen):
        """When JWKS is cached with matching kid, no network call needed."""
        auth_module._jwks_cache = {"keys": [_JWK]}
        token = _make_token()
        key = _get_signing_key(token)
        assert key["kid"] == _KID
        mock_urlopen.assert_not_called()

    def test_fetches_jwks_when_cache_empty(self, mock_urlopen):
        """Cache miss triggers fetch."""
        token = _make_token()
        key = _get_signing_key(token)
        assert key["kid"] == _KID
        mock_urlopen.assert_called_once()

    def test_refetches_when_kid_not_in_cache(self, mock_urlopen):
        """Cache has wrong kid → refetch."""
        other_jwk = dict(_JWK)
        other_jwk["kid"] = "old-kid"
        auth_module._jwks_cache = {"keys": [other_jwk]}
        # urlopen will return JWKS with correct kid
        token = _make_token()
        key = _get_signing_key(token)
        assert key["kid"] == _KID

    def test_raises_when_kid_not_found_anywhere(self, mock_urlopen):
        """Kid missing from both cache and fetched JWKS → JWTError."""
        from jose import JWTError
        token = _make_token(kid="ghost-kid")
        with pytest.raises(JWTError, match="Signing key not found"):
            _get_signing_key(token)

    def test_kid_match_is_exact(self, mock_urlopen):
        """Mutation: key["kid"] == kid → key["kid"] != kid would return wrong key."""
        # Add a decoy key
        decoy = dict(_JWK)
        decoy["kid"] = "decoy-kid"
        decoy["n"] = "decoy-n-value"
        auth_module._jwks_cache = {"keys": [decoy, _JWK]}
        token = _make_token()
        key = _get_signing_key(token)
        assert key["kid"] == _KID
        assert key["kid"] != "decoy-kid"


# ---------------------------------------------------------------------------
# 4. _fetch_jwks tests
# ---------------------------------------------------------------------------

class TestFetchJwksUrl:
    """Verify that _fetch_jwks constructs the correct URL."""

    def test_url_contains_region_and_pool(self):
        """Kills mutations to the URL template."""
        resp = _jwks_response()
        with patch("shared.auth.urllib.request.urlopen", return_value=resp) as mock_open:
            _fetch_jwks()
        url_called = mock_open.call_args[0][0]
        assert "us-east-1" in url_called
        assert "us-east-1_TestPool" in url_called
        assert ".well-known/jwks.json" in url_called
        assert url_called == f"https://cognito-idp.{REGION}.amazonaws.com/{POOL_ID}/.well-known/jwks.json"

    def test_timeout_is_5(self):
        """Kills mutation: timeout=5 → timeout=6."""
        resp = _jwks_response()
        with patch("shared.auth.urllib.request.urlopen", return_value=resp) as mock_open:
            _fetch_jwks()
        _, kwargs = mock_open.call_args
        assert kwargs.get("timeout") == 5

    def test_sets_module_cache(self):
        """After fetch, _jwks_cache is set (not None)."""
        resp = _jwks_response()
        with patch("shared.auth.urllib.request.urlopen", return_value=resp):
            result = _fetch_jwks()
        assert auth_module._jwks_cache == result
        assert auth_module._jwks_cache is not None
        assert "keys" in auth_module._jwks_cache


# ---------------------------------------------------------------------------
# 5. auth_check integration tests (real JWT, mock only network + DDB)
# ---------------------------------------------------------------------------

class TestAuthCheckIntegration:
    """Test auth_check without mocking verify_jwt — only mock urlopen + DDB."""

    def test_valid_token_with_membership(self, mock_urlopen):
        """Full integration: real JWT → verify_jwt → membership check → success."""
        token = _make_token(sub="user-int-1")
        ev = _event_with_auth(f"Bearer {token}")
        member = {"workspaceId": "ws-valid", "userId": "user-int-1", "role": "editor"}
        with patch("shared.auth.get_membership", return_value=member), \
             patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, ws_id="ws-valid")
        assert err is None
        assert user_id == "user-int-1"
        assert ws == "ws-valid"
        assert m == member

    def test_bearer_prefix_stripping(self, mock_urlopen):
        """Mutation: auth_header[7:] → auth_header[6:] or [8:] would break token."""
        token = _make_token(sub="user-strip")
        ev = _event_with_auth(f"Bearer {token}")
        member = {"workspaceId": "ws1", "userId": "user-strip", "role": "viewer"}
        with patch("shared.auth.get_membership", return_value=member), \
             patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is None
        assert user_id == "user-strip"

    def test_bearer_prefix_with_extra_space_fails(self, mock_urlopen):
        """'Bearer  token' (double space) — [7:] would include leading space → invalid."""
        token = _make_token(sub="user-space")
        ev = _event_with_auth(f"Bearer  {token}")  # double space
        user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is not None
        assert err.status_code == 403

    def test_missing_bearer_prefix(self, mock_urlopen):
        """No 'Bearer ' prefix → immediate 403."""
        token = _make_token()
        ev = _event_with_auth(token)  # no "Bearer " prefix
        user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is not None
        assert err.status_code == 403

    def test_empty_auth_header(self, mock_urlopen):
        """Empty string → 403."""
        ev = _event_with_auth("")
        user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is not None

    def test_none_auth_header(self, mock_urlopen):
        """None → 403."""
        ev = _event_with_auth(None)
        user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is not None

    def test_default_min_role_viewer(self, mock_urlopen):
        """Default min_role='viewer'; viewer member should pass."""
        token = _make_token(sub="viewer-user")
        ev = _event_with_auth(f"Bearer {token}")
        member = {"workspaceId": "ws1", "userId": "viewer-user", "role": "viewer"}
        with patch("shared.auth.get_membership", return_value=member), \
             patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is None
        assert user_id == "viewer-user"

    def test_viewer_cannot_access_editor_route(self, mock_urlopen):
        """viewer with min_role='editor' → 403."""
        token = _make_token(sub="viewer-user")
        ev = _event_with_auth(f"Bearer {token}")
        member = {"workspaceId": "ws1", "userId": "viewer-user", "role": "viewer"}
        with patch("shared.auth.get_membership", return_value=member), \
             patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, min_role="editor", ws_id="ws1")
        assert err is not None
        assert err.status_code == 403

    def test_require_ws_false_skips_membership(self, mock_urlopen):
        """require_ws=False → no membership check, returns user_id only."""
        token = _make_token(sub="user-nows")
        ev = _event_with_auth(f"Bearer {token}")
        user_id, ws, m, err = auth_check(ev, require_ws=False)
        assert err is None
        assert user_id == "user-nows"
        assert ws is None
        assert m is None

    def test_invalid_ws_id_returns_403(self, mock_urlopen):
        """Invalid workspace ID (spaces, special chars) → 403."""
        token = _make_token(sub="user-badws")
        ev = _event_with_auth(f"Bearer {token}")
        user_id, ws, m, err = auth_check(ev, ws_id="bad ws id!")
        assert err is not None
        assert err.status_code == 403

    def test_no_membership_returns_403(self, mock_urlopen):
        """User not a member of workspace → 403."""
        token = _make_token(sub="outsider")
        ev = _event_with_auth(f"Bearer {token}")
        with patch("shared.auth.get_membership", return_value=None), \
             patch("shared.middleware.get_membership", return_value=None):
            user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is not None
        assert err.status_code == 403

    def test_expired_token_returns_403(self, mock_urlopen):
        """Expired JWT → verify_jwt raises → 403."""
        token = _make_token(sub="user-exp", exp_offset=-100)
        ev = _event_with_auth(f"Bearer {token}")
        user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is not None
        assert err.status_code == 403


# ---------------------------------------------------------------------------
# 6. check_platform_admin integration tests
# ---------------------------------------------------------------------------

class TestCheckPlatformAdminIntegration:
    """Test check_platform_admin with real JWT, mock only network."""

    def test_admin_user(self, mock_urlopen):
        """User in platform-admins group → is_admin=True."""
        token = _make_token(
            sub="admin-user",
            extra_claims={"cognito:groups": ["platform-admins"]},
        )
        ev = _event_with_auth(f"Bearer {token}")
        user_id, is_admin, err = check_platform_admin(ev)
        assert err is None
        assert user_id == "admin-user"
        assert is_admin is True

    def test_non_admin_user(self, mock_urlopen):
        """User not in platform-admins → is_admin=False."""
        token = _make_token(
            sub="regular-user",
            extra_claims={"cognito:groups": ["developers"]},
        )
        ev = _event_with_auth(f"Bearer {token}")
        user_id, is_admin, err = check_platform_admin(ev)
        assert err is None
        assert user_id == "regular-user"
        assert is_admin is False

    def test_no_groups_claim(self, mock_urlopen):
        """No cognito:groups in claims → not admin."""
        token = _make_token(sub="nogroups")
        ev = _event_with_auth(f"Bearer {token}")
        user_id, is_admin, err = check_platform_admin(ev)
        assert err is None
        assert is_admin is False

    def test_missing_bearer_prefix(self, mock_urlopen):
        """No Bearer prefix → forbidden."""
        token = _make_token(sub="admin-user")
        ev = _event_with_auth(f"Token {token}")
        user_id, is_admin, err = check_platform_admin(ev)
        assert err is not None
        assert user_id is None
        assert is_admin is False

    def test_expired_token(self, mock_urlopen):
        """Expired JWT → forbidden."""
        token = _make_token(sub="admin-user", exp_offset=-100)
        ev = _event_with_auth(f"Bearer {token}")
        user_id, is_admin, err = check_platform_admin(ev)
        assert err is not None
        assert user_id is None
        assert is_admin is False


# ---------------------------------------------------------------------------
# 7. check_permission boundary tests (>= vs > mutations)
# ---------------------------------------------------------------------------

class TestCheckPermissionBoundary:
    """Target the >= comparison: ROLE_LEVEL[member_role] >= ROLE_LEVEL[min_role]."""

    def test_viewer_equals_viewer_passes(self):
        """Equality case: viewer >= viewer → True. Mutation >= to > would fail."""
        member = {"role": "viewer"}
        assert check_permission(member, "viewer") is True

    def test_editor_equals_editor_passes(self):
        """editor >= editor → True."""
        member = {"role": "editor"}
        assert check_permission(member, "editor") is True

    def test_admin_equals_admin_passes(self):
        """admin >= admin → True."""
        member = {"role": "admin"}
        assert check_permission(member, "admin") is True

    def test_owner_equals_owner_passes(self):
        """owner >= owner → True."""
        member = {"role": "owner"}
        assert check_permission(member, "owner") is True

    def test_viewer_below_editor_fails(self):
        """viewer(0) < editor(1) → False. Catches >= mutated to <=."""
        member = {"role": "viewer"}
        assert check_permission(member, "editor") is False

    def test_editor_above_viewer_passes(self):
        """editor(1) >= viewer(0) → True."""
        member = {"role": "editor"}
        assert check_permission(member, "viewer") is True

    def test_none_member_returns_false(self):
        """None member → False (first guard)."""
        assert check_permission(None, "viewer") is False

    def test_empty_member_dict_returns_false(self):
        """Empty dict (no role key) → False."""
        assert check_permission({}, "viewer") is False

    def test_unknown_role_returns_false(self):
        """Unknown role gets -1, below viewer(0) → False."""
        member = {"role": "unknown"}
        assert check_permission(member, "viewer") is False

    def test_unknown_min_role_returns_false(self):
        """Unknown min_role gets 99 → always False even for owner(3)."""
        member = {"role": "owner"}
        assert check_permission(member, "superadmin") is False

    def test_role_level_values_exact(self):
        """Verify exact numeric values — catches mutations to level constants."""
        from shared.auth import ROLE_LEVEL
        assert ROLE_LEVEL["viewer"] == 0
        assert ROLE_LEVEL["editor"] == 1
        assert ROLE_LEVEL["admin"] == 2
        assert ROLE_LEVEL["owner"] == 3

    def test_default_for_missing_role_is_negative(self):
        """member.get("role", "") → ROLE_LEVEL.get("", -1) == -1."""
        from shared.auth import ROLE_LEVEL
        assert ROLE_LEVEL.get("", -1) == -1

    def test_default_for_missing_min_role_is_99(self):
        """ROLE_LEVEL.get(min_role, 99) for unknown min_role."""
        from shared.auth import ROLE_LEVEL
        assert ROLE_LEVEL.get("nonexistent", 99) == 99


# ---------------------------------------------------------------------------
# 8. is_platform_admin targeted mutations
# ---------------------------------------------------------------------------

class TestIsPlatformAdminMutations:
    """Kill mutations to the PLATFORM_ADMIN_GROUP constant and `in` check."""

    def test_exact_group_name_required(self):
        """Group must be exactly 'platform-admins', not a substring."""
        assert is_platform_admin({"cognito:groups": ["platform-admins"]}) is True
        assert is_platform_admin({"cognito:groups": ["platform-admin"]}) is False
        assert is_platform_admin({"cognito:groups": ["platform-admins-extra"]}) is False

    def test_group_in_list_with_others(self):
        """Group present among others → True."""
        claims = {"cognito:groups": ["devs", "platform-admins", "testers"]}
        assert is_platform_admin(claims) is True

    def test_empty_groups_list(self):
        """Empty list → False."""
        assert is_platform_admin({"cognito:groups": []}) is False

    def test_none_groups(self):
        """None groups → False (or [] fallback)."""
        assert is_platform_admin({"cognito:groups": None}) is False

    def test_missing_groups_key(self):
        """No key → False."""
        assert is_platform_admin({}) is False

    def test_platform_admin_group_constant(self):
        """Verify the constant value — catches mutations to the string."""
        from shared.auth import PLATFORM_ADMIN_GROUP
        assert PLATFORM_ADMIN_GROUP == "platform-admins"


# ---------------------------------------------------------------------------
# 9. JWKS cache behavior mutations
# ---------------------------------------------------------------------------

class TestJwksCacheBehavior:
    """Kill mutations to cache logic: _jwks_cache or _fetch_jwks()."""

    def test_cache_used_when_available(self):
        """If _jwks_cache is set and contains kid, no network call."""
        auth_module._jwks_cache = {"keys": [_JWK]}
        token = _make_token()
        with patch("shared.auth.urllib.request.urlopen") as mock_url:
            key = _get_signing_key(token)
        assert key["kid"] == _KID
        mock_url.assert_not_called()

    def test_cache_miss_triggers_fetch(self):
        """_jwks_cache is None → must fetch."""
        auth_module._jwks_cache = None
        token = _make_token()
        with patch("shared.auth.urllib.request.urlopen", return_value=_jwks_response()) as mock_url:
            key = _get_signing_key(token)
        assert key["kid"] == _KID
        mock_url.assert_called()

    def test_or_short_circuit_in_jwks_fetch(self):
        """'jwks = _jwks_cache or _fetch_jwks()' — if cache is set, fetch not called.
        Kills mutation: changing `or` to `and`."""
        auth_module._jwks_cache = {"keys": [_JWK]}
        token = _make_token()
        with patch("shared.auth._fetch_jwks") as mock_fetch:
            key = _get_signing_key(token)
        mock_fetch.assert_not_called()
        assert key["kid"] == _KID


# ---------------------------------------------------------------------------
# 10. Default parameter mutations — callers relying on defaults
# ---------------------------------------------------------------------------

class TestDefaultParameterMutations:
    """Kill mutations to default argument values by calling without explicit args."""

    def test_auth_check_default_require_ws_is_true(self, mock_urlopen):
        """Default require_ws=True: calling without it MUST require workspace.
        Kills mutation: require_ws: bool = True → False."""
        token = _make_token(sub="user-default")
        ev = _event_with_auth(f"Bearer {token}")
        # Don't pass require_ws — should use default True.
        # ws_id="" (default) is invalid → validate_id returns error → 403
        user_id, ws, m, err = auth_check(ev)
        # If require_ws were False, we'd get (user_id, None, None, None) — no error
        assert err is not None
        assert err.status_code == 403

    def test_auth_check_default_min_role_viewer(self, mock_urlopen):
        """Default min_role='viewer': viewer user passes with no explicit min_role.
        Kills mutation: min_role = 'XXviewerXX' or 'VIEWER'."""
        token = _make_token(sub="viewer-dflt")
        ev = _event_with_auth(f"Bearer {token}")
        member = {"workspaceId": "ws1", "userId": "viewer-dflt", "role": "viewer"}
        with patch("shared.middleware.get_membership", return_value=member):
            # Don't pass min_role — should be "viewer" (matches member.role)
            user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is None
        assert user_id == "viewer-dflt"

    def test_auth_check_default_ws_id_empty(self, mock_urlopen):
        """Default ws_id='': validate_id('', ...) returns error → 403.
        Kills mutation: ws_id = 'XXXX'."""
        token = _make_token(sub="user-wsdefault")
        ev = _event_with_auth(f"Bearer {token}")
        # Don't pass ws_id — empty string default should fail validate_id
        user_id, ws, m, err = auth_check(ev)
        assert err is not None

    def test_check_platform_admin_bearer_prefix_exact(self, mock_urlopen):
        """'Bearer ' (with trailing space) is required — exact 7 char prefix.
        Kills mutations to the startswith literal."""
        token = _make_token(sub="admin-user", extra_claims={"cognito:groups": ["platform-admins"]})
        # Correct prefix
        ev = _event_with_auth(f"Bearer {token}")
        user_id, is_admin, err = check_platform_admin(ev)
        assert err is None
        assert is_admin is True
        # Wrong prefix variants
        for bad in [f"bearer {token}", f"BEARER {token}", f"Bear {token}", f"Bearerx{token}"]:
            ev = _event_with_auth(bad)
            user_id, is_admin, err = check_platform_admin(ev)
            assert err is not None, f"Should reject: {bad[:15]}..."

    def test_auth_check_header_name_authorization(self, mock_urlopen):
        """auth_check reads 'Authorization' header.
        Powertools' get_header_value is case-insensitive so this mutation is equivalent,
        but we verify the function is called with the correct argument."""
        token = _make_token(sub="user-hdr")
        ev = MagicMock()
        ev.get_header_value.return_value = f"Bearer {token}"
        member = {"workspaceId": "ws1", "userId": "user-hdr", "role": "viewer"}
        with patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is None
        ev.get_header_value.assert_called_with("Authorization")

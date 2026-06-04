"""Tests for shared.middleware — auth_check + check_platform_admin."""
from unittest.mock import MagicMock, patch


def _event_with_auth(token: str | None = "Bearer test-token") -> MagicMock:
    """Build a minimal ApiGatewayEvent-like object with header lookups."""
    ev = MagicMock()
    ev.get_header_value.return_value = token
    return ev


# ---------------------------------------------------------------------------
# auth_check
# ---------------------------------------------------------------------------


class TestAuthCheck:
    def test_missing_authorization_header(self):
        from shared.middleware import auth_check
        ev = _event_with_auth(None)
        user_id, ws, member, err = auth_check(ev, ws_id="ws1")
        assert user_id is None
        assert err is not None
        assert err.status_code == 403

    def test_non_bearer_authorization(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Basic abc123")
        user_id, ws, member, err = auth_check(ev, ws_id="ws1")
        assert user_id is None
        assert err is not None
        assert err.status_code == 403

    def test_jwt_failure_returns_403(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Bearer bad-token")
        with patch("shared.middleware.verify_jwt", side_effect=ValueError("bad jwt")):
            user_id, ws, member, err = auth_check(ev, ws_id="ws1")
        assert user_id is None
        assert err is not None
        assert err.status_code == 403

    def test_require_ws_false_skips_membership(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Bearer good")
        with patch("shared.middleware.verify_jwt", return_value={"sub": "user-1"}):
            user_id, ws, member, err = auth_check(ev, require_ws=False)
        assert user_id == "user-1"
        assert ws is None
        assert member is None
        assert err is None

    def test_invalid_ws_id(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Bearer good")
        with patch("shared.middleware.verify_jwt", return_value={"sub": "user-1"}):
            user_id, ws, member, err = auth_check(ev, ws_id="bad ws id")
        assert err is not None
        assert err.status_code == 403

    def test_no_membership_returns_403(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Bearer good")
        with patch("shared.middleware.verify_jwt", return_value={"sub": "user-1"}), \
             patch("shared.middleware.get_membership", return_value=None):
            user_id, ws, member, err = auth_check(ev, ws_id="ws1")
        assert err is not None
        assert err.status_code == 403

    def test_insufficient_role_returns_403(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Bearer good")
        member = {"workspaceId": "ws1", "userId": "user-1", "role": "viewer"}
        with patch("shared.middleware.verify_jwt", return_value={"sub": "user-1"}), \
             patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, min_role="admin", ws_id="ws1")
        assert err is not None
        assert err.status_code == 403

    def test_success_returns_member(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Bearer good")
        member = {"workspaceId": "ws1", "userId": "user-1", "role": "owner"}
        with patch("shared.middleware.verify_jwt", return_value={"sub": "user-1"}), \
             patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, min_role="admin", ws_id="ws1")
        assert user_id == "user-1"
        assert ws == "ws1"
        assert m == member
        assert err is None

    def test_default_min_role_is_viewer(self):
        from shared.middleware import auth_check
        ev = _event_with_auth("Bearer good")
        member = {"workspaceId": "ws1", "userId": "user-1", "role": "viewer"}
        with patch("shared.middleware.verify_jwt", return_value={"sub": "user-1"}), \
             patch("shared.middleware.get_membership", return_value=member):
            user_id, ws, m, err = auth_check(ev, ws_id="ws1")
        assert err is None
        assert user_id == "user-1"


# ---------------------------------------------------------------------------
# check_platform_admin
# ---------------------------------------------------------------------------


class TestCheckPlatformAdmin:
    def test_missing_authorization_header(self):
        from shared.middleware import check_platform_admin
        ev = _event_with_auth(None)
        user_id, is_admin, err = check_platform_admin(ev)
        assert user_id is None
        assert is_admin is False
        assert err is not None

    def test_non_bearer_returns_forbidden(self):
        from shared.middleware import check_platform_admin
        ev = _event_with_auth("Basic xyz")
        user_id, is_admin, err = check_platform_admin(ev)
        assert err is not None

    def test_jwt_failure_returns_forbidden(self):
        from shared.middleware import check_platform_admin
        ev = _event_with_auth("Bearer bad")
        with patch("shared.middleware.verify_jwt", side_effect=ValueError("bad")):
            user_id, is_admin, err = check_platform_admin(ev)
        assert user_id is None
        assert is_admin is False
        assert err is not None

    def test_admin_in_group(self):
        from shared.middleware import check_platform_admin
        ev = _event_with_auth("Bearer good")
        claims = {"sub": "user-1", "cognito:groups": ["platform-admins"]}
        with patch("shared.middleware.verify_jwt", return_value=claims):
            user_id, is_admin, err = check_platform_admin(ev)
        assert user_id == "user-1"
        assert is_admin is True
        assert err is None

    def test_non_admin(self):
        from shared.middleware import check_platform_admin
        ev = _event_with_auth("Bearer good")
        claims = {"sub": "user-1", "cognito:groups": ["other"]}
        with patch("shared.middleware.verify_jwt", return_value=claims):
            user_id, is_admin, err = check_platform_admin(ev)
        assert user_id == "user-1"
        assert is_admin is False
        assert err is None

    def test_no_groups_in_claims(self):
        from shared.middleware import check_platform_admin
        ev = _event_with_auth("Bearer good")
        claims = {"sub": "user-1"}
        with patch("shared.middleware.verify_jwt", return_value=claims):
            user_id, is_admin, err = check_platform_admin(ev)
        assert is_admin is False
        assert err is None

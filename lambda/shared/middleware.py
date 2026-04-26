"""Shared auth helper for all CRUD route files.

Usage in route files:
    from shared.middleware import auth_check

    @router.get("/api/workspaces/<wsId>/agents")
    def list_agents(wsId: str):
        user_id, ws_id, member, err = auth_check(router.current_event, min_role="viewer")
        if err:
            return err
        ...
"""
from aws_lambda_powertools import Logger
from shared.auth import verify_jwt, get_membership, check_permission, is_platform_admin
from shared.response import forbidden
from shared.validators import validate_id

logger = Logger(child=True)


def auth_check(event, min_role: str = "viewer", require_ws: bool = True, ws_id: str = ""):
    """Verify JWT + workspace membership + role.

    Returns (user_id, workspace_id, member, error_response).
    If error_response is not None, return it immediately from the route handler.
    When require_ws=False, workspace_id and member will be None.
    ws_id must be passed explicitly from the route handler's path parameter.
    """
    auth_header = event.get_header_value("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        logger.warning("auth_check: missing Bearer token")
        return None, None, None, forbidden()
    try:
        claims = verify_jwt(auth_header[7:])
        user_id = claims["sub"]
    except Exception as e:
        logger.warning("auth_check: JWT verification failed: %s", str(e))
        return None, None, None, forbidden()

    if not require_ws:
        return user_id, None, None, None

    id_err = validate_id(ws_id, "workspaceId")
    if id_err:
        logger.warning("auth_check: invalid ws_id=%s", ws_id)
        return None, None, None, forbidden()
    member = get_membership(ws_id, user_id)
    if not check_permission(member, min_role):
        logger.warning("auth_check: permission denied user=%s ws=%s role=%s min=%s", user_id, ws_id, member.get("role") if member else None, min_role)
        return None, None, None, forbidden()
    return user_id, ws_id, member, None


def check_platform_admin(event) -> tuple[str | None, bool, object | None]:
    """Check if the caller is a platform admin.

    Returns (user_id, is_admin, error_response).
    If error_response is not None, return it from the handler.
    """
    auth_header = event.get_header_value("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return None, False, forbidden()
    try:
        claims = verify_jwt(auth_header[7:])
        user_id = claims["sub"]
    except Exception:
        return None, False, forbidden()
    return user_id, is_platform_admin(claims), None

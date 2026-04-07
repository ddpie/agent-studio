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
from shared.auth import verify_jwt, get_membership, check_permission
from shared.response import forbidden
from shared.validators import validate_id

logger = Logger(child=True)


def auth_check(event, min_role: str = "viewer", require_ws: bool = True):
    """Verify JWT + workspace membership + role.

    Returns (user_id, workspace_id, member, error_response).
    If error_response is not None, return it immediately from the route handler.
    When require_ws=False, workspace_id and member will be None.
    """
    auth_header = event.get_header_value("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return None, None, None, forbidden()
    try:
        claims = verify_jwt(auth_header[7:])
        user_id = claims["sub"]
    except Exception:
        return None, None, None, forbidden()

    if not require_ws:
        return user_id, None, None, None

    ws_id = (event.resolved_path_parameters or {}).get("wsId", "")
    id_err = validate_id(ws_id, "workspaceId")
    if id_err:
        return None, None, None, forbidden()
    member = get_membership(ws_id, user_id)
    if not check_permission(member, min_role):
        return None, None, None, forbidden()
    return user_id, ws_id, member, None

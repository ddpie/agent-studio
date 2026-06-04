"""Workspace + RBAC scoping helpers shared across Meta-Agent tools.

main.py sets ``_workspace_id`` and ``_caller_id`` on this module before
each invocation. Tools that touch a specific agent call
``ensure_agent_in_workspace(agent_id, min_role=...)`` to:

  1. Confirm the agent exists and belongs to the caller's workspace
     (refusing the operation if it's in a different workspace — without
     leaking whether the agent exists elsewhere).
  2. Verify the caller's membership role meets the required threshold
     (``viewer`` < ``editor`` < ``admin`` < ``owner``).

Previously many Meta-Agent tools skipped both checks. That let
list_agents surface every AgentCore runtime in the account, including
MCP runtimes and other workspaces' agents; and let write operations
(delete/update/invoke) proceed regardless of RBAC role — delete_agent
gated on ``created_by == caller`` only, which is user-level not role-
based and didn't account for ownership transfers or shared workspace
ops.

Mirrors the CRUD Lambda's ``auth_check`` pattern in lambda/shared/middleware
so the two enforcement points stay consistent.
"""

import os

import boto3
from boto3.dynamodb.conditions import Key
from config import AGENTS_TABLE, REGION

# Role constants — kept here as the single source of truth so tools
# don't accidentally pass typo'd strings. Matches lambda/shared/auth.py's
# ROLE_LEVEL exactly; if that table ever changes both sides must move
# together.
ROLE_VIEWER = "viewer"
ROLE_EDITOR = "editor"
ROLE_ADMIN = "admin"
ROLE_OWNER = "owner"

_ROLE_LEVEL = {
    ROLE_VIEWER: 0,
    ROLE_EDITOR: 1,
    ROLE_ADMIN: 2,
    ROLE_OWNER: 3,
}

_WORKSPACES_TABLE = os.getenv("WORKSPACES_TABLE", "agent-studio-workspaces")


# Set by main.py at each invocation
_workspace_id: str = ""
_caller_id: str = ""
# Language hint carried from the invoke payload. Agent builders
# (create_agent, update_agent) read this to pick which BASE_GUIDELINES
# variant (zh vs en) to append to the freshly-authored system_prompt.
# Falls back to "en" when the caller didn't declare a language.
_creator_language: str = ""


def current_workspace() -> str:
    return _workspace_id or ""


def current_caller() -> str:
    return _caller_id or ""


def current_creator_language() -> str:
    return _creator_language or ""


def _agents_table():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.Table(AGENTS_TABLE)


def _workspaces_table():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.Table(_WORKSPACES_TABLE)


def get_agent_record(agent_id: str) -> dict | None:
    if not agent_id:
        return None
    return _agents_table().get_item(Key={"agentId": agent_id}).get("Item")


def _get_membership(workspace_id: str, user_id: str) -> dict | None:
    """Fetch the caller's workspace membership record.

    Same shape as lambda/shared/auth.get_membership — returns the Item
    with a ``role`` field, or None if not a member.
    """
    if not workspace_id or not user_id:
        return None
    try:
        return _workspaces_table().get_item(
            Key={"workspaceId": workspace_id, "sk": f"MEMBER#{user_id}"},
        ).get("Item")
    except Exception:
        return None


def _role_meets(member: dict | None, min_role: str) -> bool:
    if not member:
        return False
    return _ROLE_LEVEL.get(member.get("role", ""), -1) >= _ROLE_LEVEL.get(min_role, 99)


def ensure_agent_in_workspace(
    agent_id: str,
    min_role: str = ROLE_VIEWER,
) -> tuple[dict | None, dict | None]:
    """Load agent record, verify workspace + RBAC. Returns (record, error).

    On success ``error`` is None. On failure returns
    ``(None, {"error": "..."})`` ready to be ``json.dumps``'d back to
    the model.

    The "not in your workspace" case intentionally returns the same
    message as "not found" so a caller can't enumerate cross-workspace
    agent ids by guessing.
    """
    ws = current_workspace()
    caller = current_caller()
    if not ws:
        return None, {"error": "No workspace context — refusing to scope this operation."}

    record = get_agent_record(agent_id)
    if not record or record.get("workspace_id") != ws:
        return None, {"error": f"Agent {agent_id} not found in this workspace."}

    member = _get_membership(ws, caller)
    if not _role_meets(member, min_role):
        have = member.get("role") if member else "(not a member)"
        return None, {
            "error": (
                f"Permission denied: operation requires '{min_role}' "
                f"role or higher, you have '{have}'."
            )
        }

    return record, None


def require_role(min_role: str) -> dict | None:
    """For ops that are workspace-scoped but don't target a specific agent
    (e.g. list_agents, manage_secrets for new secrets). Returns None on
    success, or ``{"error": "..."}`` dict on failure.
    """
    ws = current_workspace()
    if not ws:
        return {"error": "No workspace context — refusing to scope this operation."}
    caller = current_caller()
    member = _get_membership(ws, caller)
    if not _role_meets(member, min_role):
        have = member.get("role") if member else "(not a member)"
        return {
            "error": (
                f"Permission denied: operation requires '{min_role}' "
                f"role or higher, you have '{have}'."
            )
        }
    return None


def list_workspace_agents(include_archived: bool = False) -> list[dict]:
    """Query the workspace-index GSI for all agents in the caller's ws.

    Paginates automatically. Returns [] when no workspace is set.
    """
    ws = current_workspace()
    if not ws:
        return []

    table = _agents_table()
    items: list[dict] = []
    last_key = None
    while True:
        kwargs = {
            "IndexName": "workspace-index",
            "KeyConditionExpression": Key("workspace_id").eq(ws),
        }
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        for item in resp.get("Items", []):
            if not include_archived and item.get("status") == "archived":
                continue
            items.append(item)
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
    return items

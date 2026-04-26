"""check_workspace_permissions — Verify IAM actions available to a workspace role."""

import json

import boto3
from strands import tool

from config import REGION
from tools._workspace import _get_workspace_role_arn

try:
    from cachetools import TTLCache
except ImportError:
    # cachetools may not be installed in all environments (e.g. test).
    # Fall through to a plain dict that never expires — acceptable for
    # short-lived Meta-Agent invocations.
    TTLCache = None  # type: ignore[assignment,misc]

# Module-level cache: (role_arn, action) -> bool.
# TTL=300s matches the spec. maxsize=1024 covers ~40 roles x 25 actions.
if TTLCache is not None:
    _permission_cache: dict[tuple[str, str], bool] = TTLCache(maxsize=1024, ttl=300)
else:
    _permission_cache: dict[tuple[str, str], bool] = {}


def _simulate_actions(role_arn: str, actions: list[str]) -> list[dict]:
    """Call SimulatePrincipalPolicy, paginating at 25 actions per call.

    Returns list of ``{"action": str, "allowed": bool}`` dicts.
    Reads from / writes to ``_permission_cache`` so repeated calls within
    the TTL window are free.
    """
    # Split into cached vs uncached
    results: dict[str, bool] = {}
    uncached: list[str] = []
    for action in actions:
        key = (role_arn, action)
        if key in _permission_cache:
            results[action] = _permission_cache[key]
        else:
            uncached.append(action)

    # Fetch uncached in batches of 25 (SimulatePrincipalPolicy limit)
    if uncached:
        iam_client = boto3.client("iam", region_name=REGION)
        for i in range(0, len(uncached), 25):
            batch = uncached[i : i + 25]
            resp = iam_client.simulate_principal_policy(
                PolicySourceArn=role_arn,
                ActionNames=batch,
                ResourceArns=["*"],
            )
            for r in resp.get("EvaluationResults", []):
                action_name = r["EvalActionName"]
                allowed = r["EvalDecision"] == "allowed"
                results[action_name] = allowed
                _permission_cache[(role_arn, action_name)] = allowed

    # Return in the original order
    return [{"action": a, "allowed": results.get(a, False)} for a in actions]


@tool
def check_workspace_permissions(workspace_id: str, actions: str) -> str:
    """Check which IAM actions the workspace role can perform.

    Call this before creating/updating agents with MCP targets to verify
    the workspace has the required AWS permissions. Results are cached
    for 5 minutes.

    Args:
        workspace_id: Workspace ID.
        actions: Comma-separated IAM actions (e.g. "cloudwatch:DescribeAlarms,logs:StartQuery").

    Returns:
        JSON with {has_role, role_arn, results: [{action, allowed}]}
        or {has_role: false} if workspace has no custom role.
    """
    try:
        role_arn = _get_workspace_role_arn(workspace_id)
        if not role_arn:
            return json.dumps({"has_role": False, "role_arn": None})

        action_list = [a.strip() for a in actions.split(",") if a.strip()]
        if not action_list:
            return json.dumps({"has_role": True, "role_arn": role_arn, "results": []})

        results = _simulate_actions(role_arn, action_list)
        return json.dumps({
            "has_role": True,
            "role_arn": role_arn,
            "results": results,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})

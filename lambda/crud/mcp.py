"""MCP discovery endpoints.

Source of truth for "what MCP targets exist" = mcp-runtime/mcp-registry.yaml
(mirrored to S3 by deploy-mcp.sh). For runtime-type targets we then consult
list_agent_runtimes to report live status (READY / CREATING / FAILED /
DELETING / unavailable). The Gateway is NOT consulted here — it used to be
the default data source but it reliably drifts out of sync with the
registry (nova-canvas incident 2026-05-06: Runtime deleted, ECR deleted,
yaml scrubbed, but the Gateway target lingered and kept appearing in /mcp).
Agents invoke MCP servers by resolving Runtime invoke URLs directly
(agent_template_v2._resolve_runtime_url), so the Gateway plays no role in
the Agent runtime path either.
"""
import json
import time
import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import REGION, S3_BUCKET, WORKSPACES_TABLE
from shared.middleware import auth_check
from shared.response import success, internal_error, bad_request

router = Router()
logger = Logger(child=True)

_control = None
_ws_table = None
_registry_cache = {"data": None, "expires": 0}


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _get_ws_table():
    global _ws_table
    if _ws_table is None:
        _ws_table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _ws_table


def _load_registry():
    """Load mcp-registry.yaml from S3 with 5-min cache.

    Returns the parsed registry dict with keys "remote_targets" and
    "runtime_targets". Each entry already carries description, category,
    sensitivity, deprecated, and (for runtime) package/version. Returns an
    empty dict with both keys on failure so callers can proceed without
    crashing the /mcp page.
    """
    now = time.time()
    if _registry_cache["data"] and now < _registry_cache["expires"]:
        return _registry_cache["data"]
    try:
        import yaml
        s3 = boto3.client("s3", region_name=REGION)
        resp = s3.get_object(Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml")
        data = yaml.safe_load(resp["Body"].read().decode())
        if not isinstance(data, dict):
            data = {}
        # Normalise the two lists so callers never hit KeyError.
        data.setdefault("remote_targets", [])
        data.setdefault("runtime_targets", [])
        _registry_cache["data"] = data
        _registry_cache["expires"] = now + 300
        return data
    except Exception as e:
        logger.warning("Failed to load mcp-registry.yaml: %s", str(e))
        return {"remote_targets": [], "runtime_targets": []}


def _runtime_name_candidates(short_name: str) -> set:
    """Return the set of agent-runtime names a registry entry might match.

    deploy-mcp.sh names runtimes as mcp_<name> with hyphens replaced by
    underscores (e.g. "aws-pricing" -> "mcp_aws_pricing"). We also accept
    the bare underscore form in case a runtime was created without the
    prefix. Matches agent_template_v2._resolve_runtime_url so the discovery
    path and the agent invocation path stay in agreement.
    """
    base = short_name.replace("-", "_")
    return {base, f"mcp_{base}"}


def _list_deployed_runtimes():
    """Paginate list_agent_runtimes into {agentRuntimeName: item}."""
    try:
        control = _get_control()
        resp = control.list_agent_runtimes()
        out = {}
        while True:
            for rt in resp.get("agentRuntimes", []):
                name = rt.get("agentRuntimeName")
                if name:
                    out[name] = rt
            if not resp.get("nextToken"):
                break
            resp = control.list_agent_runtimes(nextToken=resp["nextToken"])
        return out
    except Exception as e:
        logger.warning("list_agent_runtimes failed: %s", str(e))
        return {}


def _get_workspace_policy(ws_id):
    """Get MCP policy from workspace META item."""
    table = _get_ws_table()
    item = table.get_item(Key={"workspaceId": ws_id, "sk": "META"}).get("Item", {})
    return item.get("mcpPolicy", {"mode": "all", "allowedTargets": [], "deniedTargets": []})


def _filter_by_policy(targets, policy):
    """Filter targets based on workspace policy."""
    mode = policy.get("mode", "all")
    if mode == "all":
        return targets
    allowed = set(policy.get("allowedTargets", []))
    denied = set(policy.get("deniedTargets", []))
    if mode == "allowlist":
        return [t for t in targets if t["name"] in allowed]
    if mode == "denylist":
        return [t for t in targets if t["name"] not in denied]
    return targets


def _build_target_list():
    """Produce the /mcp/targets payload from registry + deployed runtimes.

    Every ``enabled`` entry in mcp-registry.yaml becomes one row. Remote
    targets are assumed READY (no health check possible — the endpoint is
    third-party). Runtime targets get their status from list_agent_runtimes;
    "unavailable" means the entry was declared in the registry but
    deploy-mcp.sh never ran or the runtime has since been deleted.
    """
    registry = _load_registry()
    deployed = _list_deployed_runtimes()

    out = []
    for t in registry.get("remote_targets", []):
        if not t.get("enabled"):
            continue
        if t.get("deprecated"):
            continue
        out.append({
            "name": t["name"],
            "description": t.get("description", ""),
            "category": t.get("category", "general"),
            "sensitivity": t.get("sensitivity", "low"),
            "type": "remote",
            "status": "READY",
        })

    for t in registry.get("runtime_targets", []):
        if not t.get("enabled"):
            continue
        if t.get("deprecated"):
            continue
        candidates = _runtime_name_candidates(t["name"])
        live = next((deployed[n] for n in candidates if n in deployed), None)
        status = (live or {}).get("status", "unavailable")
        out.append({
            "name": t["name"],
            "description": t.get("description", ""),
            "category": t.get("category", "general"),
            "sensitivity": t.get("sensitivity", "low"),
            "type": "runtime",
            "status": status,
        })
    return out


@router.get("/api/workspaces/<wsId>/mcp/policy")
def get_mcp_policy(wsId: str):
    """Get workspace MCP policy."""
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    try:
        policy = _get_workspace_policy(ws_id)
        return success({"policy": policy})
    except Exception:
        logger.exception("get_mcp_policy failed for workspace=%s", ws_id)
        return internal_error()


@router.put("/api/workspaces/<wsId>/mcp/policy")
def update_mcp_policy(wsId: str):
    """Update workspace MCP policy (admin only)."""
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    policy = body.get("policy", {})

    # Validate mode
    mode = policy.get("mode", "")
    if mode not in ("all", "allowlist", "denylist"):
        return bad_request("mode must be 'all', 'allowlist', or 'denylist'")

    # Ensure lists exist
    if "allowedTargets" not in policy:
        policy["allowedTargets"] = []
    if "deniedTargets" not in policy:
        policy["deniedTargets"] = []

    try:
        from datetime import datetime
        table = _get_ws_table()
        now = datetime.utcnow().isoformat() + "Z"

        table.update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcpPolicy = :p, updated_at = :u",
            ExpressionAttributeValues={
                ":p": policy,
                ":u": now,
            },
            ConditionExpression="attribute_exists(workspaceId)",
        )

        return success({"policy": policy})
    except Exception:
        logger.exception("update_mcp_policy failed for workspace=%s", ws_id)
        return internal_error()


@router.get("/api/workspaces/<wsId>/mcp/targets")
def list_available_targets(wsId: str):
    """List available MCP targets with policy filtering."""
    # Check if admin wants unfiltered list
    show_all = router.current_event.get_query_string_value("all") == "true"

    if show_all:
        # Admin-only for unfiltered list
        user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    else:
        # Regular auth for filtered list
        user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)

    if err:
        return err

    try:
        items = _build_target_list()

        # Apply policy filtering unless show_all
        if not show_all:
            policy = _get_workspace_policy(ws_id)
            items = _filter_by_policy(items, policy)

        return success({"items": items})
    except Exception:
        logger.exception("list_available_targets failed for workspace=%s", ws_id)
        return internal_error()


# --- Tool manifest cache ---
_tool_manifests: dict = {}
_tool_manifests_ttl: dict = {}
_TOOL_MANIFEST_TTL = 300  # 5 minutes


@router.get("/api/workspaces/<wsId>/mcp/targets/<targetName>/tools")
def get_target_tools(wsId: str, targetName: str):
    """Get the tool list for a specific MCP target.

    Reads from pre-cached S3 manifests (mcp/target-tools/{name}.json).
    Falls back to live tools/list via Runtime invocation if no manifest exists.
    """
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    now = time.time()

    # Check cache
    if targetName in _tool_manifests and now - _tool_manifests_ttl.get(targetName, 0) < _TOOL_MANIFEST_TTL:
        return success({"tools": _tool_manifests[targetName]})

    # Try S3 manifest — with fallback for mcp- prefix mismatch
    s3 = boto3.client("s3", region_name=REGION)
    alt = targetName.replace("-", "_")
    candidates = [targetName, f"mcp-{targetName}", f"mcp_{alt}"]
    for key_name in candidates:
        try:
            resp = s3.get_object(Bucket=S3_BUCKET, Key=f"mcp/target-tools/{key_name}.json")
            tools = json.loads(resp["Body"].read().decode())
            _tool_manifests[targetName] = tools
            _tool_manifests_ttl[targetName] = now
            return success({"tools": tools})
        except Exception:
            continue

    # No manifest cached and no S3 object. The tool-list for a target is
    # produced by deploy-mcp.sh at deploy time (it runs a one-shot
    # tools/list against the newly-built runtime and dumps the result to
    # S3). If the manifest is missing, the target exists in the registry
    # but nobody has generated its manifest yet — rerun deploy-mcp.sh for
    # that target. We deliberately do NOT try a live tools/list from
    # Lambda: cold Runtime invocation from a Lambda Function can exceed
    # the 30s budget and the caller just sees a timeout instead of an
    # actionable hint.
    _tool_manifests[targetName] = []
    _tool_manifests_ttl[targetName] = now
    return success({
        "tools": [],
        "hint": "Tool manifest not yet generated. Run "
                "scripts/deploy-mcp.sh for this target to populate "
                "mcp/target-tools/<name>.json in S3.",
    })

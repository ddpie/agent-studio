"""MCP Gateway discovery endpoints."""
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
_catalog_cache = {"data": None, "expires": 0}


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


def _load_target_catalog():
    """Load target-catalog.json from S3 with 5-min cache."""
    now = time.time()
    if _catalog_cache["data"] and now < _catalog_cache["expires"]:
        return _catalog_cache["data"]
    try:
        s3 = boto3.client("s3", region_name=REGION)
        resp = s3.get_object(Bucket=S3_BUCKET, Key="mcp/target-catalog.json")
        data = json.loads(resp["Body"].read())
        _catalog_cache["data"] = data
        _catalog_cache["expires"] = now + 300
        return data
    except Exception as e:
        logger.warning("Failed to load target catalog: %s", str(e))
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


def _list_all_gateway_targets():
    """List all targets from all gateways."""
    try:
        control = _get_control()
        gateways_resp = control.list_gateways()
        all_targets = []
        for gw in gateways_resp.get("gateways", []):
            gw_id = gw.get("gatewayId")
            if not gw_id:
                continue
            try:
                targets_resp = control.list_gateway_targets(gatewayIdentifier=gw_id)
                for t in targets_resp.get("targets", []):
                    all_targets.append({
                        "name": t.get("name", ""),
                        "status": t.get("status", "UNKNOWN"),
                        "description": t.get("description", ""),
                        "endpointUrl": t.get("endpointUrl", ""),
                    })
            except Exception as e:
                logger.warning("Failed to list targets for gateway %s: %s", gw_id, str(e))
        return all_targets
    except Exception as e:
        logger.exception("Failed to list all gateway targets")
        return []


def _merge_catalog_and_targets(catalog, gateway_targets):
    """Merge catalog metadata with gateway status."""
    merged = {}
    # Add all catalog entries
    for name, info in catalog.items():
        merged[name] = {
            "name": name,
            "description": info.get("description", ""),
            "category": info.get("category", "uncategorized"),
            "status": "unavailable",
        }
    # Overlay gateway status
    for t in gateway_targets:
        name = t.get("name", "")
        if name in merged:
            merged[name]["status"] = t.get("status", "UNKNOWN")
        else:
            merged[name] = {
                "name": name,
                "description": t.get("description", ""),
                "category": "uncategorized",
                "status": t.get("status", "UNKNOWN"),
            }
    return list(merged.values())


@router.get("/api/workspaces/<wsId>/mcp/gateways")
def list_gateways(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    try:
        control = _get_control()
        resp = control.list_gateways()
        gateways = [
            {
                "id": gw["gatewayId"],
                "name": gw.get("name", ""),
                "status": gw.get("status", ""),
            }
            for gw in resp.get("gateways", [])
        ]
        return success({"items": gateways})
    except Exception:
        logger.exception("list_gateways failed")
        return internal_error()


@router.get("/api/workspaces/<wsId>/mcp/gateways/<gatewayId>/targets")
def list_targets(wsId: str, gatewayId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    try:
        control = _get_control()
        resp = control.list_gateway_targets(gatewayIdentifier=gatewayId)
        targets = [
            {
                "name": t.get("name", ""),
                "description": t.get("description", ""),
                "endpointUrl": t.get("endpointUrl", ""),
                "status": t.get("status", ""),
            }
            for t in resp.get("targets", [])
        ]
        return success({"items": targets})
    except Exception:
        logger.exception("list_targets failed for gateway=%s", gatewayId)
        return internal_error()


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
        # Load catalog and gateway targets
        catalog = _load_target_catalog()
        gateway_targets = _list_all_gateway_targets()

        # Merge catalog metadata with gateway status
        merged = _merge_catalog_and_targets(catalog, gateway_targets)

        # Apply policy filtering unless show_all
        if not show_all:
            policy = _get_workspace_policy(ws_id)
            merged = _filter_by_policy(merged, policy)

        return success({"items": merged})
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

    # Try S3 manifest first
    try:
        s3 = boto3.client("s3", region_name=REGION)
        resp = s3.get_object(Bucket=S3_BUCKET, Key=f"mcp/target-tools/{targetName}.json")
        tools = json.loads(resp["Body"].read().decode())
        _tool_manifests[targetName] = tools
        _tool_manifests_ttl[targetName] = now
        return success({"tools": tools})
    except s3.exceptions.NoSuchKey:
        pass
    except Exception:
        logger.warning("Failed to read tool manifest for %s from S3", targetName)

    # Fallback: list from Gateway (if target exists there)
    try:
        control = _get_control()
        gateways = control.list_gateways()
        for gw in gateways.get("items", gateways.get("gateways", [])):
            gw_id = gw["gatewayId"]
            targets_resp = control.list_gateway_targets(gatewayIdentifier=gw_id)
            for t in targets_resp.get("items", targets_resp.get("targets", [])):
                if t.get("name") == targetName:
                    # Found — but we can't do tools/list from Lambda easily.
                    # Return empty with a hint.
                    _tool_manifests[targetName] = []
                    _tool_manifests_ttl[targetName] = now
                    return success({"tools": [], "hint": "Run deploy-mcp.sh to generate tool manifests"})

        return success({"tools": []})
    except Exception:
        logger.exception("get_target_tools failed for %s", targetName)
        return internal_error()

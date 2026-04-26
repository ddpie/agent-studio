"""list_mcp_servers — List available MCP tool servers from AgentCore Gateway."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import current_workspace


def _load_catalog() -> dict:
    """Load target catalog from S3 for category metadata."""
    try:
        s3 = boto3.client("s3", region_name=REGION)
        resp = s3.get_object(Bucket=S3_BUCKET, Key="mcp/target-catalog.json")
        items = json.loads(resp["Body"].read().decode())
        return {item["name"]: item for item in items}
    except Exception:
        return {}


def _load_registry_iam_policies() -> dict:
    """Load iam_policy declarations from mcp-registry.yaml.

    Returns a dict mapping target short name → iam_policy (dict or None).
    Targets not present in the registry are treated as having no extra
    IAM requirements (i.e. granted by default).
    """
    try:
        import yaml
        s3 = boto3.client("s3", region_name=REGION)
        resp = s3.get_object(Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml")
        registry = yaml.safe_load(resp["Body"].read().decode())
        policies = {}
        for t in registry.get("remote_targets", []):
            policies[t["name"]] = t.get("iam_policy")
        for t in registry.get("runtime_targets", []):
            policies[t["name"]] = t.get("iam_policy")
        return policies
    except Exception:
        return {}


def _get_workspace_role_arn(workspace_id: str) -> str | None:
    """Read the workspace's custom IAM role ARN from DDB, or None if unset."""
    if not workspace_id:
        return None
    try:
        table = boto3.resource("dynamodb", region_name=REGION).Table("agent-studio-workspaces")
        item = table.get_item(Key={"workspaceId": workspace_id, "sk": "META"}).get("Item", {})
        return item.get("roleArn") or None
    except Exception:
        return None


def _check_iam_permissions(role_arn: str, iam_policy: dict) -> dict:
    """Simulate the workspace role against the required IAM actions.

    Returns {"granted": bool, "missing_actions": [...]}.
    """
    # Extract all required actions from the policy statements
    required_actions = []
    for stmt in iam_policy.get("Statement", []):
        if stmt.get("Effect") == "Allow":
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            required_actions.extend(actions)

    if not required_actions:
        return {"granted": True, "missing_actions": []}

    # TODO: Call iam:SimulatePrincipalPolicy once check_workspace_permissions
    # tool is available. For now, structure the data flow and mark as
    # requires-simulation so the caller knows the check was not performed.
    # The actual simulation logic:
    #
    #   iam_client = boto3.client("iam", region_name=REGION)
    #   missing = []
    #   for i in range(0, len(required_actions), 25):
    #       batch = required_actions[i:i+25]
    #       resp = iam_client.simulate_principal_policy(
    #           PolicySourceArn=role_arn,
    #           ActionNames=batch,
    #           ResourceArns=["*"],
    #       )
    #       for r in resp["EvaluationResults"]:
    #           if r["EvalDecision"] != "allowed":
    #               missing.append(r["EvalActionName"])
    #   return {"granted": len(missing) == 0, "missing_actions": missing}

    try:
        iam_client = boto3.client("iam", region_name=REGION)
        missing = []
        for i in range(0, len(required_actions), 25):
            batch = required_actions[i:i + 25]
            resp = iam_client.simulate_principal_policy(
                PolicySourceArn=role_arn,
                ActionNames=batch,
                ResourceArns=["*"],
            )
            for r in resp["EvaluationResults"]:
                if r["EvalDecision"] != "allowed":
                    missing.append(r["EvalActionName"])
        return {"granted": len(missing) == 0, "missing_actions": missing}
    except Exception as e:
        # If simulation fails (e.g. insufficient permissions on Meta-Agent role),
        # report as not-granted with the error so the caller can troubleshoot.
        return {
            "granted": False,
            "missing_actions": required_actions,
            "simulation_error": str(e),
        }


def _enrich_with_permissions(target_name: str, iam_policies: dict, workspace_role_arn: str | None) -> dict:
    """Compute the permission grant status for a single MCP target.

    Returns a dict with:
      - granted: bool
      - reason: str (only when granted=False)
      - missing_actions: list (only when granted=False and role exists)
      - iam_policy: the raw policy declaration (for frontend display)
    """
    iam_policy = iam_policies.get(target_name)

    # No iam_policy declared → platform tool, always granted
    if iam_policy is None:
        return {"granted": True}

    # iam_policy declared but workspace has no custom role → cannot grant
    if not workspace_role_arn:
        return {
            "granted": False,
            "reason": "no_workspace_role",
            "iam_policy": iam_policy,
        }

    # Workspace has a custom role → simulate permissions
    result = _check_iam_permissions(workspace_role_arn, iam_policy)
    out = {"granted": result["granted"], "iam_policy": iam_policy}
    if not result["granted"]:
        out["reason"] = "missing_permissions"
        out["missing_actions"] = result.get("missing_actions", [])
        if "simulation_error" in result:
            out["simulation_error"] = result["simulation_error"]
    return out


def _list_gateway_targets() -> list:
    """List all gateway targets with pagination."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    gateways = control.list_gateways()
    all_targets = []

    for gw in gateways.get("items", gateways.get("gateways", [])):
        gw_id = gw["gatewayId"]
        gw_name = gw.get("name", "")

        resp = control.list_gateway_targets(gatewayIdentifier=gw_id)
        targets = resp.get("items", resp.get("targets", []))
        while resp.get("nextToken"):
            resp = control.list_gateway_targets(
                gatewayIdentifier=gw_id, nextToken=resp["nextToken"]
            )
            targets.extend(resp.get("items", resp.get("targets", [])))

        for t in targets:
            all_targets.append({
                "gateway_id": gw_id,
                "gateway_name": gw_name,
                "target_name": t.get("name", ""),
                "status": t.get("status", ""),
            })

    return all_targets


@tool
def list_mcp_servers() -> str:
    """List available MCP tool servers from AgentCore Gateway.

    Returns categorized MCP targets with status and permission grant info.
    Use this to show users what MCP tools are available when creating or
    updating agents. Targets with ``granted: false`` should not be included
    in agent proposals — tell the user what permissions are missing and how
    to grant them.

    Returns:
        JSON with summary, targets grouped by category, and per-target
        ``granted`` / ``reason`` / ``missing_actions`` fields.
    """
    try:
        catalog = _load_catalog()
        targets = _list_gateway_targets()
        iam_policies = _load_registry_iam_policies()

        # Load workspace role for permission checks
        ws_id = current_workspace()
        workspace_role_arn = _get_workspace_role_arn(ws_id) if ws_id else None

        # Merge gateway status with catalog metadata.
        # Strip the "mcp-" prefix that gateway targets carry — the short name
        # (e.g. "cloudwatch") is the contract everywhere else: the frontend
        # /mcp/targets API returns short names, workspace policy stores short
        # names, and _resolve_mcp_endpoints expects short names so it can map
        # "nova-canvas" → runtime "nova_canvas". Leaving the prefix on here
        # caused Meta-Agent to write "mcp-cloudwatch" into proposals, which
        # then failed to match the selector's short names in the editor and
        # would have 404'd at runtime resolution.
        merged = []
        for t in targets:
            gw_name = t["target_name"]
            short = gw_name[len("mcp-"):] if gw_name.startswith("mcp-") else gw_name
            cat_entry = catalog.get(short, {})
            perm = _enrich_with_permissions(short, iam_policies, workspace_role_arn)
            entry = {
                "name": short,
                "description": cat_entry.get("description", ""),
                "category": cat_entry.get("category", "general"),
                "status": t["status"],
                **perm,
            }
            merged.append(entry)

        # Group by category
        by_category: dict[str, list] = {}
        for m in merged:
            cat = m["category"]
            by_category.setdefault(cat, []).append(m)

        ready_count = sum(1 for m in merged if m["status"] == "READY")
        denied_count = sum(1 for m in merged if not m.get("granted", True))

        result = {
            "total": len(merged),
            "ready": ready_count,
            "denied": denied_count,
            "workspace_role": workspace_role_arn or None,
            "categories": {
                cat: [
                    {k: v for k, v in t.items() if k != "category"}
                    for t in targets_list
                ]
                for cat, targets_list in sorted(by_category.items())
            },
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

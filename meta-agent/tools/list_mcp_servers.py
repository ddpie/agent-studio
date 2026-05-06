"""list_mcp_servers — List available MCP tool servers.

Source of truth is mcp-runtime/mcp-registry.yaml (S3-mirrored). Runtime
status comes from list_agent_runtimes. The AgentCore Gateway is NOT read
here — see lambda/crud/mcp.py for the same reasoning. Agents invoke MCP
servers by resolving Runtime ARNs directly (agent_template_v2.py), so
the Gateway is no longer on any hot path.
"""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import current_workspace


def _load_registry() -> dict:
    """Load mcp-registry.yaml from S3.

    Returns the parsed dict with "remote_targets" and "runtime_targets".
    Returns empty lists on failure so callers can still render a useful
    (though empty) result.
    """
    try:
        import yaml
        s3 = boto3.client("s3", region_name=REGION)
        resp = s3.get_object(Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml")
        data = yaml.safe_load(resp["Body"].read().decode()) or {}
        data.setdefault("remote_targets", [])
        data.setdefault("runtime_targets", [])
        return data
    except Exception:
        return {"remote_targets": [], "runtime_targets": []}


def _iam_policies_from_registry(registry: dict) -> dict:
    """Extract iam_policy declarations from the registry by target name.

    Targets not present in the registry are treated as having no extra
    IAM requirements (i.e. granted by default).
    """
    policies = {}
    for t in registry.get("remote_targets", []):
        policies[t["name"]] = t.get("iam_policy")
    for t in registry.get("runtime_targets", []):
        policies[t["name"]] = t.get("iam_policy")
    return policies


def _runtime_name_candidates(short_name: str) -> set:
    """Set of agent-runtime names a registry entry might match.

    Mirror of lambda/crud/mcp.py and agent_template_v2._resolve_runtime_url
    so the discovery side and the agent invocation side stay aligned.
    """
    base = short_name.replace("-", "_")
    return {base, f"mcp_{base}"}


def _list_deployed_runtimes() -> dict:
    """Paginate list_agent_runtimes into {agentRuntimeName: item}."""
    try:
        control = boto3.client("bedrock-agentcore-control", region_name=REGION)
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


def _build_targets_from_registry(registry: dict, deployed: dict, iam_policies: dict, workspace_role_arn: str | None) -> list:
    """Produce the list-of-targets view used by list_mcp_servers.

    Reads every ``enabled`` and non-``deprecated`` entry from the registry,
    attaches the live status for runtime entries (looked up in
    list_agent_runtimes), and enriches each entry with the workspace's
    permission check against the declared iam_policy.
    """
    merged = []
    for t in registry.get("remote_targets", []):
        if not t.get("enabled") or t.get("deprecated"):
            continue
        name = t["name"]
        perm = _enrich_with_permissions(name, iam_policies, workspace_role_arn)
        merged.append({
            "name": name,
            "description": t.get("description", ""),
            "category": t.get("category", "general"),
            "type": "remote",
            "status": "READY",
            **perm,
        })

    for t in registry.get("runtime_targets", []):
        if not t.get("enabled") or t.get("deprecated"):
            continue
        name = t["name"]
        live = next(
            (deployed[n] for n in _runtime_name_candidates(name) if n in deployed),
            None,
        )
        status = (live or {}).get("status", "unavailable")
        perm = _enrich_with_permissions(name, iam_policies, workspace_role_arn)
        merged.append({
            "name": name,
            "description": t.get("description", ""),
            "category": t.get("category", "general"),
            "type": "runtime",
            "status": status,
            **perm,
        })
    return merged


@tool
def list_mcp_servers() -> str:
    """List available MCP tool servers for the current workspace.

    Reads mcp-runtime/mcp-registry.yaml (source of truth for what exists)
    and list_agent_runtimes (source of truth for current runtime health).
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
        registry = _load_registry()
        deployed = _list_deployed_runtimes()
        iam_policies = _iam_policies_from_registry(registry)

        # Load workspace role for permission checks
        ws_id = current_workspace()
        workspace_role_arn = _get_workspace_role_arn(ws_id) if ws_id else None

        merged = _build_targets_from_registry(
            registry, deployed, iam_policies, workspace_role_arn,
        )

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

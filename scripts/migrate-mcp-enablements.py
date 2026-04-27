#!/usr/bin/env python3
"""Migrate legacy workspace MCP grants to per-workspace MCP runtimes (v4).

Phase 6 / 7 migration driver for the per-workspace MCP refactor
(spec §10.2, plan §Phase 7).

Modes:
  --redeploy-agents        redeploy every Agent with mcp_targets != []
                           (Phase 7 step 3: bakes runtime_endpoint literal
                           into config.json so Agents stop calling
                           list_agent_runtimes at runtime)
  --all                    for every workspace that has legacy mcpGrants,
                           enable each grant as a per-workspace MCP runtime
  --workspace <id>         process a single workspace (with --all implied
                           unless --target is also passed)
  --target <name>          process a single target (implies --workspace)
  --rollback --workspace   pop every mcp_runtimes[*] in this workspace
                           (disable path; use if migration produced bad state)
  --dry-run                print actions only, don't mutate

Common:
  --region <region>        AWS region (defaults to AGENT_STUDIO_REGION env
                           var or us-east-1)
  --api-url <url>          API Gateway base URL (defaults to
                           AGENT_STUDIO_API_URL env var)
  --concurrency <N>        parallel actions within a workspace (default 1
                           — serial is safer given quota + inflight locks)

Auth: uses AWS IAM credentials from the calling shell (direct DDB + AgentCore
control-plane; bypasses the HTTP CRUD endpoint because that requires a
Cognito token that a script can't easily mint). Reads workspaces directly
from DDB, invokes CreateAgentRuntime + PutResourcePolicy + PutRolePolicy.

For a one-workspace spot check, prefer the UI at /#/mcp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import boto3

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml required. pip install pyyaml", file=sys.stderr)
    sys.exit(2)


# ─────────────────────────────────────────────────────────────────────


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_registry() -> dict:
    path = _project_root() / "mcp-runtime" / "mcp-registry.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


def _load_ceiling_actions() -> tuple[set[str], str]:
    """Import ceiling action set from the Lambda-bundled module."""
    gen = _project_root() / "lambda" / "crud" / "generated" / "ceiling_actions.py"
    if not gen.exists():
        print(
            "ERROR: ceiling_actions.py not generated. Run: "
            "python scripts/sync-mcp-iam-policies.py --export-ceiling",
            file=sys.stderr,
        )
        sys.exit(2)
    ns: dict = {}
    exec(gen.read_text(), ns)
    return set(ns["_CEILING_ALLOW_ACTIONS"]), ns["CEILING_HASH"]


def _action_matches(required: str, pattern: str) -> bool:
    import fnmatch
    return fnmatch.fnmatchcase(required.lower(), pattern.lower())


def _ws_hash12(ws_id: str) -> str:
    return hashlib.sha256(ws_id.encode()).hexdigest()[:12]


def _runtime_endpoint_url(region: str, arn: str) -> str:
    encoded = urllib.parse.quote(arn, safe="")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/"
        f"runtimes/{encoded}/invocations?qualifier=DEFAULT"
    )


def _now_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


# ─────────────────────────────────────────────────────────────────────


@dataclass
class Ctx:
    region: str
    account_id: str
    dry_run: bool
    registry: dict
    ceiling_actions: set[str]
    workspaces_table: str
    agents_table: str
    ddb = None
    control = None
    iam = None
    ecr = None

    def __post_init__(self):
        self.ddb = boto3.resource("dynamodb", region_name=self.region)
        self.control = boto3.client("bedrock-agentcore-control", region_name=self.region)
        self.iam = boto3.client("iam", region_name=self.region)
        self.ecr = boto3.client("ecr", region_name=self.region)

    def ws_table(self):
        return self.ddb.Table(self.workspaces_table)

    def agent_table(self):
        return self.ddb.Table(self.agents_table)


# ─────────────────────────────────────────────────────────────────────
# Workspace discovery
# ─────────────────────────────────────────────────────────────────────


def list_workspaces(ctx: Ctx) -> list[dict]:
    """Return all workspace META items."""
    rows = []
    kwargs = {
        "FilterExpression": "sk = :meta",
        "ExpressionAttributeValues": {":meta": "META"},
    }
    while True:
        resp = ctx.ws_table().scan(**kwargs)
        rows.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return rows


# ─────────────────────────────────────────────────────────────────────
# Enable flow (mimics lambda/crud/mcp_runtime_manager.py::enable_target)
# ─────────────────────────────────────────────────────────────────────


def find_registry_target(ctx: Ctx, target: str) -> dict | None:
    for t in (ctx.registry.get("runtime_targets") or []):
        if t.get("name") == target:
            return t
    return None


def check_boundary_intersection(ctx: Ctx, target: str) -> tuple[bool, list[str]]:
    reg = find_registry_target(ctx, target)
    if not reg or not reg.get("iam_policy"):
        return True, []
    required = []
    for stmt in reg["iam_policy"].get("Statement", []):
        if stmt.get("Effect") != "Allow":
            continue
        actions = stmt.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        required.extend(actions)
    missing = [
        a for a in required
        if not any(_action_matches(a, p) for p in ctx.ceiling_actions)
    ]
    return (len(missing) == 0), missing


def merge_workspace_grants(
    ctx: Ctx, role_name: str, grants: list[str]
) -> int:
    """Rebuild WorkspaceGrants with dedup + 9500-byte guard. Returns policy size."""
    policies_by_name: dict[str, dict] = {}
    for t in (ctx.registry.get("runtime_targets") or []):
        if t.get("iam_policy"):
            policies_by_name[t["name"]] = t["iam_policy"]

    statements = []
    seen: set = set()
    for g in sorted(set(grants)):
        policy = policies_by_name.get(g)
        if not policy:
            continue
        for stmt in policy.get("Statement", []):
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            resource = stmt.get("Resource", "*")
            if isinstance(resource, list):
                resource = tuple(sorted(resource))
            else:
                resource = (str(resource),)
            condition = json.dumps(stmt.get("Condition", {}), sort_keys=True)
            key = (stmt.get("Effect", "Allow"), frozenset(actions), resource, condition)
            if key in seen:
                continue
            seen.add(key)
            statements.append(stmt)

    if not statements:
        if ctx.dry_run:
            print(f"    [DRY] delete_role_policy({role_name}/WorkspaceGrants)")
            return 0
        try:
            ctx.iam.delete_role_policy(RoleName=role_name, PolicyName="WorkspaceGrants")
        except ctx.iam.exceptions.NoSuchEntityException:
            pass
        return 0

    policy_doc = json.dumps({"Version": "2012-10-17", "Statement": statements})
    if len(policy_doc) > 9500:
        raise RuntimeError(f"WorkspaceGrants exceeds 9500B: {len(policy_doc)}")
    if ctx.dry_run:
        print(f"    [DRY] put_role_policy({role_name}/WorkspaceGrants, size={len(policy_doc)}B)")
        return len(policy_doc)
    ctx.iam.put_role_policy(
        RoleName=role_name, PolicyName="WorkspaceGrants", PolicyDocument=policy_doc,
    )
    return len(policy_doc)


def apply_resource_policy(ctx: Ctx, runtime_arn: str, role_arn: str) -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": role_arn},
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": runtime_arn,
            },
            {
                "Effect": "Deny",
                "NotPrincipal": {"AWS": role_arn},
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": runtime_arn,
            },
        ],
    }
    if ctx.dry_run:
        print(f"    [DRY] put_resource_policy({runtime_arn})")
        return
    ctx.control.put_resource_policy(
        resourceArn=runtime_arn, policy=json.dumps(policy),
    )


def ensure_target_enabled(
    ctx: Ctx,
    ws_id: str,
    ws_meta: dict,
    target: str,
    wait_ready_sec: int = 600,
) -> str:
    """Enable target idempotently. Returns 'skipped' | 'enabled' | 'failed:...'."""
    # Already enabled?
    existing = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if existing and existing.get("status") in ("READY", "ACTIVE"):
        return "skipped (already READY)"
    if existing and existing.get("status") in ("CREATING", "UPDATING"):
        return "skipped (in-flight)"

    reg = find_registry_target(ctx, target)
    if not reg:
        return f"failed: target '{target}' not in registry"
    if reg.get("vpc_required"):
        return f"skipped (vpc_required)"
    if not reg.get("enabled", True):
        return f"skipped (registry disabled)"
    if reg.get("requires_env"):
        return f"failed: requires_env not implemented ({reg['requires_env']})"

    # Boundary intersection
    ok, missing = check_boundary_intersection(ctx, target)
    if not ok:
        return f"failed: boundary_gap ({', '.join(missing)})"

    role_arn = ws_meta.get("roleArn")
    role_name = ws_meta.get("roleName")
    if not role_arn:
        return "failed: workspace has no roleArn"

    # ECR image exists
    version = reg.get("version") or "latest"
    image_uri = f"{ctx.account_id}.dkr.ecr.{ctx.region}.amazonaws.com/mcp-{target}:{version}"
    try:
        ctx.ecr.describe_images(
            repositoryName=f"mcp-{target}",
            imageIds=[{"imageTag": version}],
        )
    except Exception as e:
        return f"failed: image_missing ({e})"

    current_grants = list(ws_meta.get("mcpGrants", []) or [])
    new_grants = sorted(set(current_grants) | {target})

    # Merge WorkspaceGrants
    try:
        merge_workspace_grants(ctx, role_name, new_grants)
    except Exception as e:
        return f"failed: grant_merge ({e})"

    # Build runtime name
    ws12 = _ws_hash12(ws_id)
    target_norm = target.replace("-", "_")
    runtime_name = f"asmcp_{ws12}_{target_norm}"
    if len(runtime_name) > 48:
        return f"failed: runtime name too long ({runtime_name})"

    client_token = hashlib.sha256(
        f"{ws_id}:{target}:{runtime_name}".encode()
    ).hexdigest()[:32]

    if ctx.dry_run:
        print(f"    [DRY] create_agent_runtime({runtime_name}, role={role_arn})")
        return "dry-run: would enable"

    try:
        resp = ctx.control.create_agent_runtime(
            agentRuntimeName=runtime_name,
            description=f"MCP {target} for ws {ws_id[:8]}",
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
            protocolConfiguration={"serverProtocol": "MCP"},
            networkConfiguration={"networkMode": "PUBLIC"},
            roleArn=role_arn,
            clientToken=client_token,
        )
        runtime_id = resp.get("agentRuntimeId")
        runtime_arn = resp.get("agentRuntimeArn", "")
    except ctx.control.exceptions.ConflictException:
        # Deterministic client_token retry → already created. Look it up.
        runtime_id, runtime_arn = _find_runtime_by_name(ctx, runtime_name)
        if not runtime_id:
            return "failed: conflict but can't locate runtime"
    except Exception as e:
        # Rollback grants
        try:
            merge_workspace_grants(ctx, role_name, current_grants)
        except Exception:
            pass
        return f"failed: create ({e})"

    # Apply resource policy
    try:
        apply_resource_policy(ctx, runtime_arn, role_arn)
    except Exception as e:
        try:
            ctx.control.delete_agent_runtime(agentRuntimeId=runtime_id)
            merge_workspace_grants(ctx, role_name, current_grants)
        except Exception:
            pass
        return f"failed: resource_policy ({e})"

    # Write DDB entry (CREATING)
    endpoint = _runtime_endpoint_url(ctx.region, runtime_arn) if runtime_arn else ""
    entry = {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
        "runtime_name": runtime_name,
        "runtime_endpoint": endpoint,
        "status": "CREATING",
        "image_version": version,
        "image_uri": image_uri,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "last_error": None,
        "created_by": "migrate-mcp-enablements.py",
        "inflight_action": "CREATING",
        "inflight_actor": "migrate",
        "resource_policy_set": True,
    }
    try:
        ctx.ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcp_runtimes = if_not_exists(mcp_runtimes, :empty)",
            ExpressionAttributeValues={":empty": {}},
        )
        ctx.ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcp_runtimes.#t = :e, mcpGrants = :g, updated_at = :now",
            ExpressionAttributeNames={"#t": target},
            ExpressionAttributeValues={":e": entry, ":g": new_grants, ":now": _now_iso()},
        )
    except Exception as e:
        return f"failed: ddb ({e})"

    # Wait READY
    deadline = time.time() + wait_ready_sec
    last_status = "CREATING"
    while time.time() < deadline:
        time.sleep(15)
        try:
            info = ctx.control.get_agent_runtime(agentRuntimeId=runtime_id)
            last_status = info.get("status", "")
            if last_status in ("READY", "ACTIVE"):
                # Flush to READY
                ctx.ws_table().update_item(
                    Key={"workspaceId": ws_id, "sk": "META"},
                    UpdateExpression=(
                        "SET mcp_runtimes.#t.#s = :r, "
                        "mcp_runtimes.#t.inflight_action = :null, "
                        "mcp_runtimes.#t.updated_at = :now"
                    ),
                    ExpressionAttributeNames={"#t": target, "#s": "status"},
                    ExpressionAttributeValues={
                        ":r": "READY", ":null": None, ":now": _now_iso(),
                    },
                )
                return "enabled (READY)"
            if last_status in ("FAILED", "CREATE_FAILED"):
                reason = info.get("failureReason") or info.get("statusReason") or "?"
                return f"failed: runtime {last_status} ({reason})"
        except Exception as e:
            print(f"    poll failed: {e}", file=sys.stderr)

    return f"failed: timeout (last={last_status})"


def _find_runtime_by_name(ctx: Ctx, name: str) -> tuple[str | None, str]:
    next_token = None
    while True:
        kw = {"nextToken": next_token} if next_token else {}
        resp = ctx.control.list_agent_runtimes(**kw)
        items = (
            resp.get("agentRuntimes")
            or resp.get("agentRuntimeSummaries")
            or resp.get("runtimes")
            or []
        )
        for rt in items:
            rt_name = rt.get("agentRuntimeName") or rt.get("name")
            if rt_name == name:
                rt_id = rt.get("agentRuntimeId") or rt.get("runtimeId")
                if rt_id:
                    info = ctx.control.get_agent_runtime(agentRuntimeId=rt_id)
                    return rt_id, info.get("agentRuntimeArn", "")
        next_token = resp.get("nextToken")
        if not next_token:
            return None, ""


# ─────────────────────────────────────────────────────────────────────
# Disable / rollback
# ─────────────────────────────────────────────────────────────────────


def disable_target(ctx: Ctx, ws_id: str, ws_meta: dict, target: str) -> str:
    entry = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if not entry:
        return "skipped (not enabled)"

    runtime_id = entry.get("runtime_id")
    role_name = ws_meta.get("roleName")

    if ctx.dry_run:
        print(f"    [DRY] delete_agent_runtime({runtime_id})")
        return "dry-run: would disable"

    if runtime_id:
        try:
            ctx.control.delete_agent_runtime(agentRuntimeId=runtime_id)
        except Exception as e:
            print(f"    delete_agent_runtime failed: {e}", file=sys.stderr)

    current_grants = list(ws_meta.get("mcpGrants", []) or [])
    new_grants = sorted(set(current_grants) - {target})
    if role_name:
        try:
            merge_workspace_grants(ctx, role_name, new_grants)
        except Exception as e:
            print(f"    grant shrink failed: {e}", file=sys.stderr)

    try:
        ctx.ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="REMOVE mcp_runtimes.#t SET mcpGrants = :g, updated_at = :now",
            ExpressionAttributeNames={"#t": target},
            ExpressionAttributeValues={":g": new_grants, ":now": _now_iso()},
        )
    except Exception as e:
        return f"failed: ddb ({e})"

    return "disabled"


# ─────────────────────────────────────────────────────────────────────
# Agent redeploy (Phase 3 pre-step)
# ─────────────────────────────────────────────────────────────────────


def list_agents_with_mcp(ctx: Ctx) -> list[dict]:
    rows = []
    kwargs: dict = {
        "FilterExpression": "attribute_exists(mcp_targets) AND size(mcp_targets) > :zero",
        "ExpressionAttributeValues": {":zero": 0},
    }
    while True:
        resp = ctx.agent_table().scan(**kwargs)
        rows.extend(resp.get("Items", []))
        if not resp.get("LastEvaluatedKey"):
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return rows


def redeploy_agents(ctx: Ctx) -> tuple[int, int]:
    """Invoke Meta-Agent's update_agent tool for each Agent with mcp_targets.

    Meta-Agent's update_agent repacks + re-uploads using current DDB state,
    which picks up the new _resolve_mcp_endpoints (Phase 2 T2.5) that bakes
    runtime_arn + runtime_endpoint into config.json.

    Returns (ok_count, err_count).
    """
    agents = list_agents_with_mcp(ctx)
    print(f"Found {len(agents)} Agent(s) with mcp_targets != []")
    if ctx.dry_run:
        for a in agents:
            print(f"  [DRY] would redeploy {a.get('agentId')} ({a.get('name')})")
        return len(agents), 0

    # We need the Meta-Agent to do the actual deploy. For a script-driven
    # migration we call meta_agent's HTTP POST /invoke endpoint with an
    # instruction like "redeploy agent {id}".
    #
    # Pragmatic alternative: tell the operator to run /#/agents/<id>/edit
    # → Save → Deploy per Agent. Given the v4 rollout happens on a dev env
    # with few Agents, this is acceptable; we don't need to drive the
    # Meta-Agent from a script.
    print(
        "NOTE: Automatic redeploy from script requires Meta-Agent invocation;\n"
        "      for dev env, manually redeploy each Agent via the UI (Save button\n"
        "      on /#/agents/<id>/edit triggers the current _resolve_mcp_endpoints).\n"
        "Listing affected Agents:"
    )
    for a in agents:
        print(f"  - {a.get('agentId')} ({a.get('name')}) targets={a.get('mcp_targets')}")
    return 0, 0


# ─────────────────────────────────────────────────────────────────────
# Main flows
# ─────────────────────────────────────────────────────────────────────


def migrate_workspace(
    ctx: Ctx, ws_meta: dict, target_filter: str | None = None,
) -> dict:
    ws_id = ws_meta["workspaceId"]
    grants = list(ws_meta.get("mcpGrants", []) or [])
    if target_filter:
        grants = [g for g in grants if g == target_filter]
    if not grants:
        print(f"  ws={ws_id} (no grants to migrate)")
        return {"ws_id": ws_id, "results": {}}

    print(f"  ws={ws_id} grants={grants}")
    results = {}
    for target in grants:
        # Re-fetch meta each loop so mcp_runtimes stays current between enables
        fresh = ctx.ws_table().get_item(
            Key={"workspaceId": ws_id, "sk": "META"},
        ).get("Item") or ws_meta
        result = ensure_target_enabled(ctx, ws_id, fresh, target)
        results[target] = result
        print(f"    {target}: {result}")
    return {"ws_id": ws_id, "results": results}


def rollback_workspace(ctx: Ctx, ws_meta: dict) -> dict:
    ws_id = ws_meta["workspaceId"]
    runtimes = list((ws_meta.get("mcp_runtimes") or {}).keys())
    print(f"  ws={ws_id} rollback targets={runtimes}")
    results = {}
    for target in runtimes:
        fresh = ctx.ws_table().get_item(
            Key={"workspaceId": ws_id, "sk": "META"},
        ).get("Item") or ws_meta
        result = disable_target(ctx, ws_id, fresh, target)
        results[target] = result
        print(f"    {target}: {result}")
    return {"ws_id": ws_id, "results": results}


# ─────────────────────────────────────────────────────────────────────


def main():
    p = argparse.ArgumentParser(
        description="Migrate legacy MCP grants to per-workspace runtimes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--redeploy-agents", action="store_true",
                   help="List (and with --region) enumerate Agents needing redeploy")
    p.add_argument("--all", action="store_true", help="Process all workspaces")
    p.add_argument("--workspace", help="Single workspace ID")
    p.add_argument("--target", help="Single target (implies --workspace)")
    p.add_argument("--rollback", action="store_true",
                   help="Disable all mcp_runtimes in --workspace (recovery)")
    p.add_argument("--dry-run", action="store_true", help="Print actions only")
    p.add_argument("--region", default=os.getenv("AGENT_STUDIO_REGION", "us-east-1"))
    p.add_argument("--workspaces-table", default="agent-studio-workspaces")
    p.add_argument("--agents-table", default="agent-studio-agents")
    args = p.parse_args()

    if args.target and not args.workspace:
        p.error("--target requires --workspace")
    if args.rollback and not args.workspace:
        p.error("--rollback requires --workspace (safety)")
    if not any([args.redeploy_agents, args.all, args.workspace]):
        p.error("specify one of --redeploy-agents, --all, or --workspace")

    # Resolve account id
    try:
        account_id = boto3.client("sts", region_name=args.region).get_caller_identity()["Account"]
    except Exception as e:
        print(f"ERROR: cannot get caller identity: {e}", file=sys.stderr)
        sys.exit(2)

    registry = _load_registry()
    ceiling, ceiling_hash = _load_ceiling_actions()

    ctx = Ctx(
        region=args.region,
        account_id=account_id,
        dry_run=args.dry_run,
        registry=registry,
        ceiling_actions=ceiling,
        workspaces_table=args.workspaces_table,
        agents_table=args.agents_table,
    )

    print(f"Region: {args.region}  Account: {account_id}  Dry-run: {args.dry_run}")
    print(f"Ceiling hash: {ceiling_hash}  Allow-patterns: {len(ceiling)}")

    if args.redeploy_agents:
        ok, err = redeploy_agents(ctx)
        print(f"\nRedeploy listing done (ok={ok} err={err}).")
        return

    # Enable / rollback path
    if args.workspace:
        item = ctx.ws_table().get_item(
            Key={"workspaceId": args.workspace, "sk": "META"},
        ).get("Item")
        if not item:
            print(f"ERROR: workspace {args.workspace} not found", file=sys.stderr)
            sys.exit(1)
        workspaces = [item]
    elif args.all:
        workspaces = list_workspaces(ctx)
        print(f"Found {len(workspaces)} workspace(s)")
    else:
        workspaces = []

    results = []
    for ws in workspaces:
        if args.rollback:
            results.append(rollback_workspace(ctx, ws))
        else:
            results.append(migrate_workspace(ctx, ws, target_filter=args.target))

    # Summary
    total_ok, total_skipped, total_fail = 0, 0, 0
    for r in results:
        for status in r["results"].values():
            if status.startswith("failed"):
                total_fail += 1
            elif status.startswith("skipped"):
                total_skipped += 1
            else:
                total_ok += 1
    print(f"\nSummary: ok={total_ok} skipped={total_skipped} failed={total_fail}")
    if total_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()

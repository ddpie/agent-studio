"""enable_mcp — Enable an MCP target for the current workspace.

Creates a per-workspace AgentCore Runtime using a shared ECR image, pins a
resource policy to the workspace role, and merges the target's IAM policy
into the workspace role's `WorkspaceGrants` inline policy.

Returns immediately with CREATING status; creation takes ~3–5 min. The
caller should use get_mcp_status(target) to poll.
"""
import hashlib
import json
import os
import urllib.parse
from datetime import datetime
from typing import Any

import boto3
from strands import tool

from config import REGION, ACCOUNT_ID, S3_BUCKET
from tools import _scope


_WORKSPACES_TABLE = os.getenv("WORKSPACES_TABLE", "agent-studio-workspaces")
_INFLIGHT_STATUSES = {"CREATING", "UPDATING", "DELETING"}


def _control():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _iam():
    return boto3.client("iam", region_name=REGION)


def _ws_table():
    return boto3.resource("dynamodb", region_name=REGION).Table(_WORKSPACES_TABLE)


def _s3():
    return boto3.client("s3", region_name=REGION)


def _ecr():
    return boto3.client("ecr", region_name=REGION)


def _ws_hash12(ws_id: str) -> str:
    return hashlib.sha256(ws_id.encode()).hexdigest()[:12]


def _load_registry_target(target: str) -> dict | None:
    try:
        resp = _s3().get_object(Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml")
        import yaml
        data = yaml.safe_load(resp["Body"].read())
        for t in (data.get("runtime_targets") or []):
            if t.get("name") == target:
                return t
        return None
    except Exception:
        return None


def _get_ws_meta(ws_id: str) -> dict | None:
    return _ws_table().get_item(
        Key={"workspaceId": ws_id, "sk": "META"},
        ConsistentRead=True,
    ).get("Item")


def _runtime_endpoint(region: str, arn: str) -> str:
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/"
        f"runtimes/{urllib.parse.quote(arn, safe='')}/invocations?qualifier=DEFAULT"
    )


@tool
def enable_mcp(target: str) -> str:
    """Enable an MCP target in the current workspace.

    Creates a per-workspace MCP runtime and authorizes the workspace role to
    call it. Low+medium sensitivity targets can be enabled by any editor;
    high sensitivity requires admin.

    Args:
        target: Registry target name (e.g. "cloudwatch", "iam").

    Returns:
        JSON: {"status": "CREATING|READY|error", "runtime_name": "...",
               "message": "..."}. If CREATING, poll with get_mcp_status(target)
               every 30s. Typically ready in 3–5 min.
    """
    ws_id = _scope.current_workspace()
    caller = _scope.current_caller()
    if not ws_id:
        return json.dumps({"status": "error", "error": "no_workspace"})

    # 1. Sensitivity-based role gate (spec §6.5 / D11)
    meta = _load_registry_target(target)
    if meta is None:
        return json.dumps({"status": "error", "error": "unknown_target",
                           "message": f"Target '{target}' not in registry."})
    if meta.get("vpc_required"):
        return json.dumps({"status": "error", "error": "unknown_target",
                           "message": f"Target '{target}' requires VPC mode (not supported)."})
    required_env = meta.get("requires_env") or []
    if required_env:
        return json.dumps({"status": "error", "error": "env_missing",
                           "message": f"Target '{target}' needs env vars {required_env}; "
                                      f"contact platform admin."})

    sensitivity = meta.get("sensitivity", "low")
    required_role = "admin" if sensitivity == "high" else "editor"
    err = _scope.require_role(required_role)
    if err:
        return json.dumps({"status": "error", **err})

    # 2. Workspace role check
    ws_meta = _get_ws_meta(ws_id)
    if not ws_meta:
        return json.dumps({"status": "error", "error": "workspace_not_found"})
    role_arn = ws_meta.get("roleArn")
    role_name = ws_meta.get("roleName")
    if not role_arn:
        return json.dumps({
            "status": "error", "error": "role_missing",
            "message": "Workspace role not created. A workspace admin must run "
                       "POST /api/workspaces/{ws}/role first (see /#/admin).",
        })

    # 3. Already enabled / in-flight check
    existing = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if existing:
        status = existing.get("status")
        if status in _INFLIGHT_STATUSES:
            return json.dumps({
                "status": "error", "error": "in_flight",
                "message": f"Target '{target}' is currently {status}; wait for it to finish.",
            })
        if status in ("READY", "ACTIVE"):
            return json.dumps({
                "status": status, "idempotent": True,
                "runtime_name": existing.get("runtime_name"),
                "runtime_arn": existing.get("runtime_arn"),
                "message": f"Target '{target}' already enabled.",
            })

    # 4. Merge WorkspaceGrants
    current_grants = list(ws_meta.get("mcpGrants", []) or [])
    new_grants = sorted(set(current_grants) | {target})
    try:
        _merge_workspace_grants(role_name, new_grants)
    except Exception as e:
        return json.dumps({"status": "error", "error": "policy_write_failed",
                           "message": str(e)})

    # 5. Create runtime
    ws12 = _ws_hash12(ws_id)
    target_norm = target.replace("-", "_")
    runtime_name = f"asmcp_{ws12}_{target_norm}"
    if len(runtime_name) > 48:
        return json.dumps({"status": "error", "error": "name_too_long",
                           "message": f"Runtime name '{runtime_name}' exceeds 48 chars."})
    version = meta.get("version") or "latest"
    image_uri = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/mcp-{target}:{version}"
    client_token = hashlib.sha256(f"{ws_id}:{target}:{runtime_name}".encode()).hexdigest()[:32]

    # 6. ECR pre-check
    try:
        _ecr().describe_images(repositoryName=f"mcp-{target}",
                               imageIds=[{"imageTag": version}])
    except Exception as e:
        return json.dumps({"status": "error", "error": "image_missing",
                           "message": f"ECR image mcp-{target}:{version} not found. "
                                      f"Ask platform admin to run scripts/build-mcp.sh.",
                           "detail": str(e)})

    control = _control()
    try:
        resp = control.create_agent_runtime(
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
    except Exception as e:
        # Rollback
        try:
            _merge_workspace_grants(role_name, current_grants)
        except Exception:
            pass
        return json.dumps({"status": "error", "error": "create_failed",
                           "message": f"CreateAgentRuntime failed: {e}"})

    # 7. Apply resource policy
    resource_policy_set = False
    if runtime_arn:
        try:
            _apply_resource_policy(runtime_arn, role_arn)
            resource_policy_set = True
        except Exception as e:
            # Rollback
            try:
                control.delete_agent_runtime(agentRuntimeId=runtime_id)
            except Exception:
                pass
            try:
                _merge_workspace_grants(role_name, current_grants)
            except Exception:
                pass
            return json.dumps({"status": "error", "error": "resource_policy_failed",
                               "message": f"put_resource_policy failed: {e}"})

    # 8. Write DDB
    now_iso = datetime.utcnow().isoformat() + "Z"
    entry = {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
        "runtime_name": runtime_name,
        "runtime_endpoint": _runtime_endpoint(REGION, runtime_arn) if runtime_arn else "",
        "status": "CREATING",
        "image_version": version,
        "image_uri": image_uri,
        "created_at": now_iso,
        "updated_at": now_iso,
        "last_error": None,
        "created_by": caller,
        "inflight_action": "CREATING",
        "inflight_actor": caller,
        "resource_policy_set": resource_policy_set,
    }
    try:
        # Ensure map exists
        _ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcp_runtimes = if_not_exists(mcp_runtimes, :empty)",
            ExpressionAttributeValues={":empty": {}},
        )
        _ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcp_runtimes.#t = :e, mcpGrants = :g, updated_at = :now",
            ExpressionAttributeNames={"#t": target},
            ExpressionAttributeValues={":e": entry, ":g": new_grants, ":now": now_iso},
        )
    except Exception as e:
        # Runtime created but DDB failed — the reconciler will catch up.
        pass

    return json.dumps({
        "status": "CREATING",
        "runtime_name": runtime_name,
        "runtime_arn": runtime_arn,
        "message": f"Enabling '{target}'. Takes ~3–5 min. Use get_mcp_status('{target}') to poll.",
        **entry,
    }, ensure_ascii=False, default=str)


def _merge_workspace_grants(role_name: str, grants: list[str]) -> None:
    """Read registry MCP_IAM_POLICIES from S3 and rebuild WorkspaceGrants."""
    # Read registry IAM policies
    try:
        resp = _s3().get_object(Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml")
        import yaml
        data = yaml.safe_load(resp["Body"].read())
    except Exception as e:
        raise RuntimeError(f"registry load failed: {e}")

    policies_by_name: dict[str, dict] = {}
    for t in (data.get("runtime_targets") or []):
        if t.get("iam_policy"):
            policies_by_name[t["name"]] = t["iam_policy"]

    # Merge and dedup
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

    iam = _iam()
    if not statements:
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName="WorkspaceGrants")
        except iam.exceptions.NoSuchEntityException:
            pass
        return

    policy_doc = json.dumps({"Version": "2012-10-17", "Statement": statements})
    if len(policy_doc) > 9500:
        raise RuntimeError(f"WorkspaceGrants policy size {len(policy_doc)} exceeds 9500 bytes")
    iam.put_role_policy(
        RoleName=role_name, PolicyName="WorkspaceGrants", PolicyDocument=policy_doc,
    )


def _apply_resource_policy(runtime_arn: str, owner_role_arn: str) -> None:
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": owner_role_arn},
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": runtime_arn,
            },
            {
                "Effect": "Deny",
                "NotPrincipal": {"AWS": owner_role_arn},
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": runtime_arn,
            },
        ],
    }
    _control().put_resource_policy(
        resourceArn=runtime_arn, policy=json.dumps(policy),
    )

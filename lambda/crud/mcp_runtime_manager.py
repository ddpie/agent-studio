"""Per-workspace MCP runtime lifecycle (spec §4 / §6).

Pure lifecycle helpers — no HTTP concerns. Returns typed dicts; callers
(mcp_handlers) translate to HTTP responses.

Error dicts shape: {"error": "<code>", "message": "...", **extras}
Success dicts shape: {"status": "...", "runtime_arn": "...", ...}
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any

import boto3
from aws_lambda_powertools import Logger

from shared.config import REGION, ACCOUNT_ID, WORKSPACES_TABLE, S3_BUCKET
from crud.mcp_iam_registry import MCP_IAM_POLICIES, _NO_IAM_TARGETS
from crud import workspace_iam as ws_iam

try:
    from crud.generated.ceiling_actions import CEILING_HASH, actions_within_ceiling
except Exception:  # pragma: no cover — generated file may be missing in dev
    CEILING_HASH = ""
    def actions_within_ceiling(actions: list[str]) -> tuple[bool, list[str]]:
        return (True, [])  # permissive if file missing (dev only)

logger = Logger(child=True)

# ─────────────────────────────────────────────────────────────────────
# Module-level clients (lazy)
# ─────────────────────────────────────────────────────────────────────

_control = None
_iam = None
_ws_table = None
_s3 = None
_ecr = None
_quotas = None
_sqs = None

# Module-level cache for ceiling hash drift check (§9.4).
_LIVE_CEILING_HASH_CHECKED = False
_LIVE_CEILING_HASH_OK = False


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _get_iam():
    global _iam
    if _iam is None:
        _iam = boto3.client("iam", region_name=REGION)
    return _iam


def _get_ws_table():
    global _ws_table
    if _ws_table is None:
        _ws_table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _ws_table


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _get_ecr():
    global _ecr
    if _ecr is None:
        _ecr = boto3.client("ecr", region_name=REGION)
    return _ecr


def _get_quotas():
    global _quotas
    if _quotas is None:
        _quotas = boto3.client("service-quotas", region_name=REGION)
    return _quotas


def _get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs", region_name=REGION)
    return _sqs


# ─────────────────────────────────────────────────────────────────────
# Identity + naming helpers (spec D6)
# ─────────────────────────────────────────────────────────────────────

_RUNTIME_NAME_MAX = 48
_INLINE_POLICY_SOFT_LIMIT = 9500   # IAM hard limit is 10240; leave headroom
_INFLIGHT_STATUSES = {"CREATING", "UPDATING", "DELETING"}
_TERMINAL_STATUSES = {"READY", "ACTIVE", "FAILED", "DELETED"}


def compute_ws_hash(ws_id: str) -> str:
    """Deterministic 12-hex prefix for runtime + role names (spec D6)."""
    return hashlib.sha256(ws_id.encode()).hexdigest()[:12]


def build_runtime_name(ws_id: str, target: str) -> str:
    """asmcp_{ws12}_{target_norm}; target hyphens → underscores."""
    target_norm = target.replace("-", "_")
    name = f"asmcp_{compute_ws_hash(ws_id)}_{target_norm}"
    if len(name) > _RUNTIME_NAME_MAX:
        raise ValueError(
            f"Runtime name '{name}' exceeds {_RUNTIME_NAME_MAX} chars; "
            f"target '{target}' name is too long."
        )
    return name


def _build_runtime_endpoint(region: str, runtime_arn: str) -> str:
    """Canonical invocations URL per AgentCore docs."""
    encoded = urllib.parse.quote(runtime_arn, safe="")
    return (
        f"https://bedrock-agentcore.{region}.amazonaws.com/"
        f"runtimes/{encoded}/invocations?qualifier=DEFAULT"
    )


def _client_token(ws_id: str, target: str, runtime_name: str) -> str:
    """Deterministic clientToken so retries are idempotent (spec §6.2)."""
    base = f"{ws_id}:{target}:{runtime_name}"
    return hashlib.sha256(base.encode()).hexdigest()[:32]


# ─────────────────────────────────────────────────────────────────────
# Registry helpers
# ─────────────────────────────────────────────────────────────────────

_ECR_ACCOUNT = ACCOUNT_ID  # fixed for same-account ECR; cross-account is non-goal


def _ecr_image_uri(target: str, version: str | None = None) -> str:
    tag = version or "latest"
    return f"{_ECR_ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/mcp-{target}:{tag}"


def _load_registry_meta(target: str) -> dict | None:
    """Read registry YAML from S3 (synced by deploy); return target entry."""
    try:
        resp = _get_s3().get_object(
            Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml"
        )
        import yaml
        data = yaml.safe_load(resp["Body"].read())
        for t in data.get("runtime_targets", []):
            if t.get("name") == target:
                return t
        for t in data.get("remote_targets", []):
            if t.get("name") == target:
                return t
        return None
    except Exception as e:
        logger.warning("registry read failed: %s", e)
        return None


def _required_role_for_sensitivity(sensitivity: str) -> str:
    """D11: editor for low+medium; admin for high."""
    return "admin" if sensitivity == "high" else "editor"


# ─────────────────────────────────────────────────────────────────────
# DDB workspace META helpers
# ─────────────────────────────────────────────────────────────────────


def _get_ws_meta(ws_id: str) -> dict | None:
    resp = _get_ws_table().get_item(
        Key={"workspaceId": ws_id, "sk": "META"},
        ConsistentRead=True,
    )
    return resp.get("Item")


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _set_mcp_runtime_entry(
    ws_id: str, target: str, entry: dict
) -> None:
    """Upsert workspaces[ws].mcp_runtimes[target] = entry."""
    _get_ws_table().update_item(
        Key={"workspaceId": ws_id, "sk": "META"},
        UpdateExpression=(
            "SET mcp_runtimes.#t = :e, updated_at = :now"
        ),
        ExpressionAttributeNames={"#t": target},
        ExpressionAttributeValues={":e": entry, ":now": _now_iso()},
        # Ensure mcp_runtimes attribute exists (create as empty map if absent).
        ConditionExpression="attribute_exists(workspaceId)",
    )


def _init_mcp_runtimes_map(ws_id: str) -> None:
    """Ensure mcp_runtimes map attribute exists (idempotent)."""
    try:
        _get_ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcp_runtimes = if_not_exists(mcp_runtimes, :empty)",
            ExpressionAttributeValues={":empty": {}},
        )
    except Exception as e:
        logger.warning("init mcp_runtimes failed: %s", e)


def _pop_mcp_runtime_entry(ws_id: str, target: str) -> None:
    try:
        _get_ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="REMOVE mcp_runtimes.#t",
            ExpressionAttributeNames={"#t": target},
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# Control-plane wrappers (abstract the boto3 API shape)
# ─────────────────────────────────────────────────────────────────────


def _find_runtime_by_name(name: str) -> dict | None:
    """Scan list_agent_runtimes for exact name match."""
    control = _get_control()
    next_token = None
    while True:
        kw = {"nextToken": next_token} if next_token else {}
        resp = control.list_agent_runtimes(**kw)
        items = (
            resp.get("agentRuntimes")
            or resp.get("agentRuntimeSummaries")
            or resp.get("runtimes")
            or []
        )
        for rt in items:
            rt_name = rt.get("agentRuntimeName") or rt.get("name")
            if rt_name == name:
                return rt
        next_token = resp.get("nextToken")
        if not next_token:
            return None


def _get_runtime(runtime_id: str) -> dict | None:
    try:
        return _get_control().get_agent_runtime(agentRuntimeId=runtime_id)
    except Exception as e:
        logger.info("get_agent_runtime failed: %s", e)
        return None


def _runtime_arn_from_info(info: dict) -> str:
    return info.get("agentRuntimeArn", "")


# ─────────────────────────────────────────────────────────────────────
# Ceiling drift guard (spec §9.4)
# ─────────────────────────────────────────────────────────────────────


def _check_live_ceiling_hash() -> bool:
    """Compare generated CEILING_HASH against live policy Description.

    Cached module-level (Lambda container lifetime). Returns True if OK
    or check disabled (e.g. dev — generated file missing).
    """
    global _LIVE_CEILING_HASH_CHECKED, _LIVE_CEILING_HASH_OK
    if _LIVE_CEILING_HASH_CHECKED:
        return _LIVE_CEILING_HASH_OK
    _LIVE_CEILING_HASH_CHECKED = True
    if not CEILING_HASH:
        _LIVE_CEILING_HASH_OK = True  # no generated file = skip
        return True
    from shared.config import WORKSPACE_BOUNDARY_ARN
    if not WORKSPACE_BOUNDARY_ARN:
        _LIVE_CEILING_HASH_OK = True
        return True
    try:
        iam = _get_iam()
        resp = iam.get_policy(PolicyArn=WORKSPACE_BOUNDARY_ARN)
        desc = (resp.get("Policy") or {}).get("Description") or ""
        # Description format: "...ceiling_hash=<hex>..."
        m = re.search(r"ceiling_hash=([a-f0-9]+)", desc)
        live_hash = m.group(1) if m else ""
        if not live_hash:
            # No hash in description yet (first deploy); pass silently.
            logger.info("live ceiling has no hash in Description; skipping drift check")
            _LIVE_CEILING_HASH_OK = True
            return True
        if live_hash != CEILING_HASH:
            logger.critical(
                "CEILING HASH DRIFT: bundled=%s live=%s — enable endpoints disabled",
                CEILING_HASH, live_hash,
            )
            _LIVE_CEILING_HASH_OK = False
            return False
        _LIVE_CEILING_HASH_OK = True
        return True
    except Exception as e:
        logger.warning("ceiling hash check skipped: %s", e)
        _LIVE_CEILING_HASH_OK = True
        return True


# ─────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────


def enable_target(
    ws_id: str,
    target: str,
    actor: str,
    env: dict[str, str] | None = None,
) -> dict:
    """Enable an MCP target in a workspace.

    Returns dict with 'status' key. Possible statuses:
      - 'CREATING'  (202, client polls)
      - 'error'     with 'error' code + 'message'
    Error codes: boundary_gap, quota_exceeded, role_missing, image_missing,
                 policy_too_large, in_flight, unknown_target, ceiling_stale.
    """
    if not _check_live_ceiling_hash():
        return {"status": "error", "error": "ceiling_stale",
                "message": "Lambda ceiling hash drift; redeploy required."}

    # 1. Registry lookup
    meta = _load_registry_meta(target)
    if meta is None:
        return {"status": "error", "error": "unknown_target",
                "message": f"Target '{target}' not in registry."}
    if not meta.get("enabled", True):
        return {"status": "error", "error": "unknown_target",
                "message": f"Target '{target}' is disabled in registry."}
    if meta.get("vpc_required"):
        return {"status": "error", "error": "unknown_target",
                "message": f"Target '{target}' requires VPC mode (not supported)."}

    # 2. Required env check
    required_env = meta.get("requires_env") or []
    if required_env:
        return {"status": "error", "error": "env_missing",
                "message": f"Target '{target}' needs env {required_env}; "
                           f"contact platform admin. (requires_env flow deferred.)"}

    # 3. Boundary intersection check (D10)
    policy = MCP_IAM_POLICIES.get(target)
    if policy:
        required_actions = []
        for stmt in policy.get("Statement", []):
            if stmt.get("Effect") != "Allow":
                continue
            actions = stmt.get("Action", [])
            if isinstance(actions, str):
                actions = [actions]
            required_actions.extend(actions)
        ok, missing = actions_within_ceiling(required_actions)
        if not ok:
            return {"status": "error", "error": "boundary_gap",
                    "message": "Target requires actions not permitted by "
                               "workspace boundary.",
                    "missing_actions": missing}

    # 4. Workspace role must exist
    ws_meta = _get_ws_meta(ws_id)
    if ws_meta is None:
        return {"status": "error", "error": "workspace_not_found",
                "message": f"Workspace {ws_id} does not exist."}
    role_arn = ws_meta.get("roleArn")
    role_name = ws_meta.get("roleName")
    if not role_arn or not role_name:
        return {"status": "error", "error": "role_missing",
                "message": "Workspace role must be created before enabling MCPs."}

    # 5. In-flight / already-enabled check
    existing = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if existing:
        status = existing.get("status")
        if status in _INFLIGHT_STATUSES:
            return {"status": "error", "error": "in_flight",
                    "message": f"Target '{target}' is currently {status}.",
                    "inflight_action": existing.get("inflight_action") or status,
                    "inflight_actor": existing.get("inflight_actor")}
        if status == "READY" or status == "ACTIVE":
            # Already enabled — return current state, treat as idempotent.
            return {"status": status, "idempotent": True, **existing}

    # 6. Merged WorkspaceGrants size guard
    current_grants = list(ws_meta.get("mcpGrants", []) or [])
    new_grants = sorted(set(current_grants) | {target})
    merged = ws_iam._build_mcp_policy(new_grants)
    if merged is not None:
        merged_size = len(json.dumps(merged))
        if merged_size > _INLINE_POLICY_SOFT_LIMIT:
            return {"status": "error", "error": "policy_too_large",
                    "message": f"Enabling '{target}' would push WorkspaceGrants "
                               f"to {merged_size} bytes (limit {_INLINE_POLICY_SOFT_LIMIT}). "
                               f"Disable another target first."}

    # 7. ECR image existence pre-check
    version = meta.get("version") or "latest"
    try:
        _get_ecr().describe_images(
            repositoryName=f"mcp-{target}",
            imageIds=[{"imageTag": version}],
        )
    except Exception as e:
        return {"status": "error", "error": "image_missing",
                "message": f"ECR image mcp-{target}:{version} not found. "
                           f"Ask platform admin to run scripts/build-mcp.sh.",
                "detail": str(e)}

    # 8. Quota pre-check (best-effort)
    quota_ok, quota_hint = _check_quota()
    if not quota_ok:
        return {"status": "error", "error": "quota_exceeded",
                "message": f"Near AgentCore runtime quota: {quota_hint}"}

    # 9. Merge WorkspaceGrants on workspace role
    try:
        ws_iam._write_mcp_policy(role_name, new_grants)
    except Exception as e:
        logger.exception("WorkspaceGrants write failed")
        return {"status": "error", "error": "policy_write_failed",
                "message": f"IAM policy update failed: {e}"}

    # Best-effort DDB grant bookkeeping; keep parallel with Phase 1 additive model.
    try:
        _get_ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcpGrants = :g, updated_at = :now",
            ExpressionAttributeValues={":g": new_grants, ":now": _now_iso()},
        )
    except Exception:
        pass

    # 10. Create runtime
    runtime_name = build_runtime_name(ws_id, target)
    image_uri = _ecr_image_uri(target, version)
    client_tok = _client_token(ws_id, target, runtime_name)

    _init_mcp_runtimes_map(ws_id)

    now = _now_iso()
    try:
        resp = _get_control().create_agent_runtime(
            agentRuntimeName=runtime_name,
            description=f"MCP {target} for ws {ws_id[:8]}",
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
            protocolConfiguration={"serverProtocol": "MCP"},
            networkConfiguration={"networkMode": "PUBLIC"},
            roleArn=role_arn,
            clientToken=client_tok,
        )
        runtime_id = resp.get("agentRuntimeId") or resp.get("runtimeId")
        runtime_arn = resp.get("agentRuntimeArn", "")
    except _get_control().exceptions.ConflictException:
        # Same client token → already created; fetch it.
        rt = _find_runtime_by_name(runtime_name)
        if not rt:
            return {"status": "error", "error": "conflict_unresolved",
                    "message": "CreateAgentRuntime conflict but can't locate existing runtime."}
        runtime_id = rt.get("agentRuntimeId") or rt.get("runtimeId")
        info = _get_runtime(runtime_id) or {}
        runtime_arn = _runtime_arn_from_info(info)
    except Exception as e:
        logger.exception("CreateAgentRuntime failed")
        # Rollback policy merge
        try:
            ws_iam._write_mcp_policy(role_name, current_grants)
            _get_ws_table().update_item(
                Key={"workspaceId": ws_id, "sk": "META"},
                UpdateExpression="SET mcpGrants = :g",
                ExpressionAttributeValues={":g": current_grants},
            )
        except Exception:
            pass
        return {"status": "error", "error": "create_failed",
                "message": f"CreateAgentRuntime failed: {e}"}

    # Resolve ARN if not returned in create response
    if not runtime_arn and runtime_id:
        info = _get_runtime(runtime_id) or {}
        runtime_arn = _runtime_arn_from_info(info)

    # 11. Apply resource policy (spec §4.2 Layer 1)
    resource_policy_set = False
    if runtime_arn:
        try:
            _apply_resource_policy(runtime_arn, role_arn)
            resource_policy_set = True
        except Exception as e:
            logger.exception("put-resource-policy failed; attempting rollback")
            # Best-effort: delete the runtime so we don't leak an unpinned one.
            try:
                _get_control().delete_agent_runtime(agentRuntimeId=runtime_id)
            except Exception:
                pass
            # Revert grants
            try:
                ws_iam._write_mcp_policy(role_name, current_grants)
                _get_ws_table().update_item(
                    Key={"workspaceId": ws_id, "sk": "META"},
                    UpdateExpression="SET mcpGrants = :g",
                    ExpressionAttributeValues={":g": current_grants},
                )
            except Exception:
                pass
            return {"status": "error", "error": "resource_policy_failed",
                    "message": f"Failed to pin resource policy: {e}"}

    # 12. Write DDB entry
    endpoint = _build_runtime_endpoint(REGION, runtime_arn) if runtime_arn else ""
    entry = {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
        "runtime_name": runtime_name,
        "runtime_endpoint": endpoint,
        "status": "CREATING",
        "image_version": version,
        "image_uri": image_uri,
        "created_at": now,
        "updated_at": now,
        "last_error": None,
        "created_by": actor,
        "inflight_action": "CREATING",
        "inflight_actor": actor,
        "resource_policy_set": resource_policy_set,
    }
    _set_mcp_runtime_entry(ws_id, target, entry)

    return {"status": "CREATING", **entry}


def _apply_resource_policy(runtime_arn: str, owner_role_arn: str) -> None:
    """Pin invokable principals to owning workspace role (spec §4.2 Layer 1)."""
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
    control = _get_control()
    try:
        control.put_resource_policy(
            resourceArn=runtime_arn,
            policy=json.dumps(policy),
        )
    except AttributeError:
        # boto3 shape for PutResourcePolicy uses kebab-case on some versions;
        # try alternate invocation.
        control.meta.client.put_resource_policy(
            resourceArn=runtime_arn, policy=json.dumps(policy)
        )


def disable_target(ws_id: str, target: str, actor: str) -> dict:
    """Disable an MCP target. Deletes runtime + revokes policy statements."""
    ws_meta = _get_ws_meta(ws_id)
    if ws_meta is None:
        return {"status": "error", "error": "workspace_not_found",
                "message": f"Workspace {ws_id} does not exist."}
    role_name = ws_meta.get("roleName")
    mcp_runtimes = ws_meta.get("mcp_runtimes") or {}
    entry = mcp_runtimes.get(target)
    if not entry:
        return {"status": "error", "error": "not_enabled",
                "message": f"Target '{target}' is not enabled."}

    runtime_id = entry.get("runtime_id")

    # Mark DELETING in DDB (best-effort, optimistic lock absent for simplicity).
    try:
        _get_ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression=(
                "SET mcp_runtimes.#t.#s = :deleting, "
                "mcp_runtimes.#t.inflight_action = :ia, "
                "mcp_runtimes.#t.inflight_actor = :actor, "
                "mcp_runtimes.#t.updated_at = :now"
            ),
            ExpressionAttributeNames={"#t": target, "#s": "status"},
            ExpressionAttributeValues={
                ":deleting": "DELETING",
                ":ia": "DELETING",
                ":actor": actor,
                ":now": _now_iso(),
            },
        )
    except Exception:
        pass

    # Delete runtime (best-effort; reconciler will catch stragglers).
    try:
        _get_control().delete_agent_runtime(agentRuntimeId=runtime_id)
    except Exception as e:
        logger.warning("delete_agent_runtime failed for %s: %s", runtime_id, e)

    # Shrink WorkspaceGrants (if no other enabled target shares this one's actions).
    current_grants = list(ws_meta.get("mcpGrants", []) or [])
    new_grants = sorted(set(current_grants) - {target})
    if role_name:
        try:
            ws_iam._write_mcp_policy(role_name, new_grants)
            _get_ws_table().update_item(
                Key={"workspaceId": ws_id, "sk": "META"},
                UpdateExpression="SET mcpGrants = :g, updated_at = :now",
                ExpressionAttributeValues={":g": new_grants, ":now": _now_iso()},
            )
        except Exception as e:
            logger.warning("WorkspaceGrants shrink failed: %s", e)

    # Remove DDB entry
    _pop_mcp_runtime_entry(ws_id, target)

    return {"status": "DELETED"}


def upgrade_target(ws_id: str, target: str, actor: str) -> dict:
    """Update runtime to latest registry image version."""
    ws_meta = _get_ws_meta(ws_id)
    if ws_meta is None:
        return {"status": "error", "error": "workspace_not_found"}
    entry = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if not entry:
        return {"status": "error", "error": "not_enabled"}

    reg_meta = _load_registry_meta(target)
    if not reg_meta:
        return {"status": "error", "error": "unknown_target"}
    new_version = reg_meta.get("version") or "latest"

    runtime_id = entry["runtime_id"]
    image_uri = _ecr_image_uri(target, new_version)

    try:
        _get_control().update_agent_runtime(
            agentRuntimeId=runtime_id,
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
        )
    except Exception as e:
        logger.exception("update_agent_runtime failed")
        return {"status": "error", "error": "update_failed", "message": str(e)}

    # Mark UPDATING in DDB
    try:
        _get_ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression=(
                "SET mcp_runtimes.#t.#s = :u, "
                "mcp_runtimes.#t.image_version = :v, "
                "mcp_runtimes.#t.image_uri = :uri, "
                "mcp_runtimes.#t.inflight_action = :u, "
                "mcp_runtimes.#t.inflight_actor = :actor, "
                "mcp_runtimes.#t.updated_at = :now"
            ),
            ExpressionAttributeNames={"#t": target, "#s": "status"},
            ExpressionAttributeValues={
                ":u": "UPDATING",
                ":v": new_version,
                ":uri": image_uri,
                ":actor": actor,
                ":now": _now_iso(),
            },
        )
    except Exception:
        pass

    return {"status": "UPDATING", "image_version": new_version}


def get_target_status(ws_id: str, target: str) -> dict:
    """Get single-target status. Heals DDB if non-terminal."""
    ws_meta = _get_ws_meta(ws_id)
    if ws_meta is None:
        return {"status": "error", "error": "workspace_not_found"}
    entry = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if not entry:
        return {"status": "error", "error": "not_enabled"}

    status = entry.get("status")
    if status in _INFLIGHT_STATUSES:
        info = _get_runtime(entry["runtime_id"])
        if info:
            live_status = info.get("status") or status
            if live_status in ("READY", "ACTIVE"):
                _get_ws_table().update_item(
                    Key={"workspaceId": ws_id, "sk": "META"},
                    UpdateExpression=(
                        "SET mcp_runtimes.#t.#s = :r, "
                        "mcp_runtimes.#t.inflight_action = :null, "
                        "mcp_runtimes.#t.inflight_actor = :null, "
                        "mcp_runtimes.#t.updated_at = :now"
                    ),
                    ExpressionAttributeNames={"#t": target, "#s": "status"},
                    ExpressionAttributeValues={
                        ":r": "READY",
                        ":null": None,
                        ":now": _now_iso(),
                    },
                )
                entry["status"] = "READY"
                entry["inflight_action"] = None
            elif live_status in ("FAILED", "CREATE_FAILED"):
                last_err = info.get("failureReason") or info.get("statusReason") or "unknown"
                _get_ws_table().update_item(
                    Key={"workspaceId": ws_id, "sk": "META"},
                    UpdateExpression=(
                        "SET mcp_runtimes.#t.#s = :f, "
                        "mcp_runtimes.#t.last_error = :e, "
                        "mcp_runtimes.#t.inflight_action = :null, "
                        "mcp_runtimes.#t.inflight_actor = :null, "
                        "mcp_runtimes.#t.updated_at = :now"
                    ),
                    ExpressionAttributeNames={"#t": target, "#s": "status"},
                    ExpressionAttributeValues={
                        ":f": "FAILED",
                        ":e": last_err,
                        ":null": None,
                        ":now": _now_iso(),
                    },
                )
                entry["status"] = "FAILED"
                entry["last_error"] = last_err
                entry["inflight_action"] = None

    return {"status": entry.get("status", "UNKNOWN"), **entry}


def list_catalog(ws_id: str) -> dict:
    """List all registry targets + per-workspace enable status."""
    ws_meta = _get_ws_meta(ws_id) or {}
    workspace_role_exists = bool(ws_meta.get("roleArn"))
    mcp_runtimes = ws_meta.get("mcp_runtimes") or {}

    # Heal-on-read for any non-terminal entries
    for target, entry in list(mcp_runtimes.items()):
        if entry.get("status") in _INFLIGHT_STATUSES:
            get_target_status(ws_id, target)  # best-effort heal
    # Re-read after heals
    ws_meta = _get_ws_meta(ws_id) or {}
    mcp_runtimes = ws_meta.get("mcp_runtimes") or {}

    # Load registry
    try:
        resp = _get_s3().get_object(
            Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml"
        )
        import yaml
        registry = yaml.safe_load(resp["Body"].read())
    except Exception as e:
        logger.warning("registry load failed: %s", e)
        registry = {"runtime_targets": [], "remote_targets": []}

    targets = []
    for t in (registry.get("runtime_targets") or []):
        if not t.get("enabled", True):
            continue
        if t.get("vpc_required"):
            continue
        name = t["name"]
        entry = mcp_runtimes.get(name)
        targets.append({
            "name": name,
            "displayName": t.get("name"),
            "description": t.get("description", ""),
            "category": t.get("category", "general"),
            "sensitivity": t.get("sensitivity", "low"),
            "sensitiveReasons": t.get("sensitive_reasons") or [],
            "latestVersion": t.get("version") or "latest",
            "enabled": entry is not None,
            "runtime": entry,
        })

    return {
        "workspaceRoleExists": workspace_role_exists,
        "targets": targets,
    }


def get_fleet() -> dict:
    """Admin-only: aggregate workspace × target → status across all workspaces."""
    table = _get_ws_table()
    rows = []
    scan_kw: dict[str, Any] = {
        "FilterExpression": "sk = :meta",
        "ExpressionAttributeValues": {":meta": "META"},
    }
    while True:
        resp = table.scan(**scan_kw)
        for item in resp.get("Items", []):
            ws_id = item.get("workspaceId", "")
            name = item.get("name") or ws_id
            mcp_runtimes = item.get("mcp_runtimes") or {}
            for target, entry in mcp_runtimes.items():
                rows.append({
                    "workspaceId": ws_id,
                    "workspaceName": name,
                    "target": target,
                    "status": entry.get("status"),
                    "imageVersion": entry.get("image_version"),
                    "updatedAt": entry.get("updated_at"),
                    "lastError": entry.get("last_error"),
                })
        if not resp.get("LastEvaluatedKey"):
            break
        scan_kw["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return {"rows": rows}


def _check_quota() -> tuple[bool, str]:
    """Return (ok, hint). ok=False when near quota."""
    try:
        quotas = _get_quotas()
        # Runtime quota code is provider-specific; try a conservative lookup.
        # If unknown, default to OK.
        try:
            resp = quotas.get_service_quota(
                ServiceCode="bedrock-agentcore",
                QuotaCode="L-AGENT-RUNTIMES-PER-ACCOUNT",
            )
            limit = int(resp.get("Quota", {}).get("Value", 100))
        except Exception:
            return True, ""
        # Count current runtimes
        control = _get_control()
        next_token = None
        count = 0
        while True:
            kw = {"nextToken": next_token} if next_token else {}
            r = control.list_agent_runtimes(**kw)
            items = r.get("agentRuntimes") or r.get("agentRuntimeSummaries") or r.get("runtimes") or []
            count += len(items)
            next_token = r.get("nextToken")
            if not next_token:
                break
        if count >= int(limit * 0.9):
            return False, f"{count}/{limit} used"
        return True, ""
    except Exception:
        return True, ""

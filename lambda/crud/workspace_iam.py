"""Workspace IAM role management endpoints.

Handles per-workspace IAM role creation, MCP permission grants/revocations,
and permission simulation. All role mutations are scoped to the
AgentStudioWorkspaceCeiling permission boundary.
"""
import json
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import REGION, WORKSPACES_TABLE, ACCOUNT_ID, WORKSPACE_BOUNDARY_ARN
from shared.middleware import auth_check, check_platform_admin
from shared.response import success, bad_request, forbidden, internal_error, not_found
from crud.mcp_iam_registry import MCP_IAM_POLICIES, _NO_IAM_TARGETS

router = Router()
logger = Logger(child=True)

_table = None
_iam = None

# Maximum number of actions per SimulatePrincipalPolicy call.
_SIMULATE_BATCH_SIZE = 25


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _table


def _get_iam():
    global _iam
    if _iam is None:
        _iam = boto3.client("iam", region_name=REGION)
    return _iam


def _get_workspace_meta(ws_id: str) -> dict | None:
    """Fetch the workspace META item from DDB."""
    resp = _get_table().get_item(
        Key={"workspaceId": ws_id, "sk": "META"},
        ConsistentRead=True,
    )
    return resp.get("Item")


def _role_name_for_workspace(ws_id: str) -> str:
    """Deterministic IAM role name for a workspace.

    Format: AgentStudio-ws-{first 20 chars of wsId}-{region}
    """
    return f"AgentStudio-ws-{ws_id[:20]}-{REGION}"


def _ws_hash12(ws_id: str) -> str:
    """12-hex runtime-name prefix (spec D6). Duplicated from mcp_runtime_manager
    to avoid circular import during role creation."""
    import hashlib
    return hashlib.sha256(ws_id.encode()).hexdigest()[:12]


def _build_trust_policy(ws_id: str = "") -> dict:
    """Build the trust policy for a workspace IAM role.

    v4 additive (T1.10a): ArnLike includes both the legacy `runtime/*` pattern
    (for currently deployed Agents) AND the per-workspace `runtime/asmcp_{ws12}_*`
    pattern (for new MCP runtimes). Phase 7 T1.10b narrows this to asmcp_ + Agent
    runtimes only, after all Agents have been redeployed with endpoint literal.
    """
    arn_patterns = [
        f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/*",
    ]
    if ws_id:
        ws12 = _ws_hash12(ws_id)
        arn_patterns.insert(
            0,
            f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/asmcp_{ws12}_*",
        )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": ACCOUNT_ID,
                    },
                    "ArnLike": {
                        "aws:SourceArn": arn_patterns,
                    },
                },
            }
        ],
    }


def _build_default_minimal_policy(ws_id: str = "") -> dict:
    """Build the DefaultMinimal inline policy for a new workspace role.

    Replicates the baseline permissions from AgentStudioSubAgent-basic,
    excluding AWS-service read permissions (those are granted via MCP grants).
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Bedrock",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:Converse",
                    "bedrock:ConverseStream",
                ],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{ACCOUNT_ID}:inference-profile/*",
                ],
            },
            # v4 additive (T1.10a): asmcp_{ws12}_* included alongside existing
            # runtime/* for Agents that still use list_agent_runtimes. Phase 7
            # T1.10b narrows to asmcp_ + Agent runtime patterns only, after
            # Agent redeploy (spec §4.2.1/§4.2.2).
            {
                "Sid": "AgentCoreRuntime",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:GetAgentRuntime",
                    "bedrock-agentcore:ListAgentRuntimes",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/*/runtime-endpoint/*",
                ] + ([
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/asmcp_{_ws_hash12(ws_id)}_*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/asmcp_{_ws_hash12(ws_id)}_*/runtime-endpoint/*",
                ] if ws_id else []),
            },
            {
                "Sid": "AgentCoreServices",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeCodeInterpreter",
                    "bedrock-agentcore:StartCodeInterpreterSession",
                    "bedrock-agentcore:StopCodeInterpreterSession",
                    "bedrock-agentcore:InvokeBrowser",
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:StopBrowserSession",
                    "bedrock-agentcore:GetBrowserSession",
                    "bedrock-agentcore:ListBrowserSessions",
                    "bedrock-agentcore:SaveBrowserSessionProfile",
                    "bedrock-agentcore:UpdateBrowserStream",
                    "bedrock-agentcore:ConnectBrowserStream",
                    "bedrock-agentcore:ConnectBrowserAutomationStream",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:code-interpreter-custom/*",
                    f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:browser-custom/*",
                ],
            },
            {
                "Sid": "GatewayReadOnly",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:ListGateways",
                    "bedrock-agentcore:GetGateway",
                    "bedrock-agentcore:ListGatewayTargets",
                    "bedrock-agentcore:GetGatewayTarget",
                ],
                "Resource": "*",
            },
            {
                "Sid": "Observability",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/*",
            },
            {
                "Sid": "XRay",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                "Resource": "*",
            },
            {
                "Sid": "CloudWatchMetrics",
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricData"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}
                },
            },
            {
                "Sid": "ECR",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:GetAuthorizationToken",
                ],
                "Resource": "*",
            },
        ],
    }


INLINE_POLICY_SOFT_LIMIT = 9500  # IAM hard limit is 10240; leave headroom


def _dedup_statements(statements: list[dict]) -> list[dict]:
    """De-duplicate by (Effect, frozenset(Action), canonical Resource, canonical Condition).

    Spec §9.3 / feasibility F2. Saves 10–20% of merged size.
    """
    seen: set[tuple] = set()
    out = []
    for s in statements:
        actions = s.get("Action", [])
        if isinstance(actions, str):
            actions = [actions]
        resource = s.get("Resource", "*")
        if isinstance(resource, list):
            resource = tuple(sorted(resource))
        else:
            resource = (str(resource),)
        condition = json.dumps(s.get("Condition", {}), sort_keys=True)
        key = (s.get("Effect", "Allow"), frozenset(actions), resource, condition)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def _build_mcp_policy(mcp_grants: list[str]) -> dict | None:
    """Merge IAM statements for all granted MCP targets into one policy.

    Returns None if no targets require IAM permissions.
    De-duplicates overlapping statements to save bytes.
    """
    statements = []
    for target in sorted(set(mcp_grants)):
        policy = MCP_IAM_POLICIES.get(target)
        if policy and policy.get("Statement"):
            statements.extend(policy["Statement"])
    if not statements:
        return None
    statements = _dedup_statements(statements)
    return {"Version": "2012-10-17", "Statement": statements}


class PolicySizeExceeded(Exception):
    """Raised when WorkspaceGrants inline policy would exceed the soft limit."""
    def __init__(self, size: int, limit: int = INLINE_POLICY_SOFT_LIMIT):
        self.size = size
        self.limit = limit
        super().__init__(f"WorkspaceGrants policy is {size} bytes, limit {limit}")


def _write_mcp_policy(role_name: str, mcp_grants: list[str]) -> int:
    """Rebuild and write the WorkspaceGrants inline policy. Returns policy size.

    Raises PolicySizeExceeded if the merged policy exceeds the soft limit.
    """
    iam_client = _get_iam()
    merged = _build_mcp_policy(mcp_grants)
    if merged is None:
        # No IAM-requiring targets — remove stale policy if it exists.
        try:
            iam_client.delete_role_policy(
                RoleName=role_name,
                PolicyName="WorkspaceGrants",
            )
        except iam_client.exceptions.NoSuchEntityException:
            pass
        return 0

    policy_doc = json.dumps(merged)
    if len(policy_doc) > INLINE_POLICY_SOFT_LIMIT:
        raise PolicySizeExceeded(len(policy_doc))
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="WorkspaceGrants",
        PolicyDocument=policy_doc,
    )
    return len(policy_doc)


# ─── POST /api/workspaces/{wsId}/role ───
@router.post("/api/workspaces/<wsId>/role")
def create_workspace_role(wsId: str):
    """Create a per-workspace IAM role with permission boundary.

    Requires platform-admins Cognito group membership.
    """
    _, is_admin, admin_err = check_platform_admin(router.current_event)
    if admin_err:
        return admin_err
    if not is_admin:
        return forbidden()
    ws_id = wsId

    if not ACCOUNT_ID:
        return internal_error("AGENT_STUDIO_ACCOUNT_ID not configured")
    if not WORKSPACE_BOUNDARY_ARN:
        return internal_error("WORKSPACE_BOUNDARY_ARN not configured")

    table = _get_table()
    meta = _get_workspace_meta(ws_id)
    if not meta:
        return not_found()

    # Idempotent: if role already bound, return it.
    existing_role_arn = meta.get("roleArn")
    if existing_role_arn:
        return success({
            "roleArn": existing_role_arn,
            "roleName": meta.get("roleName", ""),
            "created": False,
        })

    role_name = _role_name_for_workspace(ws_id)
    iam_client = _get_iam()

    # Check if IAM role already exists (orphaned from a previous attempt).
    try:
        existing = iam_client.get_role(RoleName=role_name)
        role_arn = existing["Role"]["Arn"]
        logger.info("IAM role already exists, binding to workspace", extra={"roleName": role_name})
    except iam_client.exceptions.NoSuchEntityException:
        # Create the role.
        try:
            resp = iam_client.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(_build_trust_policy(ws_id)),
                PermissionsBoundary=WORKSPACE_BOUNDARY_ARN,
                Tags=[
                    {"Key": "agent-studio:workspace", "Value": ws_id},
                    {"Key": "ManagedBy", "Value": "agent-studio"},
                ],
            )
            role_arn = resp["Role"]["Arn"]
        except Exception as e:
            logger.exception("Failed to create IAM role", extra={"roleName": role_name})
            return internal_error(f"Failed to create IAM role: {str(e)}")

        # Attach DefaultMinimal inline policy.
        try:
            iam_client.put_role_policy(
                RoleName=role_name,
                PolicyName="DefaultMinimal",
                PolicyDocument=json.dumps(_build_default_minimal_policy(ws_id)),
            )
        except Exception as e:
            logger.exception("Failed to attach DefaultMinimal policy", extra={"roleName": role_name})
            # Best-effort cleanup: delete the role we just created.
            try:
                iam_client.delete_role(RoleName=role_name)
            except Exception:
                pass
            return internal_error(f"Failed to attach default policy: {str(e)}")

    # Store roleArn + roleName in DDB workspace META.
    now = datetime.utcnow().isoformat() + "Z"
    try:
        table.update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET roleArn = :arn, roleName = :rn, updated_at = :now",
            ExpressionAttributeValues={
                ":arn": role_arn,
                ":rn": role_name,
                ":now": now,
            },
            ConditionExpression="attribute_exists(workspaceId) AND attribute_not_exists(roleArn)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        # Another request already bound the role — fetch and return it.
        refreshed = _get_workspace_meta(ws_id)
        return success({
            "roleArn": refreshed.get("roleArn", role_arn),
            "roleName": refreshed.get("roleName", role_name),
            "created": False,
        })

    return success({"roleArn": role_arn, "roleName": role_name, "created": True}, status_code=201)


# ─── POST /api/workspaces/{wsId}/grant-mcp ───
@router.post("/api/workspaces/<wsId>/grant-mcp")
def grant_mcp(wsId: str):
    """Grant MCP target permissions to a workspace role.

    Requires platform-admins Cognito group membership.
    Body: {"targets": ["cloudwatch", "cloudtrail"]}
    """
    _, is_admin, admin_err = check_platform_admin(router.current_event)
    if admin_err:
        return admin_err
    if not is_admin:
        return forbidden()
    ws_id = wsId

    body = router.current_event.json_body or {}
    targets = body.get("targets", [])
    if not targets or not isinstance(targets, list):
        return bad_request("targets must be a non-empty list")

    # Validate target names.
    known = set(MCP_IAM_POLICIES.keys()) | _NO_IAM_TARGETS
    unknown = [t for t in targets if t not in known]
    if unknown:
        return bad_request(f"Unknown MCP targets: {', '.join(unknown)}")

    meta = _get_workspace_meta(ws_id)
    if not meta:
        return not_found()
    if not meta.get("roleArn"):
        return bad_request("Workspace has no IAM role. Create one first via POST /role")

    role_name = meta["roleName"]
    current_grants = list(meta.get("mcpGrants", []) or [])
    updated_grants = sorted(set(current_grants) | set(targets))

    # DDB optimistic-lock update.
    table = _get_table()
    try:
        table.update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcpGrants = :g, updated_at = :now",
            ExpressionAttributeValues={
                ":g": updated_grants,
                ":now": datetime.utcnow().isoformat() + "Z",
                ":old": current_grants or [],
            },
            ConditionExpression=(
                "mcpGrants = :old"
                if current_grants
                else "(attribute_not_exists(mcpGrants) OR mcpGrants = :old)"
            ),
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return bad_request("Concurrent modification detected. Please retry.")

    # Rebuild and write IAM policy.
    try:
        policy_size = _write_mcp_policy(role_name, updated_grants)
    except Exception as e:
        logger.exception("Failed to write WorkspaceGrants policy", extra={"roleName": role_name})
        # Attempt to roll back DDB (best-effort).
        try:
            table.update_item(
                Key={"workspaceId": ws_id, "sk": "META"},
                UpdateExpression="SET mcpGrants = :g",
                ExpressionAttributeValues={":g": current_grants or []},
            )
        except Exception:
            pass
        return internal_error(f"Failed to update IAM policy: {str(e)}")

    return success({"mcpGrants": updated_grants, "policySize": policy_size})


# ─── POST /api/workspaces/{wsId}/revoke-mcp ───
@router.post("/api/workspaces/<wsId>/revoke-mcp")
def revoke_mcp(wsId: str):
    """Revoke MCP target permissions from a workspace role.

    Requires platform-admins Cognito group membership.
    Body: {"targets": ["iam"]}
    """
    _, is_admin, admin_err = check_platform_admin(router.current_event)
    if admin_err:
        return admin_err
    if not is_admin:
        return forbidden()
    ws_id = wsId

    body = router.current_event.json_body or {}
    targets = body.get("targets", [])
    if not targets or not isinstance(targets, list):
        return bad_request("targets must be a non-empty list")

    meta = _get_workspace_meta(ws_id)
    if not meta:
        return not_found()
    if not meta.get("roleArn"):
        return bad_request("Workspace has no IAM role")

    role_name = meta["roleName"]
    current_grants = list(meta.get("mcpGrants", []) or [])
    updated_grants = sorted(set(current_grants) - set(targets))

    # DDB optimistic-lock update.
    table = _get_table()
    try:
        table.update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET mcpGrants = :g, updated_at = :now",
            ExpressionAttributeValues={
                ":g": updated_grants or [],
                ":now": datetime.utcnow().isoformat() + "Z",
                ":old": current_grants or [],
            },
            ConditionExpression=(
                "mcpGrants = :old"
                if current_grants
                else "(attribute_not_exists(mcpGrants) OR mcpGrants = :old)"
            ),
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return bad_request("Concurrent modification detected. Please retry.")

    # Rebuild and write IAM policy.
    try:
        policy_size = _write_mcp_policy(role_name, updated_grants)
    except Exception as e:
        logger.exception("Failed to write WorkspaceGrants policy", extra={"roleName": role_name})
        try:
            table.update_item(
                Key={"workspaceId": ws_id, "sk": "META"},
                UpdateExpression="SET mcpGrants = :g",
                ExpressionAttributeValues={":g": current_grants or []},
            )
        except Exception:
            pass
        return internal_error(f"Failed to update IAM policy: {str(e)}")

    return success({"mcpGrants": updated_grants})


# ─── GET /api/workspaces/{wsId}/permissions ───
@router.get("/api/workspaces/<wsId>/permissions")
def get_permissions(wsId: str):
    """Check workspace role permissions via SimulatePrincipalPolicy.

    Query param: actions=cloudwatch:DescribeAlarms,logs:StartQuery
    Returns per-action allowed/denied status.
    Accessible by workspace members (viewer+) OR platform admins.
    """
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        _, is_admin, _ = check_platform_admin(router.current_event)
        if not is_admin:
            return err
        ws_id = wsId

    meta = _get_workspace_meta(ws_id)
    if not meta:
        return not_found()

    role_arn = meta.get("roleArn")
    if not role_arn:
        return success({"hasRole": False})

    qp = router.current_event.query_string_parameters or {}
    actions_str = qp.get("actions", "")
    action_list = [a.strip() for a in actions_str.split(",") if a.strip()]

    # Empty actions = just probe whether the role exists
    if not action_list:
        return success({"hasRole": True, "roleArn": role_arn, "results": []})

    iam_client = _get_iam()
    results = []
    try:
        for i in range(0, len(action_list), _SIMULATE_BATCH_SIZE):
            batch = action_list[i : i + _SIMULATE_BATCH_SIZE]
            resp = iam_client.simulate_principal_policy(
                PolicySourceArn=role_arn,
                ActionNames=batch,
                ResourceArns=["*"],
            )
            for r in resp.get("EvaluationResults", []):
                results.append({
                    "action": r["EvalActionName"],
                    "allowed": r["EvalDecision"] == "allowed",
                })
    except Exception as e:
        logger.exception("SimulatePrincipalPolicy failed", extra={"roleArn": role_arn})
        return internal_error(f"Permission simulation failed: {str(e)}")

    return success({
        "hasRole": True,
        "roleArn": role_arn,
        "results": results,
    })


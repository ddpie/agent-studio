"""Helpers for wiring MCP targets into AgentCore Harness `tools` / `allowedTools`.

Zip agents resolve mcp_targets into a list of runtime endpoints that the
generated `main.py` connects to at startup. Harness is declarative — we
pass `tools=[{type: agentcore_gateway, gatewayArn}, ...]` to CreateHarness
and harness connects to them for us. This module does the mapping from
our short target-name list (e.g. ["cloudwatch","iam"]) to the harness
tool list.

Key differences from zip:
- Harness wants a distinct tool entry per *gateway*, not per target. Targets
  on the same gateway share one `agentcore_gateway` entry.
- Cross-gateway routing happens inside the gateway — we cannot point
  harness at individual targets, only at the gateway they live in.
- `allowedTools` trims the gateway's total tool surface down to the
  requested targets. Format: `"<gatewayName>/<prefix>*"` or `"*"`.
  Using `*` is cheapest but leaks other targets on the same gateway.
"""
import boto3

from config import REGION


def _get_control_client():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def resolve_mcp_targets_to_harness_tools(target_names: list) -> list:
    """Map a list of short MCP target names to harness tool config.

    Args:
        target_names: e.g. ["cloudwatch", "iam", "aws-api"]. Case-sensitive.
            Empty / None → returns [].

    Returns:
        tools: list[dict] suitable for CreateHarness(tools=...). One entry
               per distinct gateway the requested targets live on.

    Raises:
        ValueError if a target name isn't found on any gateway.

    Note:
        We intentionally do NOT set `allowedTools` — workspace MCP policy
        already gates which targets the agent can use, and harness
        `allowedTools` wildcard semantics aren't documented. If a future
        caller needs tighter scoping, add it here once AWS publishes the
        match rules.
    """
    if not target_names:
        return []

    cp = _get_control_client()

    # Build target_name -> (gatewayId, gatewayName, gatewayArn) index.
    # Paginate through all gateways + their targets in the account.
    target_index = {}  # short_name -> gateway info
    gw_resp = cp.list_gateways()
    gateways = gw_resp.get("items", gw_resp.get("gateways", []))
    while gw_resp.get("nextToken"):
        gw_resp = cp.list_gateways(nextToken=gw_resp["nextToken"])
        gateways.extend(gw_resp.get("items", gw_resp.get("gateways", [])))

    for gw in gateways:
        gw_id = gw.get("gatewayId")
        gw_name = gw.get("name", "")
        gw_arn = gw.get("gatewayArn") or (
            f"arn:aws:bedrock-agentcore:{REGION}:"
            f"{boto3.client('sts').get_caller_identity()['Account']}:gateway/{gw_id}"
            if gw_id else ""
        )
        if not gw_id or not gw_arn:
            continue

        tr = cp.list_gateway_targets(gatewayIdentifier=gw_id)
        tgt_items = tr.get("items", tr.get("targets", []))
        while tr.get("nextToken"):
            tr = cp.list_gateway_targets(gatewayIdentifier=gw_id, nextToken=tr["nextToken"])
            tgt_items.extend(tr.get("items", tr.get("targets", [])))

        for t in tgt_items:
            full_name = t.get("name", "")
            # mcp-registry target names in DDB/UI often omit the "mcp-"
            # prefix that the gateway target carries. Accept both.
            short = full_name
            if short.startswith("mcp-"):
                short = short[len("mcp-"):]
            target_index[full_name] = (gw_id, gw_name, gw_arn)
            target_index[short] = (gw_id, gw_name, gw_arn)

    # Group requested targets by gatewayArn.
    by_gateway = {}  # gwArn -> (gwName, [short_target_name])
    missing = []
    for name in target_names:
        info = target_index.get(name)
        if not info:
            missing.append(name)
            continue
        gw_id, gw_name, gw_arn = info
        by_gateway.setdefault(gw_arn, (gw_name, []))
        by_gateway[gw_arn][1].append(name)

    if missing:
        raise ValueError(
            f"MCP targets not found on any gateway: {missing}. "
            "Check workspace MCP policy + that targets are enabled."
        )

    tools = []
    for gw_arn, (gw_name, _targets) in by_gateway.items():
        tools.append({
            "type": "agentcore_gateway",
            "name": gw_name[:64],
            "config": {
                "agentCoreGateway": {  # camelCase per SDK shape
                    "gatewayArn": gw_arn,
                    # Gateway enforces IAM auth (workspace role has
                    # bedrock-agentcore:InvokeGatewayTarget via MCP policy);
                    # this tells harness to sign the outbound call.
                    "outboundAuth": {"awsIam": {}},
                },
            },
        })

    return tools

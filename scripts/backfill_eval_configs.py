#!/usr/bin/env python3
"""Backfill: create a per-agent OnlineEvaluationConfig for every agent
that doesn't already have one.

AgentCore accepts exactly 1 `serviceName` per config, so eval configs
are keyed per agent (not per workspace) — one config per sub-agent.

Usage:
    EVALUATOR_ROLE_ARN=arn:aws:iam::<account>:role/<role> \\
        python3 scripts/backfill_eval_configs.py [--region us-east-1] [--dry-run]

Reads:
    - DynamoDB table `agent-studio-agents` (all live items, filtered by status != archived)
    - bedrock-agentcore-control:ListOnlineEvaluationConfigs

Writes (unless --dry-run):
    - bedrock-agentcore-control:CreateOnlineEvaluationConfig per missing agent

Naming scheme must match lambda/crud/evaluations.py::_eval_config_name_for_agent.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import boto3
from botocore.exceptions import ClientError


AGENTS_TABLE = "agent-studio-agents"
DEFAULT_SPANS_LOG_GROUP = "aws/spans"

DEFAULT_EVALUATORS = [
    {"evaluatorId": "Builtin.Correctness"},
    {"evaluatorId": "Builtin.Helpfulness"},
    {"evaluatorId": "Builtin.GoalSuccessRate"},
]


def eval_config_name_for_agent(workspace_id: str, agent_id: str) -> str:
    """Mirror lambda/crud/evaluations.py::_eval_config_name_for_agent."""
    safe_ws = re.sub(r"[^A-Za-z0-9]", "", workspace_id)[:16]
    safe_agent = re.sub(r"[^A-Za-z0-9]", "", agent_id)[:24]
    return f"agentstudio_{safe_ws}_{safe_agent}"[:48]


def scan_agents(region: str) -> list[tuple[str, str, str]]:
    """Return [(agentId, workspace_id, name), ...] for non-archived agents."""
    ddb = boto3.client("dynamodb", region_name=region)
    paginator = ddb.get_paginator("scan")
    out: list[tuple[str, str, str]] = []
    for page in paginator.paginate(
        TableName=AGENTS_TABLE,
        ProjectionExpression="agentId, workspace_id, #n, #s",
        ExpressionAttributeNames={"#n": "name", "#s": "status"},
    ):
        for item in page.get("Items", []):
            aid = item.get("agentId", {}).get("S", "")
            ws = item.get("workspace_id", {}).get("S", "")
            name = item.get("name", {}).get("S", "") or aid
            status = item.get("status", {}).get("S", "")
            if not aid or not ws or status == "archived":
                continue
            out.append((aid, ws, name))
    return out


def list_existing_configs(region: str) -> set[str]:
    """Return every existing OnlineEvaluationConfig name."""
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    names: set[str] = set()
    next_token: str | None = None
    while True:
        kwargs: dict = {"maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = client.list_online_evaluation_configs(**kwargs)
        for item in (
            resp.get("onlineEvaluationConfigs", [])
            or resp.get("onlineEvaluationConfigSummaries", [])
            or resp.get("items", [])
            or []
        ):
            name = item.get("onlineEvaluationConfigName") or item.get("name")
            if name:
                names.add(name)
        next_token = resp.get("nextToken")
        if not next_token:
            break
    return names


def create_config(
    region: str,
    workspace_id: str,
    agent_id: str,
    role_arn: str,
    spans_log_group: str,
) -> None:
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    name = eval_config_name_for_agent(workspace_id, agent_id)
    try:
        client.create_online_evaluation_config(
            onlineEvaluationConfigName=name,
            description=f"Online evaluation for agent {agent_id} (ws {workspace_id})",
            rule={
                "samplingConfig": {"samplingPercentage": 100.0},
                "filters": [],
            },
            dataSourceConfig={
                "cloudWatchLogs": {
                    "logGroupNames": [spans_log_group],
                    "serviceNames": [agent_id],
                },
            },
            evaluators=DEFAULT_EVALUATORS,
            evaluationExecutionRoleArn=role_arn,
            enableOnCreate=True,
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ConflictException":
            return
        raise


def print_table(rows: list[tuple[str, str, str, str]]) -> None:
    headers = ("agent_id", "ws_id", "action", "error")
    widths = [
        max(len(headers[i]), max((len(r[i]) for r in rows), default=0))
        for i in range(4)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--region",
        default=os.environ.get("AWS_REGION", "us-east-1"),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    role_arn = os.environ.get("EVALUATOR_ROLE_ARN", "").strip()
    if not role_arn and not args.dry_run:
        print("ERROR: EVALUATOR_ROLE_ARN env var is required.", file=sys.stderr)
        return 1

    spans_log_group = os.environ.get("AGENT_STUDIO_SPANS_LOG_GROUP", DEFAULT_SPANS_LOG_GROUP)

    print(f"region={args.region}  spans_log_group={spans_log_group}  dry_run={args.dry_run}")

    try:
        agents = scan_agents(args.region)
    except ClientError as e:
        print(f"ERROR scanning {AGENTS_TABLE}: {e}", file=sys.stderr)
        return 1
    print(f"found {len(agents)} live agent(s) in {AGENTS_TABLE}")

    try:
        existing = list_existing_configs(args.region)
    except ClientError as e:
        print(f"ERROR listing eval configs: {e}", file=sys.stderr)
        return 1
    print(f"found {len(existing)} existing eval config(s)")

    rows: list[tuple[str, str, str, str]] = []
    created = 0
    skipped = 0
    failed = 0

    for agent_id, ws_id, _name in sorted(agents):
        expected = eval_config_name_for_agent(ws_id, agent_id)
        if expected in existing:
            rows.append((agent_id, ws_id[:20], "exists", ""))
            skipped += 1
            continue
        if args.dry_run:
            rows.append((agent_id, ws_id[:20], "would-create", ""))
            continue
        try:
            create_config(args.region, ws_id, agent_id, role_arn, spans_log_group)
            rows.append((agent_id, ws_id[:20], "created", ""))
            created += 1
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "ClientError")
            msg = e.response.get("Error", {}).get("Message", str(e))
            rows.append((agent_id, ws_id[:20], "error", f"{code}: {msg}"))
            failed += 1

    print()
    print_table(rows)
    print(f"\nsummary: created={created} skipped={skipped} failed={failed} total={len(agents)}")
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

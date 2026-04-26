#!/usr/bin/env python3
"""Audit and optionally repair MCP Runtime execution-role drift.

Context: the canonical sub-agent IAM role was renamed from
`AgentStudioSubAgentRole-{region}` to `AgentStudioSubAgent-basic-{region}`
in commit 12a9fc8 (CDK refactor). The old role was then deleted. But
deploy-mcp.sh's update branch used to pass the existing runtime's
`roleArn` through unchanged, so MCP runtimes deployed before the rename
kept pointing at the dead role forever and every invocation failed with
403 `"The execution role cannot be assumed by Bedrock AgentCore"`.

This tool runs in two modes:

- default (report):  list MCP runtimes whose roleArn != canonical.
                     Exits 1 if any drift found (useful for CI).
- --fix:             call update_agent_runtime on each drifted runtime
                     to converge them. Preserves containerUri, protocol,
                     networkMode, and all other attributes.

Canonical role: arn:aws:iam::{account}:role/AgentStudioSubAgent-basic-{region}
                (matches `execution_role` in scripts/deploy-mcp.sh)
"""
from __future__ import annotations

import argparse
import json
import sys

import boto3


def canonical_role(account_id: str, region: str) -> str:
    return f"arn:aws:iam::{account_id}:role/AgentStudioSubAgent-basic-{region}"


def list_mcp_runtimes(control):
    """Yield every MCP runtime (name starts with 'mcp_')."""
    kwargs = {}
    while True:
        resp = control.list_agent_runtimes(**kwargs)
        for rt in resp.get("agentRuntimes", []):
            if rt.get("agentRuntimeName", "").startswith("mcp_"):
                yield rt
        if not resp.get("nextToken"):
            return
        kwargs = {"nextToken": resp["nextToken"]}


def repair(control, runtime_id: str, expected_role: str) -> tuple[bool, str]:
    """Issue update_agent_runtime to reset roleArn. Returns (ok, detail)."""
    info = control.get_agent_runtime(agentRuntimeId=runtime_id)
    current_role = info.get("roleArn", "")
    if current_role == expected_role:
        return True, "already canonical"

    artifact = info.get("agentRuntimeArtifact", {})
    network = info.get("networkConfiguration", {})
    protocol = info.get("protocolConfiguration", {}) or {"serverProtocol": "MCP"}
    try:
        control.update_agent_runtime(
            agentRuntimeId=runtime_id,
            roleArn=expected_role,
            networkConfiguration={"networkMode": network.get("networkMode", "PUBLIC")},
            protocolConfiguration=protocol,
            agentRuntimeArtifact=artifact,
        )
        return True, f"role updated: {current_role.split('/')[-1]} -> {expected_role.split('/')[-1]}"
    except Exception as e:
        return False, f"update failed: {e}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default=None, help="AWS region (default: AGENT_STUDIO_REGION or boto3 default)")
    parser.add_argument("--fix", action="store_true", help="Repair drifted runtimes (default is report-only)")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary instead of human-readable output")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    region = session.region_name
    if not region:
        print("ERROR: no region configured (pass --region or set AWS_REGION)", file=sys.stderr)
        return 2
    account_id = session.client("sts").get_caller_identity()["Account"]
    expected = canonical_role(account_id, region)
    control = session.client("bedrock-agentcore-control")

    runtimes = list(list_mcp_runtimes(control))
    summary: dict = {"region": region, "expected_role": expected, "total": 0, "drifted": [], "repaired": [], "errors": []}

    for rt in runtimes:
        summary["total"] += 1
        rid = rt["agentRuntimeId"]
        name = rt["agentRuntimeName"]
        info = control.get_agent_runtime(agentRuntimeId=rid)
        current = info.get("roleArn", "")
        if current == expected:
            continue
        summary["drifted"].append({"name": name, "id": rid, "current_role": current})
        if args.fix:
            ok, detail = repair(control, rid, expected)
            (summary["repaired"] if ok else summary["errors"]).append({"name": name, "id": rid, "detail": detail})

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_human(summary, fix=args.fix)

    if summary["errors"]:
        return 2
    if summary["drifted"] and not args.fix:
        return 1
    return 0


def _print_human(summary: dict, *, fix: bool) -> None:
    print(f"MCP runtime role audit — region {summary['region']}")
    print(f"  expected role: {summary['expected_role']}")
    print(f"  total MCP runtimes: {summary['total']}")
    print(f"  drifted: {len(summary['drifted'])}")
    for d in summary["drifted"]:
        print(f"    - {d['name']}: {d['current_role'] or '(none)'}")
    if fix:
        print(f"  repaired: {len(summary['repaired'])}")
        for r in summary["repaired"]:
            print(f"    - {r['name']}: {r['detail']}")
        if summary["errors"]:
            print(f"  errors: {len(summary['errors'])}")
            for e in summary["errors"]:
                print(f"    - {e['name']}: {e['detail']}")
    elif summary["drifted"]:
        print("\nRun with --fix to converge all drifted runtimes to the canonical role.")


if __name__ == "__main__":
    sys.exit(main())

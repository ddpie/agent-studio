#!/usr/bin/env python3
"""Audit and optionally repair MCP Runtime execution-role drift.

Supports per-target roles: if a target declares an `iam_policy` in the
MCP registry, the expected role is `AgentStudioMCP-{name}-{region}`.
Otherwise the expected role is `AgentStudioSubAgent-basic-{region}`.

Modes:

- default (report):  list MCP runtimes whose roleArn != expected.
                     Exits 1 if any drift found (useful for CI).
- --fix:             call update_agent_runtime on each drifted runtime
                     to converge them. Preserves containerUri, protocol,
                     networkMode, and all other attributes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import boto3

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml is required. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)


def _find_registry_path() -> str:
    """Locate mcp-registry.yaml relative to this script."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    return os.path.join(project_root, "mcp-runtime", "mcp-registry.yaml")


def _load_expected_roles(registry_path: str, account_id: str, region: str) -> dict[str, str]:
    """Build mapping of runtime name (mcp_{target}) -> expected role ARN.

    For each enabled, non-VPC, non-deprecated runtime target:
    - If it has iam_policy with Statement: use per-target role
    - Otherwise: use shared basic role
    """
    with open(registry_path) as f:
        registry = yaml.safe_load(f)

    basic_role = f"arn:aws:iam::{account_id}:role/AgentStudioSubAgent-basic-{region}"
    mapping: dict[str, str] = {}

    for t in registry.get("runtime_targets", []):
        if not t.get("enabled") or t.get("vpc_required") or t.get("deprecated"):
            continue
        name = t["name"]
        runtime_name = f"mcp_{name}".replace("-", "_")
        ip = t.get("iam_policy")
        if ip and isinstance(ip, dict) and ip.get("Statement"):
            mapping[runtime_name] = f"arn:aws:iam::{account_id}:role/AgentStudioMCP-{name}-{region}"
        else:
            mapping[runtime_name] = basic_role

    return mapping


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
        return True, "already correct"

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
    parser.add_argument(
        "--region", default=None, help="AWS region (default: AGENT_STUDIO_REGION or boto3 default)"
    )
    parser.add_argument("--fix", action="store_true", help="Repair drifted runtimes (default is report-only)")
    parser.add_argument(
        "--json", action="store_true", help="Emit JSON summary instead of human-readable output"
    )
    parser.add_argument("--registry", default=None, help="Path to mcp-registry.yaml (default: auto-detect)")
    args = parser.parse_args()

    session = boto3.Session(region_name=args.region)
    region = session.region_name
    if not region:
        print("ERROR: no region configured (pass --region or set AWS_REGION)", file=sys.stderr)
        return 2
    account_id = session.client("sts").get_caller_identity()["Account"]
    control = session.client("bedrock-agentcore-control")

    # Load per-target expected roles from registry
    registry_path = args.registry or _find_registry_path()
    if not os.path.isfile(registry_path):
        print(f"ERROR: registry not found at {registry_path}", file=sys.stderr)
        return 2
    expected_roles = _load_expected_roles(registry_path, account_id, region)
    basic_role = f"arn:aws:iam::{account_id}:role/AgentStudioSubAgent-basic-{region}"

    runtimes = list(list_mcp_runtimes(control))
    summary: dict = {
        "region": region,
        "total": 0,
        "drifted": [],
        "repaired": [],
        "errors": [],
        "per_target_roles": len([v for v in expected_roles.values() if v != basic_role]),
    }

    for rt in runtimes:
        summary["total"] += 1
        rid = rt["agentRuntimeId"]
        name = rt["agentRuntimeName"]

        # Determine expected role for this runtime
        expected = expected_roles.get(name, basic_role)

        info = control.get_agent_runtime(agentRuntimeId=rid)
        current = info.get("roleArn", "")
        if current == expected:
            continue

        summary["drifted"].append(
            {
                "name": name,
                "id": rid,
                "current_role": current,
                "expected_role": expected,
            }
        )

        if args.fix:
            ok, detail = repair(control, rid, expected)
            (summary["repaired"] if ok else summary["errors"]).append(
                {"name": name, "id": rid, "detail": detail}
            )

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
    print(f"  total MCP runtimes: {summary['total']}")
    print(f"  per-target roles in registry: {summary['per_target_roles']}")
    print(f"  drifted: {len(summary['drifted'])}")
    for d in summary["drifted"]:
        current_short = d["current_role"].split("/")[-1] if d["current_role"] else "(none)"
        expected_short = d["expected_role"].split("/")[-1]
        print(f"    - {d['name']}: has {current_short}, expected {expected_short}")
    if fix:
        print(f"  repaired: {len(summary['repaired'])}")
        for r in summary["repaired"]:
            print(f"    - {r['name']}: {r['detail']}")
        if summary["errors"]:
            print(f"  errors: {len(summary['errors'])}")
            for e in summary["errors"]:
                print(f"    - {e['name']}: {e['detail']}")
    elif summary["drifted"]:
        print("\nRun with --fix to converge all drifted runtimes to their expected roles.")


if __name__ == "__main__":
    sys.exit(main())

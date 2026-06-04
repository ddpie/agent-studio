#!/usr/bin/env python3
"""Upfront migration: widen existing workspace IAM roles for harness runtime.

Scans the DDB workspaces table for META items that have a `roleArn`, then:
  1. Compares the current trust policy against the new target (which includes
     `harness/*` in `aws:SourceArn`).
  2. Compares the current `DefaultMinimal` inline policy against the new target
     (which adds `ecr-public:*` and `sts:GetServiceBearerToken`).
  3. For each drift: reports (and, with --apply, issues `update_assume_role_policy`
     + `put_role_policy`).

Idempotent: a second run after a successful `--apply` is a no-op.

Usage:
    python scripts/migrate-workspace-roles-harness.py              # dry-run
    python scripts/migrate-workspace-roles-harness.py --apply      # actually write
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Pre-seed env vars so `shared.config` imports cleanly when running outside
# Lambda. Real values come from AWS_REGION / AGENT_STUDIO_* / boto3 defaults.
os.environ.setdefault("AWS_REGION", os.environ.get("AGENT_STUDIO_REGION", "us-east-1"))

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lambda", "crud"))
sys.path.insert(0, os.path.join(HERE, "..", "lambda"))

# Stub out the mcp_iam_registry import in workspace_iam — it's not on our path
# here and we don't need MCP helpers to run the migration.
import types

_stub = types.ModuleType("crud.mcp_iam_registry")
_stub.MCP_IAM_POLICIES = {}
_stub._NO_IAM_TARGETS = set()
sys.modules.setdefault("crud.mcp_iam_registry", _stub)

import boto3
from botocore.exceptions import ClientError

from crud.workspace_iam import (
    _build_default_minimal_policy,
    _build_trust_policy,
)


def _preflight_check_builders() -> None:
    """Fail loud if env vars are missing — otherwise silent ARN corruption.

    shared.config reads REGION / ACCOUNT_ID from env; when either is empty
    the policy builders produce ARNs with empty segments like
    ``arn:aws:bedrock-agentcore:us-east-1::runtime/*`` which AWS silently
    writes but then rejects at AssumeRole time. We'd rather crash here.
    """
    trust = _build_trust_policy()
    arns = []
    for stmt in trust.get("Statement", []):
        cond = stmt.get("Condition", {}).get("ArnLike", {})
        v = cond.get("aws:SourceArn")
        if isinstance(v, str):
            arns.append(v)
        elif isinstance(v, list):
            arns.extend(v)
        src_acct = stmt.get("Condition", {}).get("StringEquals", {}).get("aws:SourceAccount")
        if not src_acct:
            raise SystemExit(
                "ERROR: aws:SourceAccount is empty. Set AGENT_STUDIO_ACCOUNT_ID "
                "(or ACCOUNT_ID) before running migration."
            )
    for arn in arns:
        # Valid IAM ARN segments: arn:partition:service:region:account:resource
        parts = arn.split(":", 5)
        if len(parts) < 6 or not parts[4]:
            raise SystemExit(
                f"ERROR: malformed ARN in trust policy (empty account segment): {arn!r}. "
                "Set AGENT_STUDIO_ACCOUNT_ID before running migration."
            )


def _canonicalise(obj):
    """Recursively sort dict keys and lists of strings so two semantically-equal
    policies hash to the same value."""
    if isinstance(obj, dict):
        return {k: _canonicalise(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        items = [_canonicalise(x) for x in obj]
        # Sort lists of primitives (actions, arns) so order doesn't matter.
        if items and all(isinstance(x, (str, int, float, bool)) for x in items):
            return sorted(items)
        # Sort lists of dicts by their JSON repr — good enough for statements.
        if items and all(isinstance(x, dict) for x in items):
            return sorted(items, key=lambda d: json.dumps(d, sort_keys=True))
        return items
    return obj


def policy_needs_update(current, target) -> bool:
    """Return True if `current` is missing anything from `target`.

    Conservative: returns True on any exception or non-dict current.
    """
    try:
        if not isinstance(current, dict) or not isinstance(target, dict):
            return True
        return _canonicalise(current) != _canonicalise(target)
    except Exception:
        return True


def _scan_workspaces_with_role(ddb, table_name: str):
    """Yield every META item that has a `roleArn`.

    Uses the high-level Resource.Table.scan so items come back decoded
    (no DynamoDB typed-value wrapping). The Resource API also handles
    Attr() expression building correctly — the low-level client paginator
    silently returned 0 items when passed ``{":sk": {"S": "META"}}``
    because the paginator + FilterExpression combination does not
    type-decode values consistently across boto3 versions.
    """
    from boto3.dynamodb.conditions import Attr

    table = ddb.Table(table_name)
    kwargs = {"FilterExpression": Attr("sk").eq("META") & Attr("roleArn").exists()}
    while True:
        resp = table.scan(**kwargs)
        yield from resp.get("Items", [])
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        kwargs["ExclusiveStartKey"] = last


def _fetch_current_policies(iam, role_name: str):
    """Return (trust_policy, default_minimal_policy) or (None, None) if not found."""
    try:
        role = iam.get_role(RoleName=role_name)
    except iam.exceptions.NoSuchEntityException:
        return None, None
    trust = role["Role"]["AssumeRolePolicyDocument"]
    try:
        dm = iam.get_role_policy(RoleName=role_name, PolicyName="DefaultMinimal")
        minimal = dm["PolicyDocument"]
    except iam.exceptions.NoSuchEntityException:
        minimal = None
    return trust, minimal


def _apply_trust(iam, role_name: str, target_trust: dict) -> None:
    iam.update_assume_role_policy(
        RoleName=role_name,
        PolicyDocument=json.dumps(target_trust),
    )


def _apply_minimal(iam, role_name: str, target_minimal: dict) -> None:
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="DefaultMinimal",
        PolicyDocument=json.dumps(target_minimal),
    )


def main() -> int:
    _preflight_check_builders()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write IAM changes (default: dry-run / report only)",
    )
    parser.add_argument(
        "--table",
        default=os.environ.get("WORKSPACES_TABLE", "agent-studio-workspaces"),
        help="DynamoDB workspaces table name",
    )
    parser.add_argument("--region", default=os.environ.get("AWS_REGION"))
    args = parser.parse_args()

    ddb = boto3.resource("dynamodb", region_name=args.region)
    iam = boto3.client("iam", region_name=args.region)

    target_trust = _build_trust_policy()
    target_minimal = _build_default_minimal_policy()

    checked = 0
    needs_update = 0
    updated = 0
    missing_roles = 0

    for meta in _scan_workspaces_with_role(ddb, args.table):
        checked += 1
        ws_id = meta.get("workspaceId", "?")
        role_name = meta.get("roleName") or meta.get("roleArn", "").split("/")[-1]
        if not role_name:
            print(f"[skip] {ws_id}: roleName missing, roleArn malformed")
            continue

        try:
            current_trust, current_minimal = _fetch_current_policies(iam, role_name)
        except ClientError as e:
            print(f"[error] {ws_id} ({role_name}): {e}")
            continue

        if current_trust is None:
            print(f"[stale] {ws_id}: role {role_name} does not exist in IAM")
            missing_roles += 1
            continue

        trust_diff = policy_needs_update(current_trust, target_trust)
        minimal_diff = policy_needs_update(current_minimal, target_minimal)

        if not (trust_diff or minimal_diff):
            continue

        needs_update += 1
        parts = []
        if trust_diff:
            parts.append("trust")
        if minimal_diff:
            parts.append("DefaultMinimal")
        print(f"[drift] {ws_id} ({role_name}): {', '.join(parts)}")

        if args.apply:
            try:
                if trust_diff:
                    _apply_trust(iam, role_name, target_trust)
                if minimal_diff:
                    _apply_minimal(iam, role_name, target_minimal)
                updated += 1
                print(f"[applied] {ws_id} ({role_name})")
            except ClientError as e:
                print(f"[apply-error] {ws_id} ({role_name}): {e}")

    print("─" * 60)
    print(f"checked:        {checked}")
    print(f"missing role:   {missing_roles}")
    print(f"needs update:   {needs_update}")
    if args.apply:
        print(f"updated:        {updated}")
    else:
        print("dry-run — re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

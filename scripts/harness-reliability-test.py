#!/usr/bin/env python3
"""Harness vs zip reliability comparison at the AWS-API level.

Creates N harnesses (CreateHarness) and N agent runtimes (CreateAgentRuntime)
with identical config, polls both to READY, records success/failure + time-to-ready.
Cleans up everything at the end.

AWS-API level only — does NOT exercise Meta-Agent's code-gen path (which is
where zip agents actually go wrong in production). For that, we'd need to
spin up 10 Meta-Agent invocations, which would cost real Kiro credits.

Usage:
    python3 scripts/harness-reliability-test.py --count 10

Output:
    Markdown table printed to stdout + JSON dumped to /tmp/harness-reliability-<timestamp>.json
"""
import argparse
import json
import random
import string
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import boto3

REGION = "us-east-1"
ACCOUNT_ID = "557690613480"
WORKSPACE_ID = "f4c2d2de-37aa-493a-a228-e016e840af34"  # 曹豹个人
MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
POLL_INTERVAL = 15
TIMEOUT_SECS = 600  # 10 min

cp = boto3.client("bedrock-agentcore-control", region_name=REGION)


def _workspace_role() -> str:
    ddb = boto3.resource("dynamodb", region_name=REGION).Table("agent-studio-workspaces")
    ws = ddb.get_item(Key={"workspaceId": WORKSPACE_ID, "sk": "META"})["Item"]
    return ws["roleArn"]


def _rand_suffix() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=6))


def create_harness(idx: int, role_arn: str) -> dict:
    t0 = time.monotonic()
    name = f"relitest{_rand_suffix()}{idx}"
    try:
        resp = cp.create_harness(
            harnessName=name,
            executionRoleArn=role_arn,
            model={"bedrockModelConfig": {"modelId": MODEL_ID}},
            systemPrompt=[{"text": "你是一个帮助用户写邮件的助手。"}],
        )
        hid = resp["harness"]["harnessId"]
        return {"kind": "harness", "name": name, "id": hid, "arn": resp["harness"]["arn"], "create_ms": int((time.monotonic() - t0) * 1000), "create_error": None}
    except Exception as e:
        return {"kind": "harness", "name": name, "id": None, "create_ms": int((time.monotonic() - t0) * 1000), "create_error": str(e)[:300]}


def poll_harness(hid: str, deadline: float) -> dict:
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        try:
            h = cp.get_harness(harnessId=hid)["harness"]
            status = h.get("status")
            if status in ("READY", "CREATE_FAILED", "FAILED"):
                return {"status": status, "ready_ms": int((time.monotonic() - t0) * 1000), "failure_reason": h.get("failureReason") or ""}
        except Exception as e:
            return {"status": "POLL_ERROR", "ready_ms": int((time.monotonic() - t0) * 1000), "failure_reason": str(e)[:200]}
        time.sleep(POLL_INTERVAL)
    return {"status": "TIMEOUT", "ready_ms": TIMEOUT_SECS * 1000, "failure_reason": f"exceeded {TIMEOUT_SECS}s"}


def delete_harness(hid: str) -> None:
    try:
        cp.delete_harness(harnessId=hid)
    except Exception:
        pass


# ---- zip path: we point all test runtimes at the same pre-baked
# deployment zip from an existing production agent. This gives us a
# zip that is known-valid (has main.py at root, deps inlined) so we
# isolate AWS runtime-creation reliability from zip-contents issues.
S3_BUCKET = "bedrock-agentcore-codebuild-sources-557690613480-us-east-1"
ZIP_TEMPLATE_KEY = "agents/AWSArchAdvisor/deployment.zip"


def create_zip_runtime(idx: int, role_arn: str) -> dict:
    """Create an AgentCore runtime from a pre-staged S3 zip.

    This skips the Meta-Agent code-gen step entirely — we point all 10
    runtimes at the SAME pre-built base zip (no per-agent code). So this
    measures pure AWS AgentCore runtime-creation reliability.

    It does NOT exercise the full production zip path (Meta-Agent → LLM
    codegen → zip upload → create_agent_runtime). That would need Kiro
    credits and has LLM variability; call that "pipeline reliability" and
    note it separately.
    """
    t0 = time.monotonic()
    # AgentCore requires alphanumeric-only names, max 36 chars. The random
    # suffix we generate already fits but index can push past if >= 2 digits.
    # Keep the name simple + short.
    name = f"relitest{_rand_suffix()}{idx:02d}"
    try:
        resp = cp.create_agent_runtime(
            agentRuntimeName=name,
            roleArn=role_arn,
            agentRuntimeArtifact={
                "codeConfiguration": {
                    "code": {"s3": {"bucket": S3_BUCKET, "prefix": ZIP_TEMPLATE_KEY}},
                    "runtime": "PYTHON_3_10",
                    "entryPoint": ["main.py"],
                },
            },
            networkConfiguration={"networkMode": "PUBLIC"},
            protocolConfiguration={"serverProtocol": "HTTP"},
        )
        return {"kind": "zip", "name": name, "id": resp.get("agentRuntimeId"), "arn": resp.get("agentRuntimeArn"), "create_ms": int((time.monotonic() - t0) * 1000), "create_error": None}
    except Exception as e:
        return {"kind": "zip", "name": name, "id": None, "create_ms": int((time.monotonic() - t0) * 1000), "create_error": str(e)[:300]}


def poll_zip_runtime(rid: str, deadline: float) -> dict:
    t0 = time.monotonic()
    while time.monotonic() < deadline:
        try:
            r = cp.get_agent_runtime(agentRuntimeId=rid)
            status = r.get("status")
            if status in ("READY", "CREATE_FAILED", "FAILED"):
                return {"status": status, "ready_ms": int((time.monotonic() - t0) * 1000), "failure_reason": r.get("failureReason") or ""}
        except Exception as e:
            return {"status": "POLL_ERROR", "ready_ms": int((time.monotonic() - t0) * 1000), "failure_reason": str(e)[:200]}
        time.sleep(POLL_INTERVAL)
    return {"status": "TIMEOUT", "ready_ms": TIMEOUT_SECS * 1000, "failure_reason": f"exceeded {TIMEOUT_SECS}s"}


def delete_zip_runtime(rid: str) -> None:
    try:
        cp.delete_agent_runtime(agentRuntimeId=rid)
    except Exception:
        pass


def run_batch(count: int, creator, poller, deleter, role_arn: str) -> list:
    """Create `count` resources in parallel, poll each, then delete."""
    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        created = list(pool.map(lambda i: creator(i, role_arn), range(count)))
    deadline = time.monotonic() + TIMEOUT_SECS
    # Poll in parallel too
    with ThreadPoolExecutor(max_workers=min(count, 10)) as pool:
        futures = {}
        for c in created:
            if c.get("id"):
                futures[pool.submit(poller, c["id"], deadline)] = c
            else:
                results.append({**c, "status": "CREATE_ERROR", "ready_ms": 0, "failure_reason": c["create_error"]})
        for f in as_completed(futures):
            c = futures[f]
            results.append({**c, **f.result()})
    # Cleanup
    with ThreadPoolExecutor(max_workers=5) as pool:
        for r in results:
            if r.get("id"):
                pool.submit(deleter, r["id"])
    return results


def summarize(results: list, label: str) -> dict:
    total = len(results)
    ready = [r for r in results if r["status"] == "READY"]
    errs = [r for r in results if r["status"] != "READY"]
    if ready:
        avg_ready_s = sum(r["ready_ms"] for r in ready) / len(ready) / 1000
    else:
        avg_ready_s = 0
    return {
        "label": label,
        "total": total,
        "ready": len(ready),
        "errors": len(errs),
        "success_rate_pct": round(100.0 * len(ready) / total, 1) if total else 0,
        "avg_ready_seconds": round(avg_ready_s, 1),
        "error_samples": [e.get("failure_reason") or e["status"] for e in errs[:3]],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--skip-zip", action="store_true", help="skip zip runtime test (just harness)")
    args = ap.parse_args()

    role_arn = _workspace_role()
    print(f"Using role: {role_arn}")
    print(f"Creating {args.count} harnesses + {'0' if args.skip_zip else args.count} zip runtimes...\n")

    harness_results = run_batch(args.count, create_harness, poll_harness, delete_harness, role_arn)
    zip_results = [] if args.skip_zip else run_batch(args.count, create_zip_runtime, poll_zip_runtime, delete_zip_runtime, role_arn)

    summaries = [summarize(harness_results, "harness")]
    if zip_results:
        summaries.append(summarize(zip_results, "zip"))

    print("\n## Reliability comparison\n")
    print("| kind | N | ready | errors | success% | avg ready (s) | sample errors |")
    print("|---|---|---|---|---|---|---|")
    for s in summaries:
        errs = "; ".join(s["error_samples"]) or "—"
        print(f"| {s['label']} | {s['total']} | {s['ready']} | {s['errors']} | {s['success_rate_pct']}% | {s['avg_ready_seconds']} | {errs[:80]} |")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = f"/tmp/harness-reliability-{ts}.json"
    with open(out_path, "w") as f:
        json.dump({
            "summaries": summaries,
            "harness_runs": harness_results,
            "zip_runs": zip_results,
        }, f, indent=2, default=str)
    print(f"\nRaw data: {out_path}")


if __name__ == "__main__":
    main()

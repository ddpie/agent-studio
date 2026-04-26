"""E2E: deployed Agent writes events + retrieves records via AgentCore Memory.

Gated by AGENT_STUDIO_E2E=1. Uses real AWS APIs — needs valid credentials
with bedrock-agentcore permissions.
"""
import os
import time
import uuid

import boto3
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("AGENT_STUDIO_E2E") != "1",
    reason="e2e tests require AGENT_STUDIO_E2E=1",
)

REGION = os.environ.get("AGENT_STUDIO_REGION", "us-east-1")

# Strategy config matching lambda/shared/memory_strategies.py
_STRATEGIES = [
    {"userPreferenceMemoryStrategy": {
        "name": "UserPreferences",
        "namespaceTemplates": ["/users/{actorId}/preferences/"],
    }},
]


@pytest.fixture(scope="module")
def memory_resource():
    """Create a throwaway Memory resource for the test run, tear down after."""
    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    name = f"e2e-memory-{uuid.uuid4().hex[:8]}"
    resp = ctrl.create_memory(
        name=name,
        description="E2E test — safe to delete",
        memoryStrategies=_STRATEGIES,
    )
    memory_id = resp["memory"]["id"]
    yield memory_id
    try:
        ctrl.delete_memory(memoryId=memory_id)
    except Exception:
        pass


def test_event_write_and_preference_extraction(memory_resource):
    """Write a preference-laden event, wait for extraction, verify record exists."""
    data = boto3.client("bedrock-agentcore", region_name=REGION)
    agent_id = "e2eAgent"
    caller_id = "e2eUser"
    actor_id = f"{agent_id}_{caller_id}"
    session_id = f"e2e-{uuid.uuid4().hex[:8]}"

    data.create_event(
        memoryId=memory_resource,
        actorId=actor_id,
        sessionId=session_id,
        eventTimestamp=int(time.time()),
        payload=[{
            "conversational": {
                "role": "USER",
                "content": {"text": "Please always respond in concise Chinese. I prefer that style."},
            },
        }],
    )

    # Extraction is async — poll up to 3 minutes.
    ns = f"/users/{actor_id}/preferences/"
    deadline = time.time() + 180
    records = []
    while time.time() < deadline:
        resp = data.list_memory_records(
            memoryId=memory_resource, namespace=ns, maxResults=10,
        )
        records = resp.get("memoryRecordSummaries", [])
        if records:
            break
        time.sleep(10)

    assert records, f"no records extracted after 180s in namespace {ns}"
    all_text = " ".join(
        (r.get("content") or {}).get("text", "") for r in records
    ).lower()
    assert "chinese" in all_text or "concise" in all_text, (
        f"extracted records don't mention expected preference: {all_text[:200]}"
    )


def test_delete_record_removes_it(memory_resource):
    """After extraction, delete a record and verify it's gone."""
    data = boto3.client("bedrock-agentcore", region_name=REGION)
    actor_id = "e2eAgent_e2eUser"
    ns = f"/users/{actor_id}/preferences/"

    resp = data.list_memory_records(
        memoryId=memory_resource, namespace=ns, maxResults=10,
    )
    records = resp.get("memoryRecordSummaries", [])
    if not records:
        pytest.skip("no records to delete — test_event_write may not have run first")

    record_id = records[0]["memoryRecordId"]
    data.delete_memory_record(memoryId=memory_resource, memoryRecordId=record_id)

    # Verify deletion
    resp2 = data.list_memory_records(
        memoryId=memory_resource, namespace=ns, maxResults=10,
    )
    remaining_ids = [r["memoryRecordId"] for r in resp2.get("memoryRecordSummaries", [])]
    assert record_id not in remaining_ids, f"record {record_id} still present after delete"

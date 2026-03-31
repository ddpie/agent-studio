"""Deployment utilities for packaging and deploying agents to AgentCore Runtime."""

import boto3
import io
import json
import time
import zipfile

from config import REGION, ACCOUNT_ID, S3_BUCKET, AGENT_ROLE_ARN, BASE_DEPLOYMENT_KEY


def build_deployment_package(agent_code: str) -> bytes:
    """Clone base deployment zip and replace main.py with new agent code.

    The base zip contains all pre-built Python dependencies (~25MB).
    We only swap out main.py to create a new agent.
    """
    s3 = boto3.client("s3", region_name=REGION)
    base_resp = s3.get_object(Bucket=S3_BUCKET, Key=BASE_DEPLOYMENT_KEY)
    base_data = base_resp["Body"].read()

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base_data), "r") as base_zip:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zip:
            for item in base_zip.namelist():
                if item == "main.py":
                    new_zip.writestr("main.py", agent_code)
                elif item.startswith(("mcp_client/", "model/")):
                    continue  # Skip template-specific modules
                else:
                    new_zip.writestr(item, base_zip.read(item))

    return buf.getvalue()


def upload_deployment(agent_id: str, package: bytes) -> str:
    """Upload deployment package to S3. Returns S3 key."""
    s3 = boto3.client("s3", region_name=REGION)
    s3_key = f"agents/{agent_id}/deployment.zip"
    s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=package)
    return s3_key


def create_runtime(agent_name: str, description: str, s3_key: str, role_arn: str = AGENT_ROLE_ARN) -> dict:
    """Create an AgentCore Runtime. Returns {agent_id, agent_arn}."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    resp = control.create_agent_runtime(
        agentRuntimeName=agent_name,
        description=description,
        roleArn=role_arn,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": S3_BUCKET, "prefix": s3_key}},
                "runtime": "PYTHON_3_10",
                "entryPoint": ["main.py"],
            }
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
    )

    return {
        "agent_id": resp["agentRuntimeId"],
        "agent_arn": resp["agentRuntimeArn"],
    }


def wait_for_ready(agent_id: str, timeout: int = 300) -> str:
    """Wait for agent to become READY. Returns final status."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    start = time.time()

    while time.time() - start < timeout:
        resp = control.get_agent_runtime(agentRuntimeId=agent_id)
        status = resp["status"]
        if status == "READY":
            return status
        if status in ("FAILED", "DELETING"):
            return status
        time.sleep(10)

    return "TIMEOUT"


def invoke_runtime(agent_id: str, prompt: str) -> str:
    """Invoke a deployed agent. Returns the streamed text response."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    resp = client.invoke_agent_runtime(
        agentRuntimeArn=f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{agent_id}",
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}).encode(),
    )

    parts = []
    for event in resp["response"]:
        if isinstance(event, bytes):
            text = event.decode("utf-8")
            for line in text.strip().split("\n"):
                if line.startswith("data: "):
                    content = line[6:].strip().strip('"')
                    parts.append(content)
        elif isinstance(event, dict):
            for v in event.values():
                if isinstance(v, bytes):
                    parts.append(v.decode("utf-8"))

    return "".join(parts)


def delete_runtime(agent_id: str):
    """Delete an agent runtime."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    control.delete_agent_runtime(agentRuntimeId=agent_id)

"""manage_secrets — Store and retrieve agent secrets via AWS Secrets Manager."""

import json

import boto3
from strands import tool

from config import REGION
from tools._scope import ensure_agent_in_workspace, ROLE_EDITOR, ROLE_ADMIN


@tool
def set_agent_secrets(agent_id: str, secrets: str) -> str:
    """Store secrets for an agent in AWS Secrets Manager.

    Secrets are stored as a JSON object under the name agent-studio/{agentId}.
    Existing secrets are merged (new keys added, existing keys updated).

    Args:
        agent_id: The agent runtime ID.
        secrets: JSON string of key-value pairs (e.g., '{"API_KEY": "xxx", "APP_SECRET": "yyy"}').

    Returns:
        JSON with status.
    """
    _record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_ADMIN)
    if err:
        return json.dumps(err)

    try:
        new_secrets = json.loads(secrets)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON for secrets"})

    sm = boto3.client("secretsmanager", region_name=REGION)
    secret_name = f"agent-studio/{agent_id}"

    # Try to update existing, or create new
    try:
        existing = sm.get_secret_value(SecretId=secret_name)
        current = json.loads(existing["SecretString"])
        current.update(new_secrets)
        sm.update_secret(SecretId=secret_name, SecretString=json.dumps(current))
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(Name=secret_name, SecretString=json.dumps(new_secrets))

    # Mask values in response
    masked = {k: "***" for k in new_secrets}
    return json.dumps({"status": "saved", "secret_name": secret_name, "keys": masked})


@tool
def list_agent_secrets(agent_id: str) -> str:
    """List secret key names (not values) for an agent.

    Args:
        agent_id: The agent runtime ID.

    Returns:
        JSON with secret key names.
    """
    _record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    sm = boto3.client("secretsmanager", region_name=REGION)
    secret_name = f"agent-studio/{agent_id}"

    try:
        existing = sm.get_secret_value(SecretId=secret_name)
        keys = list(json.loads(existing["SecretString"]).keys())
        return json.dumps({"agent_id": agent_id, "keys": keys})
    except sm.exceptions.ResourceNotFoundException:
        return json.dumps({"agent_id": agent_id, "keys": []})


@tool
def delete_agent_secret(agent_id: str, key: str) -> str:
    """Delete a specific secret key for an agent.

    Args:
        agent_id: The agent runtime ID.
        key: The secret key to delete.

    Returns:
        JSON with status.
    """
    _record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_ADMIN)
    if err:
        return json.dumps(err)

    sm = boto3.client("secretsmanager", region_name=REGION)
    secret_name = f"agent-studio/{agent_id}"

    try:
        existing = sm.get_secret_value(SecretId=secret_name)
        current = json.loads(existing["SecretString"])
        if key in current:
            del current[key]
            sm.update_secret(SecretId=secret_name, SecretString=json.dumps(current))
            return json.dumps({"status": "deleted", "key": key})
        return json.dumps({"error": f"Key '{key}' not found"})
    except sm.exceptions.ResourceNotFoundException:
        return json.dumps({"error": "No secrets found for this agent"})

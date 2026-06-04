"""manage_secrets — Store and retrieve agent secrets via AWS Secrets Manager.

Path layout (one Secret per key, matches lambda/crud/secrets.py exactly):

    agent-studio/{workspace_id}/{agent_id}/{KEY_NAME}

This is the same layout the UI writes through the CRUD Lambda, so a secret
saved via the Secrets tab in the edit page and a secret saved by
Meta-Agent land in the same bucket. Previously this module wrote a single
JSON blob at ``agent-studio/{agent_id}`` — cross-surface inconsistency
that made list_agent_secrets return empty after the UI had written keys
and vice versa, plus broke the IAM resource scoping which uses the
per-key ARN pattern.

Tool responses never echo secret values — only key names + status. A
returned value would end up in the Meta-Agent's conversation context and
leak into transcripts / telemetry / model logs. Use the UI or the CRUD
API if a human needs to read back a plaintext secret (and they usually
shouldn't).
"""

import json

import boto3
from config import REGION
from strands import tool

from tools._scope import ROLE_ADMIN, ROLE_EDITOR, ensure_agent_in_workspace

_SECRET_KEY_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _secret_path(workspace_id: str, agent_id: str, key: str = "") -> str:
    base = f"agent-studio/{workspace_id}/{agent_id}"
    return f"{base}/{key}" if key else base


def _validate_key(key: str) -> str:
    """Return an error string if ``key`` is invalid, else empty string.

    Mirrors lambda/shared/validators.py::validate_secret_key so the two
    write surfaces accept exactly the same set of keys — otherwise a key
    the UI rejects could slip in via Meta-Agent and later fail when the
    UI tries to list or overwrite it.
    """
    if not key:
        return "key is required"
    if len(key) > 64:
        return "key must be 64 characters or less"
    if any(c not in _SECRET_KEY_CHARS for c in key):
        return "key must match [A-Z0-9_]"
    return ""


def _put_one(sm, secret_name: str, value: str) -> None:
    """Put-or-create a single-key secret."""
    try:
        sm.put_secret_value(SecretId=secret_name, SecretString=value)
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(Name=secret_name, SecretString=value)


@tool
def set_agent_secrets(agent_id: str, secrets: str) -> str:
    """Store one or more secret key/value pairs for an agent.

    Each key becomes its own Secrets Manager entry under
    ``agent-studio/{workspace_id}/{agent_id}/{KEY}``. Existing keys are
    overwritten, other keys under the agent's prefix are left alone —
    this matches the per-key UI behavior where one save doesn't wipe
    siblings.

    Args:
        agent_id: The agent runtime ID. The agent must belong to the
            caller's workspace; cross-workspace writes are refused.
        secrets: JSON string of key-value pairs, e.g.
            ``'{"FEISHU_USER_ACCESS_TOKEN": "u-...", "API_KEY": "..."}'``.
            Keys must match ``[A-Z0-9_]`` and be 1–64 chars.

    Returns:
        JSON with ``status`` and list of ``saved`` key names. Never
        includes values.
    """
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_ADMIN)
    if err:
        return json.dumps(err)

    try:
        payload = json.loads(secrets)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON for secrets"})
    if not isinstance(payload, dict) or not payload:
        return json.dumps({"error": "secrets must be a non-empty JSON object"})

    workspace_id = record.get("workspace_id", "")
    sm = boto3.client("secretsmanager", region_name=REGION)

    saved: list[str] = []
    errors: list[dict] = []
    for key, value in payload.items():
        key_err = _validate_key(key)
        if key_err:
            errors.append({"key": key, "error": key_err})
            continue
        if not isinstance(value, str) or not value:
            errors.append({"key": key, "error": "value must be a non-empty string"})
            continue
        if len(value) > 4096:
            errors.append({"key": key, "error": "value exceeds 4096 chars"})
            continue
        try:
            _put_one(sm, _secret_path(workspace_id, agent_id, key), value)
            saved.append(key)
        except Exception as e:
            errors.append({"key": key, "error": str(e)})

    return json.dumps(
        {
            "status": "saved" if saved and not errors else ("partial" if saved else "failed"),
            "agent_id": agent_id,
            "workspace_id": workspace_id,
            "saved": saved,
            "errors": errors,
        }
    )


@tool
def list_agent_secrets(agent_id: str) -> str:
    """List secret key names for an agent. Values are never returned.

    Args:
        agent_id: The agent runtime ID.

    Returns:
        JSON with ``agent_id`` and ``keys`` (list of key name strings).
    """
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    workspace_id = record.get("workspace_id", "")
    prefix = _secret_path(workspace_id, agent_id) + "/"
    sm = boto3.client("secretsmanager", region_name=REGION)

    keys: list[str] = []
    try:
        paginator = sm.get_paginator("list_secrets")
        for page in paginator.paginate(Filters=[{"Key": "name", "Values": [prefix]}]):
            for secret in page.get("SecretList", []):
                name = secret.get("Name", "")
                if name.startswith(prefix):
                    k = name[len(prefix) :]
                    if k and "/" not in k:
                        keys.append(k)
    except Exception as e:
        return json.dumps({"error": f"list_secrets failed: {e}"})

    keys.sort()
    return json.dumps({"agent_id": agent_id, "keys": keys})


@tool
def delete_agent_secret(agent_id: str, key: str) -> str:
    """Delete a single secret key for an agent.

    Args:
        agent_id: The agent runtime ID.
        key: The secret key to delete (must match [A-Z0-9_]).

    Returns:
        JSON with status.
    """
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_ADMIN)
    if err:
        return json.dumps(err)

    key_err = _validate_key(key)
    if key_err:
        return json.dumps({"error": key_err})

    workspace_id = record.get("workspace_id", "")
    sm = boto3.client("secretsmanager", region_name=REGION)
    secret_name = _secret_path(workspace_id, agent_id, key)

    try:
        sm.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
    except sm.exceptions.ResourceNotFoundException:
        return json.dumps({"error": f"Secret '{key}' not found for this agent"})
    except Exception as e:
        return json.dumps({"error": f"delete failed: {e}"})

    return json.dumps({"status": "deleted", "agent_id": agent_id, "key": key})

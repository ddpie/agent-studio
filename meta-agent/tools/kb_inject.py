"""kb_inject — Deploy-time injection of KB retrieve tool into agent zip."""

import json

import boto3
from config import KB_TABLE, REGION

from tools_library.kb_retrieve import TOOL_CODE as KB_RETRIEVE_TOOL_CODE


def build_kb_injection(kb_records: list[dict]) -> str:
    """Build Python code to prepend to tools.py for KB retrieval.

    Args:
        kb_records: List of dicts with kb_id, bedrock_kb_id, name.

    Returns:
        Python code string with BOUND_KBS constant + kb_retrieve function, or "" if empty.
    """
    if not kb_records:
        return ""

    bound_kbs_json = json.dumps(kb_records, ensure_ascii=False)
    region_line = 'import os as _os_kb; REGION = _os_kb.getenv("AWS_REGION", "us-east-1")'
    return f"{region_line}\nBOUND_KBS = {bound_kbs_json}\n\n{KB_RETRIEVE_TOOL_CODE.strip()}\n"


def resolve_kb_bindings(ws_id: str, kb_ids: list[str]) -> list[dict]:
    """Fetch KB metadata from DDB for the given kb_ids.

    Returns list of {kb_id, bedrock_kb_id, name} dicts.
    """
    if not kb_ids:
        return []

    ddb = boto3.client("dynamodb", region_name=REGION)
    results = []
    for kb_id in kb_ids:
        try:
            resp = ddb.get_item(
                TableName=KB_TABLE,
                Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}},
                ProjectionExpression="kb_id, bedrock_kb_id, #n",
                ExpressionAttributeNames={"#n": "name"},
            )
            item = resp.get("Item")
            if item and item.get("bedrock_kb_id"):
                results.append(
                    {
                        "kb_id": item["kb_id"]["S"],
                        "bedrock_kb_id": item["bedrock_kb_id"]["S"],
                        "name": item.get("name", {}).get("S", kb_id),
                    }
                )
        except Exception:
            pass
    return results

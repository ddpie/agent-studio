"""kb_list — List all Knowledge Bases in the current workspace."""

import json

import boto3
from config import KB_TABLE, REGION, S3_BUCKET
from strands import tool

from tools._scope import current_workspace


@tool
def kb_list() -> str:
    """List all Knowledge Bases in the current workspace.

    Returns:
        JSON array of KB summaries with kb_id, name, status, doc_count, updated_at.
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "no_workspace"})

    ddb = boto3.client("dynamodb", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    try:
        resp = ddb.query(
            TableName=KB_TABLE,
            KeyConditionExpression="ws_id = :ws",
            ExpressionAttributeValues={":ws": {"S": ws_id}},
        )
    except Exception as e:
        return json.dumps({"error": "query_failed", "message": str(e)})

    results = []
    for item in resp.get("Items", []):
        kb_id = item["kb_id"]["S"]
        s3_prefix = item.get("s3_prefix", {}).get("S", f"kb/{ws_id}/{kb_id}/documents/")
        doc_count = 0
        try:
            list_resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=s3_prefix, MaxKeys=1000)
            doc_count = list_resp.get("KeyCount", 0)
        except Exception:
            pass

        results.append(
            {
                "kb_id": kb_id,
                "name": item.get("name", {}).get("S", ""),
                "description": item.get("description", {}).get("S", ""),
                "status": item.get("status", {}).get("S", "UNKNOWN"),
                "doc_count": doc_count,
                "updated_at": item.get("updated_at", {}).get("S", ""),
            }
        )

    return json.dumps(results, indent=2, ensure_ascii=False)

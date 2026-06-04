"""kb_delete — Delete a Knowledge Base (with confirmation gate)."""

import json
from datetime import datetime, timezone

import boto3
from config import AGENTS_TABLE, KB_TABLE, REGION, S3_BUCKET, VECTORS_BUCKET
from strands import tool

from tools._scope import current_workspace


@tool
def kb_delete(kb_id: str, confirm: bool = False) -> str:
    """Delete a Knowledge Base and all its data.

    First call without confirm=True returns impact summary and requires confirmation.
    Call with confirm=True to actually delete.

    Args:
        kb_id: The Knowledge Base ID to delete.
        confirm: Must be True to actually execute deletion.

    Returns:
        JSON with requires_confirmation (if not confirmed) or deletion result.
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "no_workspace"})

    ddb = boto3.client("dynamodb", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    bedrock = boto3.client("bedrock-agent", region_name=REGION)
    s3v = boto3.client("s3vectors", region_name=REGION)

    try:
        resp = ddb.get_item(TableName=KB_TABLE, Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}})
        item = resp.get("Item")
        if not item:
            return json.dumps({"error": "kb_not_found"})
    except Exception as e:
        return json.dumps({"error": "ddb_read_failed", "message": str(e)})

    bedrock_kb_id = item["bedrock_kb_id"]["S"]
    data_source_id = item.get("data_source_id", {}).get("S", "")
    vector_index_name = item.get("vector_index_name", {}).get("S", "")
    s3_prefix = item.get("s3_prefix", {}).get("S", "")
    attached = item.get("attached_agent_ids", {}).get("SS", [])

    doc_count = 0
    try:
        list_resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=s3_prefix, MaxKeys=1000)
        doc_count = list_resp.get("KeyCount", 0)
    except Exception:
        pass

    if not confirm:
        return json.dumps(
            {
                "requires_confirmation": True,
                "kb_id": kb_id,
                "name": item.get("name", {}).get("S", ""),
                "impact": {
                    "documents": doc_count,
                    "attached_agents": len(attached),
                    "agent_ids": list(attached),
                },
                "message": "Call kb_delete again with confirm=True to proceed.",
            },
            ensure_ascii=False,
        )

    last_job = item.get("last_ingestion_job_id", {}).get("S")
    if last_job and data_source_id:
        try:
            job_resp = bedrock.get_ingestion_job(
                knowledgeBaseId=bedrock_kb_id, dataSourceId=data_source_id, ingestionJobId=last_job
            )
            if job_resp["ingestionJob"]["status"] == "IN_PROGRESS":
                return json.dumps(
                    {
                        "error": "ingestion_in_progress",
                        "message": "Wait for ingestion to complete before deleting.",
                    }
                )
        except Exception:
            pass

    try:
        ddb.update_item(
            TableName=KB_TABLE,
            Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}},
            UpdateExpression="SET #s = :s, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": {"S": "DELETING"},
                ":now": {"S": datetime.now(timezone.utc).isoformat()},
            },
        )
    except Exception:
        pass

    errors = []

    if data_source_id:
        try:
            bedrock.delete_data_source(knowledgeBaseId=bedrock_kb_id, dataSourceId=data_source_id)
        except Exception as e:
            errors.append(f"delete_data_source: {e}")

    try:
        bedrock.delete_knowledge_base(knowledgeBaseId=bedrock_kb_id)
    except Exception as e:
        errors.append(f"delete_kb: {e}")

    if s3_prefix:
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=s3_prefix):
                objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
                if objects:
                    s3.delete_objects(Bucket=S3_BUCKET, Delete={"Objects": objects})
        except Exception as e:
            errors.append(f"delete_s3: {e}")

    if vector_index_name:
        try:
            s3v.delete_index(vectorBucketName=VECTORS_BUCKET, indexName=vector_index_name)
        except Exception as e:
            errors.append(f"delete_index: {e}")

    if attached:
        for agent_id in attached:
            try:
                ddb.update_item(
                    TableName=AGENTS_TABLE,
                    Key={"agentId": {"S": agent_id}},
                    UpdateExpression="DELETE knowledge_bases :kb_set",
                    ExpressionAttributeValues={":kb_set": {"SS": [kb_id]}},
                )
            except Exception:
                pass

    try:
        ddb.delete_item(TableName=KB_TABLE, Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}})
    except Exception as e:
        errors.append(f"delete_ddb: {e}")

    if errors:
        return json.dumps(
            {"deleted": True, "warnings": errors, "message": "KB deleted with some cleanup warnings."}
        )
    return json.dumps({"deleted": True, "kb_id": kb_id, "message": "Knowledge Base fully deleted."})

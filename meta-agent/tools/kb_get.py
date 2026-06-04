"""kb_get — Get detailed information about a Knowledge Base."""

import json

import boto3
from config import KB_TABLE, REGION, S3_BUCKET
from strands import tool

from tools._scope import current_workspace


@tool
def kb_get(kb_id: str) -> str:
    """Get detailed information about a Knowledge Base including documents and ingestion status.

    Args:
        kb_id: The Knowledge Base ID.

    Returns:
        JSON with full KB details, recent documents, and ingestion status.
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "no_workspace"})

    ddb = boto3.client("dynamodb", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    bedrock = boto3.client("bedrock-agent", region_name=REGION)

    try:
        resp = ddb.get_item(TableName=KB_TABLE, Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}})
        item = resp.get("Item")
        if not item:
            return json.dumps({"error": "kb_not_found"})
    except Exception as e:
        return json.dumps({"error": "ddb_read_failed", "message": str(e)})

    bedrock_kb_id = item["bedrock_kb_id"]["S"]
    s3_prefix = item.get("s3_prefix", {}).get("S", "")

    documents = []
    try:
        list_resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=s3_prefix, MaxKeys=20)
        for obj in list_resp.get("Contents", []):
            documents.append({
                "key": obj["Key"],
                "filename": obj["Key"].split("/")[-1],
                "size_bytes": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })
    except Exception:
        pass

    bedrock_status = None
    try:
        kb_resp = bedrock.get_knowledge_base(knowledgeBaseId=bedrock_kb_id)
        bedrock_status = kb_resp["knowledgeBase"]["status"]
    except Exception:
        pass

    ingestion = None
    last_job_id = item.get("last_ingestion_job_id", {}).get("S")
    if last_job_id:
        try:
            data_source_id = item["data_source_id"]["S"]
            job_resp = bedrock.get_ingestion_job(
                knowledgeBaseId=bedrock_kb_id, dataSourceId=data_source_id, ingestionJobId=last_job_id
            )
            job = job_resp["ingestionJob"]
            ingestion = {"job_id": last_job_id, "status": job["status"], "statistics": job.get("statistics", {})}
        except Exception:
            pass

    result = {
        "kb_id": kb_id,
        "name": item.get("name", {}).get("S", ""),
        "description": item.get("description", {}).get("S", ""),
        "status": item.get("status", {}).get("S", ""),
        "bedrock_status": bedrock_status,
        "bedrock_kb_id": bedrock_kb_id,
        "embedding_model": item.get("embedding_model", {}).get("S", ""),
        "documents": documents,
        "doc_count": len(documents),
        "ingestion": ingestion,
        "attached_agent_ids": item.get("attached_agent_ids", {}).get("SS", []),
        "created_at": item.get("created_at", {}).get("S", ""),
        "updated_at": item.get("updated_at", {}).get("S", ""),
    }

    return json.dumps(result, indent=2, ensure_ascii=False, default=str)

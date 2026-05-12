"""kb_delete_document — Delete a single document from a Knowledge Base."""

import json
import boto3
from strands import tool
from datetime import datetime, timezone

from config import REGION, S3_BUCKET, KB_TABLE
from tools._scope import current_workspace


@tool
def kb_delete_document(kb_id: str, document_key: str) -> str:
    """Delete a single document from a Knowledge Base and re-ingest to remove its vectors.

    Args:
        kb_id: The Knowledge Base ID.
        document_key: Full S3 key of the document (from kb_get results).

    Returns:
        JSON with deleted status and ingestion_job_id.
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

    s3_prefix = item["s3_prefix"]["S"]
    if not document_key.startswith(s3_prefix):
        return json.dumps({"error": "invalid_document_key", "message": "Document key does not belong to this KB."})

    bedrock_kb_id = item["bedrock_kb_id"]["S"]
    data_source_id = item["data_source_id"]["S"]

    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=document_key)
    except Exception as e:
        return json.dumps({"error": "delete_failed", "message": str(e)})

    ingestion_job_id = None
    try:
        ingest_resp = bedrock.start_ingestion_job(knowledgeBaseId=bedrock_kb_id, dataSourceId=data_source_id)
        ingestion_job_id = ingest_resp["ingestionJob"]["ingestionJobId"]
        ddb.update_item(
            TableName=KB_TABLE,
            Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}},
            UpdateExpression="SET last_ingestion_job_id = :job, updated_at = :now",
            ExpressionAttributeValues={":job": {"S": ingestion_job_id}, ":now": {"S": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as e:
        return json.dumps({"deleted": True, "ingestion_job_id": None, "warning": f"Document deleted but re-ingestion failed: {e}"})

    return json.dumps({
        "deleted": True,
        "document_key": document_key,
        "ingestion_job_id": ingestion_job_id,
        "message": "Document deleted. Re-ingestion started to remove vectors.",
    })

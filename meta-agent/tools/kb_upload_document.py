"""kb_upload_document — Upload a document to a Knowledge Base and start ingestion."""

import json
import re
import uuid
from datetime import datetime, timezone

import boto3
from config import KB_TABLE, REGION, S3_BUCKET
from strands import tool

from tools._scope import current_workspace

ALLOWED_EXTENSIONS = {"pdf", "md", "txt", "html", "csv", "docx", "xlsx", "pptx"}
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024


def _safe_filename(filename: str) -> str:
    name = re.sub(r'[^\w\-.]', '_', filename)
    return name[:100]


@tool
def kb_upload_document(kb_id: str, staging_key: str, filename: str) -> str:
    """Upload a document to a Knowledge Base and start ingestion.

    The file must already be uploaded to S3 staging (via the frontend upload flow).
    Supported formats: pdf, md, txt, html, csv, docx, xlsx, pptx. Max 50MB.

    Args:
        kb_id: The Knowledge Base ID to upload to.
        staging_key: S3 key where the file was staged.
        filename: Original filename (used for extension validation and naming).

    Returns:
        JSON with document_key, ingestion_job_id, status, or error.
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "no_workspace"})

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return json.dumps({"error": "unsupported_format", "allowed": sorted(ALLOWED_EXTENSIONS), "got": ext})

    s3 = boto3.client("s3", region_name=REGION)
    ddb = boto3.client("dynamodb", region_name=REGION)
    bedrock = boto3.client("bedrock-agent", region_name=REGION)

    try:
        item_resp = ddb.get_item(TableName=KB_TABLE, Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}})
        item = item_resp.get("Item")
        if not item:
            return json.dumps({"error": "kb_not_found"})
    except Exception as e:
        return json.dumps({"error": "ddb_read_failed", "message": str(e)})

    bedrock_kb_id = item["bedrock_kb_id"]["S"]
    data_source_id = item["data_source_id"]["S"]
    s3_prefix = item["s3_prefix"]["S"]

    try:
        head = s3.head_object(Bucket=S3_BUCKET, Key=staging_key)
        if head["ContentLength"] > MAX_FILE_SIZE_BYTES:
            return json.dumps({"error": "file_too_large", "max_mb": 50, "actual_mb": round(head["ContentLength"] / 1024 / 1024, 1)})
    except Exception as e:
        return json.dumps({"error": "staging_file_not_found", "message": str(e)})

    doc_id = uuid.uuid4().hex[:12]
    safe_name = _safe_filename(filename)
    document_key = f"{s3_prefix}{doc_id}-{safe_name}"

    try:
        s3.copy_object(Bucket=S3_BUCKET, CopySource={"Bucket": S3_BUCKET, "Key": staging_key}, Key=document_key)
    except Exception as e:
        return json.dumps({"error": "copy_failed", "message": str(e)})

    ingestion_job_id = None
    try:
        ingest_resp = bedrock.start_ingestion_job(knowledgeBaseId=bedrock_kb_id, dataSourceId=data_source_id)
        ingestion_job_id = ingest_resp["ingestionJob"]["ingestionJobId"]
    except Exception as e:
        return json.dumps({"error": "ingestion_start_failed", "message": str(e), "document_key": document_key})

    try:
        ddb.update_item(
            TableName=KB_TABLE,
            Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}},
            UpdateExpression="SET last_ingestion_job_id = :job, updated_at = :now",
            ExpressionAttributeValues={":job": {"S": ingestion_job_id}, ":now": {"S": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception:
        pass

    return json.dumps({
        "document_key": document_key,
        "ingestion_job_id": ingestion_job_id,
        "status": "IN_PROGRESS",
        "message": f"Document '{filename}' uploaded. Ingestion started (usually 1-3 minutes).",
    }, ensure_ascii=False)

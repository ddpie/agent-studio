"""kb_check_ingestion — Check the status of a Knowledge Base ingestion job."""

import json
import boto3
from strands import tool

from config import REGION, KB_TABLE
from tools._scope import current_workspace


@tool
def kb_check_ingestion(kb_id: str, ingestion_job_id: str = "") -> str:
    """Check ingestion job status for a Knowledge Base.

    Args:
        kb_id: The Knowledge Base ID.
        ingestion_job_id: Specific job ID. If empty, checks the most recent job.

    Returns:
        JSON with status, documents_processed, documents_failed, failure_reasons.
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "no_workspace"})

    ddb = boto3.client("dynamodb", region_name=REGION)
    bedrock = boto3.client("bedrock-agent", region_name=REGION)

    try:
        resp = ddb.get_item(TableName=KB_TABLE, Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}})
        item = resp.get("Item")
        if not item:
            return json.dumps({"error": "kb_not_found"})
    except Exception as e:
        return json.dumps({"error": "ddb_read_failed", "message": str(e)})

    bedrock_kb_id = item["bedrock_kb_id"]["S"]
    data_source_id = item["data_source_id"]["S"]
    job_id = ingestion_job_id or item.get("last_ingestion_job_id", {}).get("S", "")

    if not job_id:
        return json.dumps({"error": "no_ingestion_job", "message": "No ingestion has been started yet."})

    try:
        job_resp = bedrock.get_ingestion_job(
            knowledgeBaseId=bedrock_kb_id, dataSourceId=data_source_id, ingestionJobId=job_id
        )
        job = job_resp["ingestionJob"]
        stats = job.get("statistics", {})

        scanned = stats.get("numberOfDocumentsScanned", 0)
        new_indexed = stats.get("numberOfNewDocumentsIndexed", 0)
        modified = stats.get("numberOfModifiedDocumentsIndexed", 0)
        failed = stats.get("numberOfDocumentsFailed", 0)
        successful = scanned - failed

        if failed == 0:
            summary = f"全部 {scanned} 个文档索引完成（{new_indexed} 个新增，{modified} 个更新，{scanned - new_indexed - modified} 个无变化）"
        else:
            summary = f"{scanned} 个文档中 {successful} 个成功，{failed} 个失败"

        return json.dumps({
            "ingestion_job_id": job_id,
            "status": job["status"],
            "summary": summary,
            "total_documents_processed": successful,
            "documents_failed": failed,
            "documents_new": new_indexed,
            "documents_updated": modified,
            "documents_unchanged": scanned - new_indexed - modified - failed,
            "failure_reasons": job.get("failureReasons", []),
            "started_at": str(job.get("startedAt", "")),
            "updated_at": str(job.get("updatedAt", "")),
        }, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": "get_job_failed", "message": str(e)})

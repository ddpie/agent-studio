"""Knowledge Base CRUD endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import KB_TABLE, KB_SERVICE_ROLE_ARN, VECTORS_BUCKET, REGION, ACCOUNT_ID, S3_BUCKET, AGENTS_TABLE
from shared.middleware import auth_check
from shared.response import success, not_found, bad_request, internal_error

router = Router()
logger = Logger(child=True)

_table = None
_s3 = None
_bedrock = None
_s3vectors = None
_agents_table = None

ALLOWED_EXTENSIONS = {"pdf", "md", "txt", "html", "csv", "docx", "xlsx", "pptx"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(KB_TABLE)
    return _table


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _get_bedrock():
    global _bedrock
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-agent", region_name=REGION)
    return _bedrock


def _get_s3vectors():
    global _s3vectors
    if _s3vectors is None:
        _s3vectors = boto3.client("s3vectors", region_name=REGION)
    return _s3vectors


def _kb_response(item: dict) -> dict:
    """Convert DDB item to camelCase API response."""
    return {
        "kbId": item.get("kb_id", ""),
        "workspaceId": item.get("workspace_id", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "status": item.get("status", ""),
        "bedrockKbId": item.get("bedrock_kb_id", ""),
        "dataSourceId": item.get("data_source_id", ""),
        "indexName": item.get("index_name", ""),
        "indexArn": item.get("index_arn", ""),
        "s3Prefix": item.get("s3_prefix", ""),
        "documentCount": item.get("document_count", 0),
        "attachedAgentIds": list(item.get("attached_agent_ids", set())),
        "createdBy": item.get("created_by", ""),
        "createdAt": item.get("created_at", ""),
        "updatedAt": item.get("updated_at", ""),
    }


# ── List ──


@router.get("/api/workspaces/<wsId>/knowledge-bases")
def list_knowledge_bases(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    table = _get_table()
    from boto3.dynamodb.conditions import Key
    resp = table.query(
        KeyConditionExpression=Key("ws_id").eq(ws_id),
    )
    items = [_kb_response(i) for i in resp.get("Items", []) if i.get("status") != "DELETED"]
    return success({"items": items})


# ── Get ──


@router.get("/api/workspaces/<wsId>/knowledge-bases/<kbId>")
def get_knowledge_base(wsId: str, kbId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    table = _get_table()
    resp = table.get_item(Key={"ws_id": ws_id, "kb_id": kbId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item:
        return not_found("Knowledge base not found")

    kb = _kb_response(item)
    s3 = _get_s3()
    prefix = item.get("s3_prefix", "")

    # Documents
    documents = []
    if prefix:
        try:
            list_resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix, MaxKeys=50)
            for obj in list_resp.get("Contents", []):
                documents.append({
                    "key": obj["Key"],
                    "filename": obj["Key"].split("/")[-1],
                    "sizeBytes": obj["Size"],
                    "lastModified": obj["LastModified"].isoformat(),
                })
        except Exception:
            pass
    kb["documents"] = documents
    kb["docCount"] = len(documents)

    # Ingestion status
    last_job = item.get("last_ingestion_job_id")
    data_source_id = item.get("data_source_id", "")
    bedrock_kb_id = item.get("bedrock_kb_id", "")
    if last_job and data_source_id and bedrock_kb_id:
        bedrock = _get_bedrock()
        try:
            job_resp = bedrock.get_ingestion_job(
                knowledgeBaseId=bedrock_kb_id, dataSourceId=data_source_id, ingestionJobId=last_job
            )
            job = job_resp["ingestionJob"]
            stats = job.get("statistics", {})
            kb["ingestion"] = {
                "jobId": last_job,
                "status": job["status"],
                "documentsScanned": stats.get("numberOfDocumentsScanned", 0),
                "documentsIndexed": stats.get("numberOfNewDocumentsIndexed", 0) + stats.get("numberOfModifiedDocumentsIndexed", 0),
                "documentsFailed": stats.get("numberOfDocumentsFailed", 0),
                "failureReasons": job.get("failureReasons", []),
            }
        except Exception:
            pass

    return success(kb)


# ── Create ──


@router.post("/api/workspaces/<wsId>/knowledge-bases")
def create_knowledge_base(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    name = body.get("name", "").strip()
    if not name:
        return bad_request("name is required")
    if len(name) > 200:
        return bad_request("name must be 200 characters or less")

    description = body.get("description", "").strip()
    kb_id = f"kb_{uuid.uuid4().hex[:16]}"
    index_name = f"kb{kb_id.replace('_', '').replace('-', '')}"
    s3_prefix = f"kb/{ws_id}/{kb_id}/documents/"
    now = datetime.utcnow().isoformat() + "Z"

    index_arn = None
    bedrock_kb_id = None
    data_source_id = None

    try:
        # 1. Create S3 Vectors index
        s3vectors = _get_s3vectors()
        idx_resp = s3vectors.create_index(
            vectorBucketName=VECTORS_BUCKET,
            indexName=index_name,
            dimension=1024,
            distanceMetric="cosine",
            dataType="float32",
        )
        index_arn = idx_resp["indexArn"]

        # 2. Create Bedrock Knowledge Base
        bedrock = _get_bedrock()
        vector_bucket_arn = f"arn:aws:s3vectors:{REGION}:{ACCOUNT_ID}:bucket/{VECTORS_BUCKET}"
        kb_resp = bedrock.create_knowledge_base(
            name=f"{ws_id}-{kb_id}",
            description=description or name,
            roleArn=KB_SERVICE_ROLE_ARN,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0",
                },
            },
            storageConfiguration={
                "type": "S3_VECTORS",
                "s3VectorsConfiguration": {
                    "vectorBucketArn": vector_bucket_arn,
                    "indexArn": index_arn,
                },
            },
        )
        bedrock_kb_id = kb_resp["knowledgeBase"]["knowledgeBaseId"]

        # 3. Create Data Source
        ds_resp = bedrock.create_data_source(
            knowledgeBaseId=bedrock_kb_id,
            name=f"{kb_id}-source",
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": f"arn:aws:s3:::{S3_BUCKET}",
                    "inclusionPrefixes": [s3_prefix],
                },
            },
            dataDeletionPolicy="DELETE",
        )
        data_source_id = ds_resp["dataSource"]["dataSourceId"]

    except Exception as e:
        logger.exception("Failed to create KB infrastructure for %s", kb_id)
        # Best-effort cleanup
        _cleanup_kb_infra(bedrock_kb_id, data_source_id, index_name)
        return internal_error(f"Failed to create knowledge base: {str(e)}")

    # 4. Write DDB item
    item = {
        "kb_id": kb_id,
        "workspace_id": ws_id,
        "name": name,
        "description": description,
        "status": "ACTIVE",
        "bedrock_kb_id": bedrock_kb_id,
        "data_source_id": data_source_id,
        "index_name": index_name,
        "index_arn": index_arn,
        "s3_prefix": s3_prefix,
        "document_count": 0,
        "attached_agent_ids": set(),
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }

    table = _get_table()
    table.put_item(Item=item)

    return success(_kb_response(item), status_code=201)


def _cleanup_kb_infra(bedrock_kb_id: str | None, data_source_id: str | None, index_name: str | None):
    """Best-effort cleanup on creation failure."""
    try:
        if data_source_id and bedrock_kb_id:
            _get_bedrock().delete_data_source(
                knowledgeBaseId=bedrock_kb_id,
                dataSourceId=data_source_id,
            )
    except Exception:
        logger.warning("Cleanup: failed to delete data source %s", data_source_id)

    try:
        if bedrock_kb_id:
            _get_bedrock().delete_knowledge_base(knowledgeBaseId=bedrock_kb_id)
    except Exception:
        logger.warning("Cleanup: failed to delete KB %s", bedrock_kb_id)

    try:
        if index_name:
            _get_s3vectors().delete_index(
                vectorBucketName=VECTORS_BUCKET,
                indexName=index_name,
            )
    except Exception:
        logger.warning("Cleanup: failed to delete vector index %s", index_name)


# ── Delete ──


@router.delete("/api/workspaces/<wsId>/knowledge-bases/<kbId>")
def delete_knowledge_base(wsId: str, kbId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    params = router.current_event.query_string_parameters or {}
    if params.get("confirm") != "true":
        return bad_request("Must pass ?confirm=true to delete a knowledge base")

    table = _get_table()
    resp = table.get_item(Key={"ws_id": ws_id, "kb_id": kbId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item or item.get("workspace_id") != ws_id:
        return not_found("Knowledge base not found")

    # Mark as DELETING
    now = datetime.utcnow().isoformat() + "Z"
    table.update_item(
        Key={"ws_id": ws_id, "kb_id": kbId},
        UpdateExpression="SET #s = :del, updated_at = :now",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":del": "DELETING", ":now": now},
    )

    bedrock_kb_id = item.get("bedrock_kb_id", "")
    data_source_id = item.get("data_source_id", "")
    index_name = item.get("index_name", "")
    s3_prefix = item.get("s3_prefix", "")
    attached_agent_ids = item.get("attached_agent_ids", set())

    # 1. Delete Data Source
    try:
        if data_source_id and bedrock_kb_id:
            _get_bedrock().delete_data_source(
                knowledgeBaseId=bedrock_kb_id,
                dataSourceId=data_source_id,
            )
    except Exception:
        logger.warning("Delete KB: failed to delete data source %s", data_source_id)

    # 2. Delete Knowledge Base
    try:
        if bedrock_kb_id:
            _get_bedrock().delete_knowledge_base(knowledgeBaseId=bedrock_kb_id)
    except Exception:
        logger.warning("Delete KB: failed to delete Bedrock KB %s", bedrock_kb_id)

    # 3. Delete S3 documents
    try:
        if s3_prefix:
            s3 = _get_s3()
            while True:
                list_resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=s3_prefix, MaxKeys=1000)
                objects = list_resp.get("Contents", [])
                if not objects:
                    break
                s3.delete_objects(
                    Bucket=S3_BUCKET,
                    Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
                )
                if not list_resp.get("IsTruncated"):
                    break
    except Exception:
        logger.warning("Delete KB: failed to delete S3 objects at %s", s3_prefix)

    # 4. Delete Vector index
    try:
        if index_name:
            _get_s3vectors().delete_index(
                vectorBucketName=VECTORS_BUCKET,
                indexName=index_name,
            )
    except Exception:
        logger.warning("Delete KB: failed to delete vector index %s", index_name)

    # 5. Clean agent bindings
    if attached_agent_ids:
        agents_table = _get_agents_table()
        for agent_id in attached_agent_ids:
            try:
                agents_table.update_item(
                    Key={"agentId": agent_id},
                    UpdateExpression="DELETE knowledge_base_ids :kb_set",
                    ExpressionAttributeValues={":kb_set": {kbId}},
                )
            except Exception:
                logger.warning("Delete KB: failed to unlink agent %s", agent_id)

    # 6. Delete DDB item
    table.delete_item(Key={"ws_id": ws_id, "kb_id": kbId})

    return success({"deleted": True, "kbId": kbId})


# ── Upload Document ──


@router.post("/api/workspaces/<wsId>/knowledge-bases/<kbId>/documents")
def upload_document(wsId: str, kbId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    staging_key = body.get("stagingKey", "").strip()
    file_name = body.get("fileName", "").strip()

    if not staging_key:
        return bad_request("stagingKey is required")
    if not file_name:
        return bad_request("fileName is required")

    # Validate extension
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    if ext not in ALLOWED_EXTENSIONS:
        return bad_request(f"File type '.{ext}' not allowed. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    # Verify KB exists and belongs to workspace
    table = _get_table()
    resp = table.get_item(Key={"ws_id": ws_id, "kb_id": kbId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item or item.get("workspace_id") != ws_id:
        return not_found("Knowledge base not found")
    if item.get("status") != "ACTIVE":
        return bad_request("Knowledge base is not active")

    s3 = _get_s3()

    # Check file size via HeadObject
    try:
        head = s3.head_object(Bucket=S3_BUCKET, Key=staging_key)
        file_size = head.get("ContentLength", 0)
        if file_size > MAX_FILE_SIZE:
            return bad_request(f"File exceeds maximum size of 50MB (got {file_size} bytes)")
    except Exception:
        return bad_request("Staging file not found or inaccessible")

    # Copy from staging to KB prefix
    s3_prefix = item.get("s3_prefix", "")
    dest_key = f"{s3_prefix}{file_name}"

    try:
        s3.copy_object(
            Bucket=S3_BUCKET,
            Key=dest_key,
            CopySource={"Bucket": S3_BUCKET, "Key": staging_key},
        )
    except Exception:
        logger.exception("Failed to copy document from staging to KB prefix")
        return internal_error("Failed to copy document")

    # Update document count
    now = datetime.utcnow().isoformat() + "Z"
    table.update_item(
        Key={"ws_id": ws_id, "kb_id": kbId},
        UpdateExpression="SET document_count = document_count + :one, updated_at = :now",
        ExpressionAttributeValues={":one": 1, ":now": now},
    )

    # Start ingestion job
    ingestion_job_id = None
    try:
        bedrock = _get_bedrock()
        ingest_resp = bedrock.start_ingestion_job(
            knowledgeBaseId=item.get("bedrock_kb_id", ""),
            dataSourceId=item.get("data_source_id", ""),
        )
        ingestion_job_id = ingest_resp.get("ingestionJob", {}).get("ingestionJobId")
    except Exception:
        logger.warning("Failed to start ingestion job for KB %s", kbId)

    return success({
        "kbId": kbId,
        "fileName": file_name,
        "destKey": dest_key,
        "ingestionJobId": ingestion_job_id,
        "uploadedAt": now,
    }, status_code=201)


# ── Delete Document ──


@router.post("/api/workspaces/<wsId>/knowledge-bases/<kbId>/documents/delete")
def delete_document(wsId: str, kbId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    file_name = body.get("fileName", "").strip()
    if not file_name:
        return bad_request("fileName is required")

    # Verify KB
    table = _get_table()
    resp = table.get_item(Key={"ws_id": ws_id, "kb_id": kbId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item or item.get("workspace_id") != ws_id:
        return not_found("Knowledge base not found")

    s3_prefix = item.get("s3_prefix", "")
    doc_key = f"{s3_prefix}{file_name}"

    # Delete from S3
    s3 = _get_s3()
    try:
        s3.delete_object(Bucket=S3_BUCKET, Key=doc_key)
    except Exception:
        logger.exception("Failed to delete document %s", doc_key)
        return internal_error("Failed to delete document")

    # Decrement document count
    now = datetime.utcnow().isoformat() + "Z"
    table.update_item(
        Key={"ws_id": ws_id, "kb_id": kbId},
        UpdateExpression="SET document_count = document_count - :one, updated_at = :now",
        ExpressionAttributeValues={":one": 1, ":now": now},
    )

    # Start re-ingestion to sync the index
    try:
        bedrock = _get_bedrock()
        bedrock.start_ingestion_job(
            knowledgeBaseId=item.get("bedrock_kb_id", ""),
            dataSourceId=item.get("data_source_id", ""),
        )
    except Exception:
        logger.warning("Failed to start re-ingestion after document delete for KB %s", kbId)

    return success({"deleted": True, "fileName": file_name})


# ── Ingestion Status ──


@router.get("/api/workspaces/<wsId>/knowledge-bases/<kbId>/ingestion")
def get_ingestion_status(wsId: str, kbId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    # Verify KB
    table = _get_table()
    resp = table.get_item(Key={"ws_id": ws_id, "kb_id": kbId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item or item.get("workspace_id") != ws_id:
        return not_found("Knowledge base not found")

    bedrock_kb_id = item.get("bedrock_kb_id", "")
    data_source_id = item.get("data_source_id", "")

    if not bedrock_kb_id or not data_source_id:
        return success({"jobs": []})

    try:
        bedrock = _get_bedrock()
        list_resp = bedrock.list_ingestion_jobs(
            knowledgeBaseId=bedrock_kb_id,
            dataSourceId=data_source_id,
            maxResults=5,
            sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        )
        jobs = []
        for job in list_resp.get("ingestionJobSummaries", []):
            jobs.append({
                "ingestionJobId": job.get("ingestionJobId", ""),
                "status": job.get("status", ""),
                "startedAt": job.get("startedAt", "").isoformat() if job.get("startedAt") else "",
                "updatedAt": job.get("updatedAt", "").isoformat() if job.get("updatedAt") else "",
                "statistics": job.get("statistics", {}),
            })
        return success({"jobs": jobs})
    except Exception:
        logger.exception("Failed to list ingestion jobs for KB %s", kbId)
        return internal_error("Failed to fetch ingestion status")

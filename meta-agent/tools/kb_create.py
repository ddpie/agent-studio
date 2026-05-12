"""kb_create — Create a new Knowledge Base with S3 Vectors backend."""

import json
import uuid
from datetime import datetime, timezone

import boto3
from strands import tool

from config import REGION, ACCOUNT_ID, S3_BUCKET, KB_TABLE, KB_SERVICE_ROLE_ARN, VECTORS_BUCKET
from tools._scope import current_workspace, current_caller


@tool
def kb_create(name: str, description: str = "") -> str:
    """Create a new Knowledge Base backed by S3 Vectors and Cohere Multilingual v3 embedding.

    The KB is created in the current workspace. After creation, upload documents
    with kb_upload_document and attach to agents with kb_attach_to_agent.

    Args:
        name: Human-readable name for the KB. Must be unique within the workspace.
        description: Optional description.

    Returns:
        JSON with kb_id, bedrock_kb_id, status, or error.
    """
    ws_id = current_workspace()
    caller_id = current_caller()

    if not ws_id:
        return json.dumps({"error": "no_workspace", "message": "No workspace context."})

    if REGION not in ("us-east-1", "us-west-2"):
        return json.dumps({"error": "region_unsupported", "message": f"Knowledge Bases with S3 Vectors not available in {REGION}"})

    kb_id = f"kb_{uuid.uuid4().hex[:16]}"
    vector_index_name = f"kb{kb_id.replace('_', '').replace('-', '')}"
    s3_prefix = f"kb/{ws_id}/{kb_id}/documents/"
    now = datetime.now(timezone.utc).isoformat()

    s3v_client = boto3.client("s3vectors", region_name=REGION)
    bedrock_client = boto3.client("bedrock-agent", region_name=REGION)
    ddb = boto3.client("dynamodb", region_name=REGION)

    # Check name uniqueness in workspace
    try:
        resp = ddb.query(
            TableName=KB_TABLE,
            KeyConditionExpression="ws_id = :ws",
            FilterExpression="#n = :name",
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues={":ws": {"S": ws_id}, ":name": {"S": name}},
        )
        if resp.get("Items"):
            return json.dumps({"error": "name_exists", "message": f"KB named '{name}' already exists in this workspace."})
    except Exception as e:
        return json.dumps({"error": "ddb_check_failed", "message": str(e)})

    # Step 1: Create vector index
    index_arn = None
    try:
        idx_resp = s3v_client.create_index(
            vectorBucketName=VECTORS_BUCKET,
            indexName=vector_index_name,
            dimension=1024,
            distanceMetric="cosine",
            dataType="float32",
            metadataConfiguration={
                "nonFilterableMetadataKeys": ["AMAZON_BEDROCK_TEXT", "AMAZON_BEDROCK_METADATA"],
            },
        )
        index_arn = idx_resp["indexArn"]
    except Exception as e:
        return json.dumps({"error": "vector_index_failed", "message": str(e)})

    # Step 2: Create Bedrock Knowledge Base
    bedrock_kb_id = None
    bedrock_kb_arn = None
    try:
        kb_resp = bedrock_client.create_knowledge_base(
            name=f"as-{ws_id[:8]}-{kb_id}",
            description=description or f"Agent Studio KB: {name}",
            roleArn=KB_SERVICE_ROLE_ARN,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": f"arn:aws:bedrock:{REGION}::foundation-model/cohere.embed-multilingual-v3",
                },
            },
            storageConfiguration={
                "type": "S3_VECTORS",
                "s3VectorsConfiguration": {
                    "vectorBucketArn": f"arn:aws:s3vectors:{REGION}:{ACCOUNT_ID}:bucket/{VECTORS_BUCKET}",
                    "indexArn": index_arn,
                },
            },
        )
        bedrock_kb_id = kb_resp["knowledgeBase"]["knowledgeBaseId"]
        bedrock_kb_arn = kb_resp["knowledgeBase"]["knowledgeBaseArn"]
    except Exception as e:
        try:
            s3v_client.delete_index(vectorBucketName=VECTORS_BUCKET, indexName=vector_index_name)
        except Exception:
            pass
        return json.dumps({"error": "create_kb_failed", "message": str(e)})

    # Step 3: Create data source
    data_source_id = None
    try:
        ds_resp = bedrock_client.create_data_source(
            knowledgeBaseId=bedrock_kb_id,
            name=f"s3-{kb_id}",
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
        try:
            bedrock_client.delete_knowledge_base(knowledgeBaseId=bedrock_kb_id)
        except Exception:
            pass
        try:
            s3v_client.delete_index(vectorBucketName=VECTORS_BUCKET, indexName=vector_index_name)
        except Exception:
            pass
        return json.dumps({"error": "create_datasource_failed", "message": str(e)})

    # Step 4: Write DDB record
    try:
        ddb.put_item(
            TableName=KB_TABLE,
            Item={
                "ws_id": {"S": ws_id},
                "kb_id": {"S": kb_id},
                "name": {"S": name},
                "description": {"S": description},
                "bedrock_kb_id": {"S": bedrock_kb_id},
                "bedrock_kb_arn": {"S": bedrock_kb_arn},
                "data_source_id": {"S": data_source_id},
                "s3_prefix": {"S": s3_prefix},
                "vector_index_name": {"S": vector_index_name},
                "embedding_model": {"S": "cohere.embed-multilingual-v3"},
                "status": {"S": "ACTIVE"},
                "created_at": {"S": now},
                "updated_at": {"S": now},
                "created_by": {"S": caller_id},
            },
        )
    except Exception as e:
        return json.dumps({"error": "ddb_write_failed", "message": str(e), "kb_id": kb_id, "bedrock_kb_id": bedrock_kb_id})

    return json.dumps({
        "kb_id": kb_id,
        "bedrock_kb_id": bedrock_kb_id,
        "name": name,
        "status": "ACTIVE",
        "s3_prefix": s3_prefix,
        "message": f"Knowledge Base '{name}' created. Upload documents with kb_upload_document.",
    }, ensure_ascii=False)

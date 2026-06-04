"""Tests for crud.kb — Knowledge Base CRUD + ingestion."""
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_kb_table():
    with patch("crud.kb._get_table") as g:
        t = MagicMock()
        t.name = "test-kb"
        t.get_item.return_value = {"Item": None}
        # Explicit LastEvaluatedKey to avoid hangs in any pagination loops
        t.query.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.scan.return_value = {"Items": [], "LastEvaluatedKey": None}
        t.put_item.return_value = {}
        t.update_item.return_value = {}
        t.delete_item.return_value = {}
        g.return_value = t
        yield t


@pytest.fixture
def mock_agents_table():
    with patch("crud.kb._get_agents_table") as g:
        t = MagicMock()
        t.name = "test-agents"
        t.get_item.return_value = {"Item": None}
        t.update_item.return_value = {}
        g.return_value = t
        yield t


@pytest.fixture
def mock_kb_s3():
    with patch("crud.kb._get_s3") as g:
        s = MagicMock()
        s.list_objects_v2.return_value = {
            "Contents": [],
            "KeyCount": 0,
            "IsTruncated": False,
        }
        s.head_object.return_value = {"ContentLength": 1000}
        s.copy_object.return_value = {}
        s.delete_object.return_value = {}
        s.delete_objects.return_value = {}
        g.return_value = s
        yield s


@pytest.fixture
def mock_bedrock():
    with patch("crud.kb._get_bedrock") as g:
        b = MagicMock()
        b.create_knowledge_base.return_value = {
            "knowledgeBase": {"knowledgeBaseId": "kb-bedrock-1"}
        }
        b.create_data_source.return_value = {
            "dataSource": {"dataSourceId": "ds-1"}
        }
        b.start_ingestion_job.return_value = {
            "ingestionJob": {"ingestionJobId": "job-1"}
        }
        b.get_ingestion_job.return_value = {
            "ingestionJob": {
                "status": "COMPLETE",
                "statistics": {
                    "numberOfDocumentsScanned": 5,
                    "numberOfDocumentsFailed": 0,
                    "numberOfNewDocumentsIndexed": 5,
                    "numberOfModifiedDocumentsIndexed": 0,
                },
                "failureReasons": [],
            }
        }
        b.list_ingestion_jobs.return_value = {"ingestionJobSummaries": []}
        b.delete_data_source.return_value = {}
        b.delete_knowledge_base.return_value = {}
        g.return_value = b
        yield b


@pytest.fixture
def mock_s3vectors():
    with patch("crud.kb._get_s3vectors") as g:
        v = MagicMock()
        v.create_index.return_value = {
            "indexArn": "arn:aws:s3vectors:us-east-1:123:bucket/vb/index/idx",
        }
        v.delete_index.return_value = {}
        g.return_value = v
        yield v


@pytest.fixture
def mock_ddb_client():
    with patch("crud.kb._get_ddb_client") as g:
        c = MagicMock()
        c.transact_write_items.return_value = {}
        g.return_value = c
        yield c


@pytest.fixture
def _mock_editor(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "editor",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_viewer(workspace_id, user_id):
    member = {
        "workspaceId": workspace_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "viewer",
    }
    with patch("shared.middleware.get_membership", return_value=member):
        yield


@pytest.fixture
def _mock_no_membership():
    with patch("shared.middleware.get_membership", return_value=None):
        yield


def _apigw(method, path, body=None, query_params=None):
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "headers": {"Authorization": "Bearer tok", "Content-Type": "application/json"},
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query_params or {},
        "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
        "isBase64Encoded": False,
    }


def _invoke(event):
    from crud.handler import lambda_handler
    return lambda_handler(event, MagicMock())


def _kb_item(workspace_id, kb_id="kb_abc123def456", name="My KB", status="ACTIVE", **extra):
    base = {
        "ws_id": workspace_id,
        "kb_id": kb_id,
        "name": name,
        "description": "desc",
        "status": status,
        "bedrock_kb_id": "kb-bedrock-1",
        "data_source_id": "ds-1",
        "index_name": "kbabc",
        "index_arn": "arn:aws:s3vectors:us-east-1:1:bucket/vb/index/kbabc",
        "s3_prefix": f"kb/{workspace_id}/{kb_id}/documents/",
        "created_by": "u1",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# List / Get
# ---------------------------------------------------------------------------


class TestListKnowledgeBases:
    def test_empty(self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_kb_s3):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/knowledge-bases"))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["items"] == []

    def test_returns_active_only(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_kb_s3
    ):
        mock_kb_table.query.return_value = {
            "Items": [
                _kb_item(workspace_id, "kb_a", "A"),
                _kb_item(workspace_id, "kb_b", "B", status="DELETED"),
            ],
            "LastEvaluatedKey": None,
        }
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/knowledge-bases"))
        data = json.loads(resp["body"])
        ids = [k["kbId"] for k in data["items"]]
        assert ids == ["kb_a"]

    def test_no_membership_forbidden(
        self, workspace_id, mock_jwt, _mock_no_membership, mock_kb_table
    ):
        resp = _invoke(_apigw("GET", f"/api/workspaces/{workspace_id}/knowledge-bases"))
        assert resp["statusCode"] == 403


class TestGetKnowledgeBase:
    def test_not_found(self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_kb_s3):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_x",
        ))
        assert resp["statusCode"] == 404

    def test_with_documents(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_kb_s3
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": f"kb/{workspace_id}/kb_abc123def456/documents/file.pdf",
                    "Size": 12345,
                    "LastModified": datetime(2026, 4, 1, tzinfo=timezone.utc),
                },
            ],
            "KeyCount": 1,
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["documents"]) == 1
        assert data["documents"][0]["filename"] == "file.pdf"

    def test_with_ingestion_status(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, last_ingestion_job_id="job-1"
        )}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
        ))
        data = json.loads(resp["body"])
        assert data["ingestion"]["status"] == "COMPLETE"
        assert data["ingestion"]["documentsScanned"] == 5
        assert data["ingestion"]["documentsIndexed"] == 5


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateKnowledgeBase:
    def test_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        body = {"name": "My KB", "description": "desc"}
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/knowledge-bases", body=body
        ))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["name"] == "My KB"
        assert data["status"] == "ACTIVE"
        assert data["bedrockKbId"] == "kb-bedrock-1"
        assert data["dataSourceId"] == "ds-1"
        # Each AWS resource was created
        mock_s3vectors.create_index.assert_called_once()
        mock_bedrock.create_knowledge_base.assert_called_once()
        mock_bedrock.create_data_source.assert_called_once()
        mock_kb_table.put_item.assert_called_once()

    def test_missing_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/knowledge-bases", body={}
        ))
        assert resp["statusCode"] == 400
        assert "name" in json.loads(resp["body"])["error"]

    def test_name_too_long(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases",
            body={"name": "A" * 201},
        ))
        assert resp["statusCode"] == 400

    def test_create_index_failure_returns_500(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        mock_s3vectors.create_index.side_effect = Exception("vector boom")
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/knowledge-bases",
            body={"name": "X"},
        ))
        assert resp["statusCode"] == 500
        # DDB write should NOT happen
        mock_kb_table.put_item.assert_not_called()

    def test_create_kb_failure_cleans_up_index(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        mock_bedrock.create_knowledge_base.side_effect = Exception("kb boom")
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/knowledge-bases",
            body={"name": "X"},
        ))
        assert resp["statusCode"] == 500
        # Cleanup: index deleted
        mock_s3vectors.delete_index.assert_called_once()

    def test_viewer_forbidden(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table
    ):
        resp = _invoke(_apigw(
            "POST", f"/api/workspaces/{workspace_id}/knowledge-bases",
            body={"name": "X"},
        ))
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteKnowledgeBase:
    def test_requires_confirm(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 400
        assert "confirm" in json.loads(resp["body"])["error"]

    def test_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_x",
            query_params={"confirm": "true"},
        ))
        assert resp["statusCode"] == 404

    def test_full_delete(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, attached_agent_ids={"agt-1", "agt-2"}
        )}
        # S3 list returns one page of objects then empty (loop exit)
        mock_kb_s3.list_objects_v2.side_effect = [
            {"Contents": [{"Key": "kb/x/doc1.pdf"}], "IsTruncated": False},
            {"Contents": [], "IsTruncated": False},
        ]
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
            query_params={"confirm": "true"},
        ))
        assert resp["statusCode"] == 200
        # Status set to DELETING
        update_calls = mock_kb_table.update_item.call_args_list
        assert any(":del" in str(c) and "DELETING" in str(c) for c in update_calls)
        # Bedrock cleanup
        mock_bedrock.delete_data_source.assert_called_once()
        mock_bedrock.delete_knowledge_base.assert_called_once()
        # S3 docs deleted
        mock_kb_s3.delete_objects.assert_called_once()
        # Vector index deleted
        mock_s3vectors.delete_index.assert_called_once()
        # Agents unlinked (2 agents)
        assert mock_agents_table.update_item.call_count == 2
        # Final delete
        mock_kb_table.delete_item.assert_called_once()

    def test_delete_continues_on_partial_failure(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        """Delete must be idempotent — best-effort cleanup, not transactional."""
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_bedrock.delete_data_source.side_effect = Exception("ds gone")
        mock_bedrock.delete_knowledge_base.side_effect = Exception("kb gone")
        # Empty S3 (no docs to delete)
        mock_kb_s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
            query_params={"confirm": "true"},
        ))
        assert resp["statusCode"] == 200
        mock_kb_table.delete_item.assert_called_once()


# ---------------------------------------------------------------------------
# Upload Document
# ---------------------------------------------------------------------------


class TestUploadDocument:
    def test_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        body = {"stagingKey": "uploads/staging/u1/file.pdf", "fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents",
            body=body,
        ))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["fileName"] == "file.pdf"
        assert data["ingestionJobId"] == "job-1"
        mock_kb_s3.copy_object.assert_called_once()
        mock_bedrock.start_ingestion_job.assert_called_once()
        mock_kb_table.update_item.assert_called_once()

    def test_missing_staging_key(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_kb_s3
    ):
        body = {"fileName": "x.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc/documents",
            body=body,
        ))
        assert resp["statusCode"] == 400
        assert "stagingKey" in json.loads(resp["body"])["error"]

    def test_missing_file_name(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table
    ):
        body = {"stagingKey": "x"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc/documents",
            body=body,
        ))
        assert resp["statusCode"] == 400
        assert "fileName" in json.loads(resp["body"])["error"]

    def test_invalid_extension(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table
    ):
        body = {"stagingKey": "x", "fileName": "evil.exe"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc/documents",
            body=body,
        ))
        assert resp["statusCode"] == 400
        assert "exe" in json.loads(resp["body"])["error"]

    def test_kb_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table
    ):
        body = {"stagingKey": "x", "fileName": "x.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_x/documents",
            body=body,
        ))
        assert resp["statusCode"] == 404

    def test_kb_not_active(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_kb_s3
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, status="DELETING"
        )}
        body = {"stagingKey": "x", "fileName": "x.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc/documents",
            body=body,
        ))
        assert resp["statusCode"] == 400
        assert "not active" in json.loads(resp["body"])["error"]

    def test_file_too_large(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_kb_s3
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.head_object.return_value = {"ContentLength": 100 * 1024 * 1024}
        body = {"stagingKey": "x", "fileName": "huge.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc/documents",
            body=body,
        ))
        assert resp["statusCode"] == 400
        assert "50MB" in json.loads(resp["body"])["error"] or "exceeds" in json.loads(resp["body"])["error"].lower()

    def test_staging_file_missing(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_kb_s3
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.head_object.side_effect = Exception("nope")
        body = {"stagingKey": "x", "fileName": "x.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc/documents",
            body=body,
        ))
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# Delete Document
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    def test_success_by_filename(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        body = {"fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 200
        mock_kb_s3.delete_object.assert_called_once()
        # Re-ingestion triggered
        mock_bedrock.start_ingestion_job.assert_called_once()

    def test_kb_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table
    ):
        body = {"fileName": "x.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_x/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 404

    def test_doc_key_outside_prefix_rejected(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_kb_s3
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        body = {"documentKey": "kb/other-ws/foo.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 400
        assert "does not belong" in json.loads(resp["body"])["error"]

    def test_missing_inputs(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table
    ):
        body = {}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# Ingestion Status
# ---------------------------------------------------------------------------


class TestIngestionStatus:
    def test_kb_not_found(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table
    ):
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_x/ingestion",
        ))
        assert resp["statusCode"] == 404

    def test_returns_jobs(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_bedrock.list_ingestion_jobs.return_value = {
            "ingestionJobSummaries": [
                {
                    "ingestionJobId": "job-1",
                    "status": "COMPLETE",
                    "startedAt": datetime(2026, 4, 1, tzinfo=timezone.utc),
                    "updatedAt": datetime(2026, 4, 1, tzinfo=timezone.utc),
                    "statistics": {},
                },
            ]
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/ingestion",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["ingestionJobId"] == "job-1"

    def test_no_bedrock_id_returns_empty(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, bedrock_kb_id="", data_source_id=""
        )}
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/ingestion",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["jobs"] == []

    def test_bedrock_failure_returns_500(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_bedrock.list_ingestion_jobs.side_effect = Exception("api boom")
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/ingestion",
        ))
        assert resp["statusCode"] == 500


# ---------------------------------------------------------------------------
# Attach / Detach Agent ↔ KB
# ---------------------------------------------------------------------------


class TestAttachKnowledgeBase:
    def test_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": set(),
            }
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        mock_ddb_client.transact_write_items.assert_called_once()

    def test_kb_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_x",
        ))
        assert resp["statusCode"] == 404

    def test_kb_being_deleted(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, status="DELETING"
        )}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc",
        ))
        assert resp["statusCode"] == 400

    def test_agent_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-x/knowledge-bases/kb_abc",
        ))
        assert resp["statusCode"] == 404

    def test_agent_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": "other"}
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc",
        ))
        assert resp["statusCode"] == 404

    def test_already_attached_idempotent(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": {"kb_abc123def456"},
            }
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        mock_ddb_client.transact_write_items.assert_not_called()

    def test_max_kbs_per_agent(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": {f"kb_x{i:03d}" for i in range(5)},
            }
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 400
        assert "5" in json.loads(resp["body"])["error"]

    def test_transact_failure_returns_500(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": workspace_id}
        }
        mock_ddb_client.transact_write_items.side_effect = Exception("boom")
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 500


class TestDetachKnowledgeBase:
    def test_success(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": {"kb_abc123def456"},
            }
        }
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        mock_ddb_client.transact_write_items.assert_called_once()

    def test_not_attached_idempotent(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": set(),
            }
        }
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        mock_ddb_client.transact_write_items.assert_not_called()

    def test_kb_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_x",
        ))
        assert resp["statusCode"] == 404

    def test_agent_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {"Item": None}
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-x/knowledge-bases/kb_abc",
        ))
        assert resp["statusCode"] == 404


# ---------------------------------------------------------------------------
# _kb_response helper
# ---------------------------------------------------------------------------


class TestKbResponse:
    def test_basic_shape(self):
        from crud.kb import _kb_response
        item = {
            "kb_id": "kb_x",
            "ws_id": "ws-1",
            "name": "X",
            "status": "ACTIVE",
            "bedrock_kb_id": "kbb",
            "data_source_id": "ds",
            "index_name": "idx",
            "index_arn": "arn",
            "s3_prefix": "kb/ws-1/kb_x/documents/",
            "attached_agent_ids": {"agt-1"},
            "document_count": 7,
        }
        resp = _kb_response(item)
        assert resp["kbId"] == "kb_x"
        assert resp["workspaceId"] == "ws-1"
        assert resp["docCount"] == 7
        assert resp["attachedAgentIds"] == ["agt-1"]

    def test_count_docs_uses_s3(self):
        from crud.kb import _kb_response
        with patch("crud.kb._count_s3_documents", return_value=12):
            resp = _kb_response({"kb_id": "x", "s3_prefix": "kb/x/"}, count_docs=True)
        assert resp["docCount"] == 12


# ---------------------------------------------------------------------------
# Lazy-init helpers + _count_s3_documents
# ---------------------------------------------------------------------------


class TestLazyInit:
    def test_get_table_caches(self):
        import crud.kb as k
        k._table = None
        with patch("crud.kb.boto3.resource") as mk:
            tbl = MagicMock()
            mk.return_value.Table.return_value = tbl
            t1 = k._get_table()
            t2 = k._get_table()
            assert t1 is t2
        k._table = None

    def test_get_agents_table_caches(self):
        import crud.kb as k
        k._agents_table = None
        with patch("crud.kb.boto3.resource") as mk:
            tbl = MagicMock()
            mk.return_value.Table.return_value = tbl
            t1 = k._get_agents_table()
            t2 = k._get_agents_table()
            assert t1 is t2
        k._agents_table = None

    def test_get_s3_caches(self):
        import crud.kb as k
        k._s3 = None
        with patch("crud.kb.boto3.client") as mk:
            mk.return_value = MagicMock()
            c1 = k._get_s3()
            c2 = k._get_s3()
            assert c1 is c2
        k._s3 = None

    def test_get_bedrock_caches(self):
        import crud.kb as k
        k._bedrock = None
        with patch("crud.kb.boto3.client") as mk:
            mk.return_value = MagicMock()
            c1 = k._get_bedrock()
            c2 = k._get_bedrock()
            assert c1 is c2
        k._bedrock = None

    def test_get_s3vectors_caches(self):
        import crud.kb as k
        k._s3vectors = None
        with patch("crud.kb.boto3.client") as mk:
            mk.return_value = MagicMock()
            c1 = k._get_s3vectors()
            c2 = k._get_s3vectors()
            assert c1 is c2
        k._s3vectors = None

    def test_get_ddb_client_caches(self):
        import crud.kb as k
        k._ddb_client = None
        with patch("crud.kb.boto3.client") as mk:
            mk.return_value = MagicMock()
            c1 = k._get_ddb_client()
            c2 = k._get_ddb_client()
            assert c1 is c2
        k._ddb_client = None


class TestCountS3Documents:
    def test_empty_prefix_returns_zero(self):
        from crud.kb import _count_s3_documents
        assert _count_s3_documents("") == 0

    def test_returns_keycount(self):
        from crud.kb import _count_s3_documents
        fake_s3 = MagicMock()
        fake_s3.list_objects_v2.return_value = {"KeyCount": 7}
        with patch("crud.kb._get_s3", return_value=fake_s3):
            assert _count_s3_documents("kb/x/") == 7

    def test_exception_returns_zero(self):
        from crud.kb import _count_s3_documents
        fake_s3 = MagicMock()
        fake_s3.list_objects_v2.side_effect = Exception("boom")
        with patch("crud.kb._get_s3", return_value=fake_s3):
            assert _count_s3_documents("kb/x/") == 0


# ---------------------------------------------------------------------------
# Auth failures (Bearer token missing / verify_jwt raises)
# ---------------------------------------------------------------------------


class TestAuthFailures:
    """All KB endpoints share auth_check at the top — verify the missing
    Authorization → forbidden() path is wired up."""

    def test_list_no_jwt(self, workspace_id, mock_kb_table):
        resp = _invoke({
            "httpMethod": "GET",
            "path": f"/api/workspaces/{workspace_id}/knowledge-bases",
            "resource": f"/api/workspaces/{workspace_id}/knowledge-bases",
            "pathParameters": {},
            "headers": {"Authorization": ""},
            "body": None,
            "queryStringParameters": {},
            "requestContext": {"identity": {"sourceIp": "127.0.0.1"}},
            "isBase64Encoded": False,
        })
        assert resp["statusCode"] == 403


# ---------------------------------------------------------------------------
# get_knowledge_base — list_objects exception + ingestion error swallowed
# ---------------------------------------------------------------------------


class TestGetKbErrorPaths:
    def test_list_objects_exception_swallowed(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_kb_s3
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.list_objects_v2.side_effect = Exception("s3 down")
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["documents"] == []

    def test_ingestion_get_job_exception_swallowed(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, last_ingestion_job_id="job-1"
        )}
        mock_bedrock.get_ingestion_job.side_effect = Exception("api boom")
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        # ingestion key absent because get_ingestion_job failed
        assert "ingestion" not in data


# ---------------------------------------------------------------------------
# _cleanup_kb_infra: each step's exception is swallowed
# ---------------------------------------------------------------------------


class TestCleanupKbInfra:
    def test_all_steps_run_with_exceptions(self, mock_bedrock, mock_s3vectors):
        from crud.kb import _cleanup_kb_infra
        mock_bedrock.delete_data_source.side_effect = Exception("x")
        mock_bedrock.delete_knowledge_base.side_effect = Exception("y")
        mock_s3vectors.delete_index.side_effect = Exception("z")
        # Should not raise
        _cleanup_kb_infra("kb-1", "ds-1", "idx-1")

    def test_no_args_skips_steps(self, mock_bedrock, mock_s3vectors):
        from crud.kb import _cleanup_kb_infra
        _cleanup_kb_infra(None, None, None)
        mock_bedrock.delete_data_source.assert_not_called()
        mock_bedrock.delete_knowledge_base.assert_not_called()
        mock_s3vectors.delete_index.assert_not_called()


# ---------------------------------------------------------------------------
# Delete KB additional paths
# ---------------------------------------------------------------------------


class TestDeleteKbExtra:
    def test_confirm_via_body(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        """Confirmation can also come via JSON body (POST /delete)."""
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/delete",
            body={"confirm": True},
        ))
        assert resp["statusCode"] == 200

    def test_s3_truncated_continues(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        """S3 list returns IsTruncated=True → loop continues until empty."""
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.list_objects_v2.side_effect = [
            {"Contents": [{"Key": "kb/x/doc1.pdf"}], "IsTruncated": True},
            {"Contents": [{"Key": "kb/x/doc2.pdf"}], "IsTruncated": False},
            {"Contents": [], "IsTruncated": False},
        ]
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
            query_params={"confirm": "true"},
        ))
        assert resp["statusCode"] == 200
        # delete_objects called twice (one per non-empty page)
        assert mock_kb_s3.delete_objects.call_count == 2

    def test_s3_loop_exception_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.list_objects_v2.side_effect = Exception("s3 boom")
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
            query_params={"confirm": "true"},
        ))
        assert resp["statusCode"] == 200

    def test_vector_index_exception_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
        mock_s3vectors.delete_index.side_effect = Exception("x")
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
            query_params={"confirm": "true"},
        ))
        assert resp["statusCode"] == 200

    def test_agent_unlink_exception_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock, mock_s3vectors, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, attached_agent_ids={"agt-1"}
        )}
        mock_kb_s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}
        mock_agents_table.update_item.side_effect = Exception("ddb boom")
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456",
            query_params={"confirm": "true"},
        ))
        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# Upload Document additional paths
# ---------------------------------------------------------------------------


class TestUploadDocExtra:
    def test_copy_object_failure_returns_500(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.copy_object.side_effect = Exception("copy fail")
        body = {"stagingKey": "uploads/staging/u1/file.pdf", "fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents",
            body=body,
        ))
        assert resp["statusCode"] == 500

    def test_ingestion_failure_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_bedrock.start_ingestion_job.side_effect = Exception("ingestion down")
        body = {"stagingKey": "uploads/staging/u1/file.pdf", "fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents",
            body=body,
        ))
        assert resp["statusCode"] == 201
        data = json.loads(resp["body"])
        assert data["ingestionJobId"] is None

    def test_ddb_update_failure_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_table.update_item.side_effect = Exception("ddb boom")
        body = {"stagingKey": "uploads/staging/u1/file.pdf", "fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents",
            body=body,
        ))
        # Update failure swallowed → still 201
        assert resp["statusCode"] == 201

    def test_filename_alias(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        """Body field 'filename' (lowercase) is accepted as alias."""
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        body = {"stagingKey": "uploads/staging/u1/x.pdf", "filename": "x.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents",
            body=body,
        ))
        assert resp["statusCode"] == 201


# ---------------------------------------------------------------------------
# Delete Document extra paths
# ---------------------------------------------------------------------------


class TestDeleteDocExtra:
    def test_delete_object_failure_returns_500(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_s3.delete_object.side_effect = Exception("s3 boom")
        body = {"fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 500

    def test_update_item_failure_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_kb_table.update_item.side_effect = Exception("ddb boom")
        body = {"fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 200

    def test_reingestion_failure_swallowed(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_bedrock.start_ingestion_job.side_effect = Exception("re-ingest fail")
        body = {"fileName": "file.pdf"}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 200

    def test_doc_key_inside_prefix_calls_delete(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_kb_s3, mock_bedrock
    ):
        """documentKey starting with the KB's s3_prefix is used directly.

        The fileName in the success response is derived from doc_key when
        only documentKey is supplied (the previous unbound-local-name bug
        was fixed in this branch).
        """
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        doc_key = f"kb/{workspace_id}/kb_abc123def456/documents/file.pdf"
        body = {"documentKey": doc_key}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/documents/delete",
            body=body,
        ))
        assert resp["statusCode"] == 200
        called = mock_kb_s3.delete_object.call_args.kwargs
        assert called["Key"] == doc_key
        body_resp = json.loads(resp["body"])
        assert body_resp["fileName"] == "file.pdf"


# ---------------------------------------------------------------------------
# Attach/Detach extra paths
# ---------------------------------------------------------------------------


class TestAttachDetachExtra:
    def test_attach_kb_marked_deleted(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(
            workspace_id, status="DELETED"
        )}
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc",
        ))
        assert resp["statusCode"] == 400

    def test_attach_knowledge_bases_field_as_list(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        """When agent's knowledge_bases is stored as a list, the code coerces to set."""
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": ["kb_other"],
            }
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        mock_ddb_client.transact_write_items.assert_called_once()

    def test_detach_kb_not_found(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_x",
        ))
        assert resp["statusCode"] == 404

    def test_detach_knowledge_bases_as_list(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": ["kb_abc123def456"],
            }
        }
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 200
        mock_ddb_client.transact_write_items.assert_called_once()

    def test_detach_transact_failure_returns_500(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": {"kb_abc123def456"},
            }
        }
        mock_ddb_client.transact_write_items.side_effect = Exception("boom")
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456",
        ))
        assert resp["statusCode"] == 500

    def test_detach_agent_other_workspace(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table, mock_agents_table
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {"agentId": "agt-1", "workspace_id": "other-ws"}
        }
        resp = _invoke(_apigw(
            "DELETE",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc",
        ))
        assert resp["statusCode"] == 404

    def test_detach_via_post_alias(
        self, workspace_id, mock_jwt, _mock_editor, mock_kb_table,
        mock_agents_table, mock_ddb_client
    ):
        """POST /detach is a registered alias for DELETE."""
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_agents_table.get_item.return_value = {
            "Item": {
                "agentId": "agt-1",
                "workspace_id": workspace_id,
                "knowledge_bases": {"kb_abc123def456"},
            }
        }
        resp = _invoke(_apigw(
            "POST",
            f"/api/workspaces/{workspace_id}/agents/agt-1/knowledge-bases/kb_abc123def456/detach",
        ))
        assert resp["statusCode"] == 200


# ---------------------------------------------------------------------------
# Ingestion status — list_ingestion_jobs returns multiple jobs missing fields
# ---------------------------------------------------------------------------


class TestIngestionStatusExtra:
    def test_jobs_with_missing_dates(
        self, workspace_id, mock_jwt, _mock_viewer, mock_kb_table, mock_bedrock
    ):
        mock_kb_table.get_item.return_value = {"Item": _kb_item(workspace_id)}
        mock_bedrock.list_ingestion_jobs.return_value = {
            "ingestionJobSummaries": [
                {
                    "ingestionJobId": "j1",
                    "status": "FAILED",
                    # no startedAt / updatedAt
                },
            ]
        }
        resp = _invoke(_apigw(
            "GET",
            f"/api/workspaces/{workspace_id}/knowledge-bases/kb_abc123def456/ingestion",
        ))
        assert resp["statusCode"] == 200
        data = json.loads(resp["body"])
        assert data["jobs"][0]["startedAt"] == ""
        assert data["jobs"][0]["updatedAt"] == ""

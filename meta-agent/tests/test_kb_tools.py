"""Tests for the Knowledge Base tool family.

Covers kb_create, kb_delete, kb_upload_document, kb_attach (attach + detach),
kb_get, kb_list, kb_check_ingestion, kb_inject helpers, kb_delete_document.

Each tool is a Strands @tool that returns a JSON string. We verify both the
happy path (DDB shape, downstream calls) and the never-raise contract for
validation / dependency failures.
"""
import json
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Module stubs ───────────────────────────────────────────────────────────
_mock_strands = sys.modules.get("strands") or types.ModuleType("strands")
if not hasattr(_mock_strands, "tool"):
    _mock_strands.tool = lambda f: f
if not hasattr(_mock_strands, "Agent"):
    _mock_strands.Agent = MagicMock
sys.modules["strands"] = _mock_strands
_mock_strands_models = sys.modules.get("strands.models") or types.ModuleType("strands.models")
if not hasattr(_mock_strands_models, "BedrockModel"):
    _mock_strands_models.BedrockModel = MagicMock
sys.modules["strands.models"] = _mock_strands_models

_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _k, _v in {
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENTS_TABLE": "agent-studio-agents",
    "KB_TABLE": "agent-studio-knowledge-bases",
    "KB_SERVICE_ROLE_ARN": "arn:aws:iam::000:role/kb-svc",
    "VECTORS_BUCKET": "studio-vectors-test",
}.items():
    if not hasattr(_mock_config, _k):
        setattr(_mock_config, _k, _v)
sys.modules["config"] = _mock_config


@pytest.fixture(autouse=True)
def _scope(monkeypatch):
    from tools import _scope
    monkeypatch.setattr(_scope, "_caller_id", "user-1", raising=False)
    monkeypatch.setattr(_scope, "_workspace_id", "ws-1", raising=False)


def _grant_agent(monkeypatch, role="editor"):
    """Set up scope so ensure_agent_in_workspace passes."""
    from tools import _scope

    workspaces = MagicMock()
    workspaces.get_item.return_value = {"Item": {"role": role}}
    monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)

    agents_table = MagicMock()
    agents_table.get_item.return_value = {
        "Item": {"agentId": "a-1", "workspace_id": "ws-1"},
    }
    monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)


def _kb_item(**overrides):
    """Helper: standard low-level DDB item shape for a KB record."""
    base = {
        "ws_id": {"S": "ws-1"},
        "kb_id": {"S": "kb-123"},
        "name": {"S": "MyKB"},
        "description": {"S": "desc"},
        "bedrock_kb_id": {"S": "BEDROCK-KB-1"},
        "data_source_id": {"S": "DS-1"},
        "s3_prefix": {"S": "kb/ws-1/kb-123/documents/"},
        "vector_index_name": {"S": "kbvec1"},
        "embedding_model": {"S": "cohere.embed-multilingual-v3"},
        "status": {"S": "ACTIVE"},
        "created_at": {"S": "2024-01-01T00:00:00+00:00"},
        "updated_at": {"S": "2024-01-01T00:00:00+00:00"},
    }
    base.update(overrides)
    return base


# ── kb_create ─────────────────────────────────────────────────────────────

class TestKbCreate:
    def test_no_workspace(self, monkeypatch):
        from tools import _scope, kb_create as mod
        monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)

        out = json.loads(mod.kb_create("My KB"))
        assert out["error"] == "no_workspace"

    def test_unsupported_region(self, monkeypatch):
        from tools import kb_create as mod
        monkeypatch.setattr(mod, "REGION", "ap-northeast-1")

        out = json.loads(mod.kb_create("My KB"))
        assert out["error"] == "region_unsupported"

    def test_name_collision(self, monkeypatch):
        from tools import kb_create as mod

        fake_ddb = MagicMock()
        fake_ddb.query.return_value = {"Items": [{"kb_id": {"S": "existing"}}]}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_create("Duplicate"))

        assert out["error"] == "name_exists"

    def test_ddb_check_failure(self, monkeypatch):
        from tools import kb_create as mod

        fake_client = MagicMock()
        fake_client.query.side_effect = Exception("ddb explode")

        with patch("boto3.client", return_value=fake_client):
            out = json.loads(mod.kb_create("New KB"))

        assert out["error"] == "ddb_check_failed"

    def test_happy_path(self, monkeypatch):
        from tools import kb_create as mod

        fake_s3v = MagicMock()
        fake_s3v.create_index.return_value = {
            "indexArn": "arn:aws:s3vectors:us-east-1:000:bucket/x/index/y",
        }

        fake_bedrock = MagicMock()
        fake_bedrock.create_knowledge_base.return_value = {
            "knowledgeBase": {
                "knowledgeBaseId": "BEDROCK-KB-1",
                "knowledgeBaseArn": "arn:bedrock-kb-1",
            }
        }
        fake_bedrock.create_data_source.return_value = {
            "dataSource": {"dataSourceId": "DS-1"},
        }

        fake_ddb = MagicMock()
        fake_ddb.query.return_value = {"Items": []}

        def client_factory(svc, **kw):
            if svc == "s3vectors":
                return fake_s3v
            if svc == "bedrock-agent":
                return fake_bedrock
            if svc == "dynamodb":
                return fake_ddb
            return MagicMock()

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_create("My KB", description="A demo"))

        assert out["status"] == "ACTIVE"
        assert out["bedrock_kb_id"] == "BEDROCK-KB-1"
        assert out["name"] == "My KB"
        # DDB write recorded with full schema
        fake_ddb.put_item.assert_called_once()

    def test_vector_index_failure(self, monkeypatch):
        from tools import kb_create as mod

        fake_s3v = MagicMock()
        fake_s3v.create_index.side_effect = Exception("vector boom")
        fake_ddb = MagicMock()
        fake_ddb.query.return_value = {"Items": []}

        def client_factory(svc, **kw):
            if svc == "s3vectors":
                return fake_s3v
            if svc == "dynamodb":
                return fake_ddb
            return MagicMock()

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_create("My KB"))

        assert out["error"] == "vector_index_failed"

    def test_kb_creation_rolls_back_index_on_failure(self, monkeypatch):
        from tools import kb_create as mod

        fake_s3v = MagicMock()
        fake_s3v.create_index.return_value = {"indexArn": "arn:idx"}

        fake_bedrock = MagicMock()
        fake_bedrock.create_knowledge_base.side_effect = Exception("bedrock boom")

        fake_ddb = MagicMock()
        fake_ddb.query.return_value = {"Items": []}

        def client_factory(svc, **kw):
            return {
                "s3vectors": fake_s3v, "bedrock-agent": fake_bedrock,
                "dynamodb": fake_ddb,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_create("My KB"))

        assert out["error"] == "create_kb_failed"
        # Rollback should have called delete_index
        fake_s3v.delete_index.assert_called_once()


# ── kb_delete ─────────────────────────────────────────────────────────────

class TestKbDelete:
    def test_no_workspace(self, monkeypatch):
        from tools import _scope, kb_delete as mod
        monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)

        out = json.loads(mod.kb_delete("kb-x"))
        assert out["error"] == "no_workspace"

    def test_kb_not_found(self, monkeypatch):
        from tools import kb_delete as mod

        fake_client = MagicMock()
        fake_client.get_item.return_value = {}

        with patch("boto3.client", return_value=fake_client):
            out = json.loads(mod.kb_delete("kb-missing"))

        assert out["error"] == "kb_not_found"

    def test_requires_confirmation_first(self, monkeypatch):
        """Without confirm=True returns the impact summary."""
        from tools import kb_delete as mod

        item = _kb_item(attached_agent_ids={"SS": ["a-1", "a-2"]})

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": item}
        fake_s3 = MagicMock()
        fake_s3.list_objects_v2.return_value = {"KeyCount": 5}

        def client_factory(svc, **kw):
            if svc == "dynamodb":
                return fake_ddb
            if svc == "s3":
                return fake_s3
            return MagicMock()

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_delete("kb-123", confirm=False))

        assert out["requires_confirmation"] is True
        assert out["impact"]["documents"] == 5
        assert out["impact"]["attached_agents"] == 2

    def test_blocks_when_ingestion_in_progress(self, monkeypatch):
        from tools import kb_delete as mod

        item = _kb_item(last_ingestion_job_id={"S": "JOB1"})
        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": item}
        fake_s3 = MagicMock()
        fake_s3.list_objects_v2.return_value = {"KeyCount": 0}
        fake_bedrock = MagicMock()
        fake_bedrock.get_ingestion_job.return_value = {
            "ingestionJob": {"status": "IN_PROGRESS"},
        }

        def client_factory(svc, **kw):
            return {
                "dynamodb": fake_ddb, "s3": fake_s3,
                "bedrock-agent": fake_bedrock,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_delete("kb-123", confirm=True))

        assert out["error"] == "ingestion_in_progress"

    def test_full_delete_paginates_s3(self, monkeypatch):
        """confirm=True deletes data source + KB + S3 objects + DDB."""
        from tools import kb_delete as mod

        item = _kb_item()
        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": item}

        # Paginator must terminate
        paginator = MagicMock()
        paginator.paginate.return_value = [
            {"Contents": [{"Key": "kb/ws-1/kb-123/documents/a.pdf"}]},
            {"Contents": []},
        ]
        fake_s3 = MagicMock()
        fake_s3.list_objects_v2.return_value = {"KeyCount": 1}
        fake_s3.get_paginator.return_value = paginator

        fake_bedrock = MagicMock()
        fake_s3v = MagicMock()

        def client_factory(svc, **kw):
            return {
                "dynamodb": fake_ddb, "s3": fake_s3,
                "bedrock-agent": fake_bedrock, "s3vectors": fake_s3v,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_delete("kb-123", confirm=True))

        assert out["deleted"] is True
        fake_bedrock.delete_data_source.assert_called_once()
        fake_bedrock.delete_knowledge_base.assert_called_once()
        fake_s3v.delete_index.assert_called_once()


# ── kb_upload_document ────────────────────────────────────────────────────

class TestKbUpload:
    def test_unsupported_format(self, monkeypatch):
        from tools import kb_upload_document as mod
        out = json.loads(mod.kb_upload_document("kb-1", "stage/x.exe", "x.exe"))
        assert out["error"] == "unsupported_format"

    def test_kb_not_found(self, monkeypatch):
        from tools import kb_upload_document as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_upload_document("kb-x", "stage/a.pdf", "a.pdf"))

        assert out["error"] == "kb_not_found"

    def test_file_too_large(self, monkeypatch):
        from tools import kb_upload_document as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}
        fake_s3 = MagicMock()
        fake_s3.head_object.return_value = {
            "ContentLength": 60 * 1024 * 1024,
        }

        def client_factory(svc, **kw):
            return {"s3": fake_s3, "dynamodb": fake_ddb}.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_upload_document("kb-1", "stage/a.pdf", "a.pdf"))

        assert out["error"] == "file_too_large"

    def test_staging_missing(self, monkeypatch):
        from tools import kb_upload_document as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}
        fake_s3 = MagicMock()
        fake_s3.head_object.side_effect = Exception("NoSuchKey")

        def client_factory(svc, **kw):
            return {"s3": fake_s3, "dynamodb": fake_ddb}.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_upload_document("kb-1", "stage/a.pdf", "a.pdf"))

        assert out["error"] == "staging_file_not_found"

    def test_happy_path(self, monkeypatch):
        from tools import kb_upload_document as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}
        fake_s3 = MagicMock()
        fake_s3.head_object.return_value = {"ContentLength": 1234}

        fake_bedrock = MagicMock()
        fake_bedrock.start_ingestion_job.return_value = {
            "ingestionJob": {"ingestionJobId": "JOB-NEW"},
        }

        def client_factory(svc, **kw):
            return {
                "s3": fake_s3, "dynamodb": fake_ddb,
                "bedrock-agent": fake_bedrock,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_upload_document(
                "kb-123", "staging/abc.pdf", "Hello World.pdf",
            ))

        assert out["status"] == "IN_PROGRESS"
        assert out["ingestion_job_id"] == "JOB-NEW"
        assert "documents/" in out["document_key"]
        assert "Hello_World.pdf" in out["document_key"]
        fake_s3.copy_object.assert_called_once()


def test_safe_filename():
    from tools.kb_upload_document import _safe_filename
    assert _safe_filename("My File!.pdf") == "My_File_.pdf"
    # Truncation to 100 chars
    assert len(_safe_filename("a" * 200)) == 100


# ── kb_attach (attach + detach) ───────────────────────────────────────────

class TestKbAttach:
    def test_no_workspace(self, monkeypatch):
        from tools import _scope, kb_attach as mod
        monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)
        out = json.loads(mod.kb_attach_to_agent("kb-1", "a-1"))
        assert out["error"] == "no_workspace"

    def test_kb_not_found(self, monkeypatch):
        from tools import kb_attach as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_attach_to_agent("kb-missing", "a-1"))

        assert out["error"] == "kb_not_found"

    def test_attach_blocks_when_agent_not_in_workspace(self, monkeypatch):
        from tools import _scope, kb_attach as mod

        # Workspace member but agent in another ws
        workspaces = MagicMock()
        workspaces.get_item.return_value = {"Item": {"role": "editor"}}
        monkeypatch.setattr(_scope, "_workspaces_table", lambda: workspaces)
        agents_table = MagicMock()
        agents_table.get_item.return_value = {
            "Item": {"agentId": "a-1", "workspace_id": "ws-OTHER"},
        }
        monkeypatch.setattr(_scope, "_agents_table", lambda: agents_table)

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_attach_to_agent("kb-123", "a-1"))

        assert "error" in out
        assert "not found in this workspace" in out["error"]

    def test_attach_happy_path(self, monkeypatch):
        from tools import kb_attach as mod
        _grant_agent(monkeypatch, role="editor")

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_attach_to_agent("kb-123", "a-1"))

        assert out["attached"] is True
        assert out["needs_redeploy"] is True
        # update_item called twice (agent + KB)
        assert fake_ddb.update_item.call_count == 2

    def test_detach_blocks_when_kb_missing(self, monkeypatch):
        from tools import kb_attach as mod
        _grant_agent(monkeypatch, role="editor")

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {}
        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_detach_from_agent("kb-x", "a-1"))
        assert out["error"] == "kb_not_found"

    def test_detach_happy_path(self, monkeypatch):
        from tools import kb_attach as mod
        _grant_agent(monkeypatch, role="editor")

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_detach_from_agent("kb-123", "a-1"))

        assert out["detached"] is True
        assert out["needs_redeploy"] is True


# ── kb_get ────────────────────────────────────────────────────────────────

class TestKbGet:
    def test_no_workspace(self, monkeypatch):
        from tools import _scope, kb_get as mod
        monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)
        out = json.loads(mod.kb_get("kb-x"))
        assert out["error"] == "no_workspace"

    def test_not_found(self, monkeypatch):
        from tools import kb_get as mod
        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_get("kb-missing"))

        assert out["error"] == "kb_not_found"

    def test_happy_path_with_documents(self, monkeypatch):
        from datetime import datetime, timezone

        from tools import kb_get as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {
            "Item": _kb_item(last_ingestion_job_id={"S": "JOB-1"}),
        }

        fake_s3 = MagicMock()
        fake_s3.list_objects_v2.return_value = {
            "Contents": [{
                "Key": "kb/ws-1/kb-123/documents/a.pdf",
                "Size": 100,
                "LastModified": datetime(2024, 1, 1, tzinfo=timezone.utc),
            }],
        }

        fake_bedrock = MagicMock()
        fake_bedrock.get_knowledge_base.return_value = {
            "knowledgeBase": {"status": "ACTIVE"},
        }
        fake_bedrock.get_ingestion_job.return_value = {
            "ingestionJob": {"status": "COMPLETE", "statistics": {}},
        }

        def client_factory(svc, **kw):
            return {
                "dynamodb": fake_ddb, "s3": fake_s3,
                "bedrock-agent": fake_bedrock,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_get("kb-123"))

        assert out["kb_id"] == "kb-123"
        assert out["doc_count"] == 1
        assert out["bedrock_status"] == "ACTIVE"
        assert out["ingestion"]["status"] == "COMPLETE"


# ── kb_list ───────────────────────────────────────────────────────────────

class TestKbList:
    def test_no_workspace(self, monkeypatch):
        from tools import _scope, kb_list as mod
        monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)
        out = json.loads(mod.kb_list())
        assert out["error"] == "no_workspace"

    def test_query_failure(self, monkeypatch):
        from tools import kb_list as mod
        fake_ddb = MagicMock()
        fake_ddb.query.side_effect = Exception("ddb explode")

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_list())

        assert out["error"] == "query_failed"

    def test_happy_path(self, monkeypatch):
        from tools import kb_list as mod

        fake_ddb = MagicMock()
        fake_ddb.query.return_value = {"Items": [_kb_item(), _kb_item(
            kb_id={"S": "kb-456"}, name={"S": "Other"},
        )]}

        fake_s3 = MagicMock()
        fake_s3.list_objects_v2.return_value = {"KeyCount": 3}

        def client_factory(svc, **kw):
            return {"dynamodb": fake_ddb, "s3": fake_s3}.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_list())

        assert isinstance(out, list)
        assert len(out) == 2
        assert out[0]["doc_count"] == 3


# ── kb_check_ingestion ────────────────────────────────────────────────────

class TestKbCheckIngestion:
    def test_no_workspace(self, monkeypatch):
        from tools import _scope, kb_check_ingestion as mod
        monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)
        out = json.loads(mod.kb_check_ingestion("kb-x"))
        assert out["error"] == "no_workspace"

    def test_kb_not_found(self, monkeypatch):
        from tools import kb_check_ingestion as mod
        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {}
        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_check_ingestion("kb-x"))
        assert out["error"] == "kb_not_found"

    def test_no_job_yet(self, monkeypatch):
        from tools import kb_check_ingestion as mod
        fake_ddb = MagicMock()
        # KB exists but has never had an ingestion
        fake_ddb.get_item.return_value = {"Item": _kb_item()}
        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_check_ingestion("kb-123"))
        assert out["error"] == "no_ingestion_job"

    def test_happy_path_complete(self, monkeypatch):
        from tools import kb_check_ingestion as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {
            "Item": _kb_item(last_ingestion_job_id={"S": "JOB-OK"}),
        }
        fake_bedrock = MagicMock()
        fake_bedrock.get_ingestion_job.return_value = {
            "ingestionJob": {
                "status": "COMPLETE",
                "statistics": {
                    "numberOfDocumentsScanned": 5,
                    "numberOfNewDocumentsIndexed": 3,
                    "numberOfModifiedDocumentsIndexed": 1,
                    "numberOfDocumentsFailed": 0,
                },
                "failureReasons": [],
                "startedAt": "now",
                "updatedAt": "now",
            },
        }

        def client_factory(svc, **kw):
            return {
                "dynamodb": fake_ddb, "bedrock-agent": fake_bedrock,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_check_ingestion("kb-123"))

        assert out["status"] == "COMPLETE"
        assert out["documents_new"] == 3
        assert out["documents_failed"] == 0
        assert out["documents_unchanged"] == 1  # 5 - 3 - 1 - 0

    def test_happy_path_with_failures(self, monkeypatch):
        from tools import kb_check_ingestion as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {
            "Item": _kb_item(last_ingestion_job_id={"S": "JOB-PARTIAL"}),
        }
        fake_bedrock = MagicMock()
        fake_bedrock.get_ingestion_job.return_value = {
            "ingestionJob": {
                "status": "COMPLETE",
                "statistics": {
                    "numberOfDocumentsScanned": 5,
                    "numberOfNewDocumentsIndexed": 3,
                    "numberOfModifiedDocumentsIndexed": 0,
                    "numberOfDocumentsFailed": 2,
                },
                "failureReasons": ["one fail"],
            },
        }

        def client_factory(svc, **kw):
            return {
                "dynamodb": fake_ddb, "bedrock-agent": fake_bedrock,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_check_ingestion("kb-123"))

        assert out["documents_failed"] == 2
        assert "失败" in out["summary"]


# ── kb_inject helpers (no @tool, but pure helpers) ────────────────────────

class TestKbInject:
    def test_build_kb_injection_empty(self):
        from tools.kb_inject import build_kb_injection
        assert build_kb_injection([]) == ""

    def test_build_kb_injection_includes_records_and_tool_code(self):
        from tools.kb_inject import build_kb_injection
        records = [{"kb_id": "kb-1", "bedrock_kb_id": "BK1", "name": "n"}]
        out = build_kb_injection(records)
        assert "BOUND_KBS" in out
        assert "kb-1" in out
        assert "REGION" in out

    def test_resolve_kb_bindings_empty(self):
        from tools.kb_inject import resolve_kb_bindings
        assert resolve_kb_bindings("ws-1", []) == []

    def test_resolve_kb_bindings_skips_missing(self):
        from tools.kb_inject import resolve_kb_bindings

        fake_ddb = MagicMock()
        fake_ddb.get_item.side_effect = [
            {"Item": {"kb_id": {"S": "kb-1"}, "bedrock_kb_id": {"S": "BK1"},
                      "name": {"S": "alpha"}}},
            {},  # second one missing
        ]

        with patch("boto3.client", return_value=fake_ddb):
            out = resolve_kb_bindings("ws-1", ["kb-1", "kb-2"])

        assert len(out) == 1
        assert out[0]["bedrock_kb_id"] == "BK1"

    def test_resolve_kb_bindings_swallows_ddb_errors(self):
        from tools.kb_inject import resolve_kb_bindings

        fake_ddb = MagicMock()
        fake_ddb.get_item.side_effect = Exception("boom")

        with patch("boto3.client", return_value=fake_ddb):
            out = resolve_kb_bindings("ws-1", ["kb-1"])

        assert out == []


# ── kb_delete_document ────────────────────────────────────────────────────

class TestKbDeleteDocument:
    def test_no_workspace(self, monkeypatch):
        from tools import _scope, kb_delete_document as mod
        monkeypatch.setattr(_scope, "_workspace_id", "", raising=False)
        out = json.loads(mod.kb_delete_document("kb-x", "k/foo.pdf"))
        assert out["error"] == "no_workspace"

    def test_kb_not_found(self, monkeypatch):
        from tools import kb_delete_document as mod
        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {}
        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_delete_document("kb-x", "k/foo.pdf"))
        assert out["error"] == "kb_not_found"

    def test_invalid_document_key(self, monkeypatch):
        """Document key must start with the KB's s3_prefix."""
        from tools import kb_delete_document as mod
        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}

        with patch("boto3.client", return_value=fake_ddb):
            out = json.loads(mod.kb_delete_document("kb-123", "wrong/prefix/foo.pdf"))

        assert out["error"] == "invalid_document_key"

    def test_happy_path(self, monkeypatch):
        from tools import kb_delete_document as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}
        fake_s3 = MagicMock()
        fake_bedrock = MagicMock()
        fake_bedrock.start_ingestion_job.return_value = {
            "ingestionJob": {"ingestionJobId": "JOB-RE"},
        }

        def client_factory(svc, **kw):
            return {
                "dynamodb": fake_ddb, "s3": fake_s3,
                "bedrock-agent": fake_bedrock,
            }.get(svc, MagicMock())

        doc_key = "kb/ws-1/kb-123/documents/abc.pdf"
        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_delete_document("kb-123", doc_key))

        assert out["deleted"] is True
        assert out["ingestion_job_id"] == "JOB-RE"
        fake_s3.delete_object.assert_called_once()

    def test_s3_delete_failure(self, monkeypatch):
        from tools import kb_delete_document as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}
        fake_s3 = MagicMock()
        fake_s3.delete_object.side_effect = Exception("AccessDenied")

        def client_factory(svc, **kw):
            return {"dynamodb": fake_ddb, "s3": fake_s3}.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_delete_document(
                "kb-123", "kb/ws-1/kb-123/documents/x.pdf",
            ))

        assert out["error"] == "delete_failed"

    def test_reingest_failure_returns_warning(self, monkeypatch):
        """If S3 delete succeeds but re-ingest fails, returns warning, not error."""
        from tools import kb_delete_document as mod

        fake_ddb = MagicMock()
        fake_ddb.get_item.return_value = {"Item": _kb_item()}
        fake_s3 = MagicMock()
        fake_bedrock = MagicMock()
        fake_bedrock.start_ingestion_job.side_effect = Exception("throttled")

        def client_factory(svc, **kw):
            return {
                "dynamodb": fake_ddb, "s3": fake_s3,
                "bedrock-agent": fake_bedrock,
            }.get(svc, MagicMock())

        with patch("boto3.client", side_effect=client_factory):
            out = json.loads(mod.kb_delete_document(
                "kb-123", "kb/ws-1/kb-123/documents/x.pdf",
            ))

        assert out["deleted"] is True
        assert out["ingestion_job_id"] is None
        assert "warning" in out

"""Tests for review_image tool — Claude Vision setting consistency check."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest


def _make_s3_response(image_bytes: bytes = b"\x89PNG_FAKE"):
    mock_body = MagicMock()
    mock_body.read.return_value = image_bytes
    return {"Body": mock_body, "ContentType": "image/png"}


def _make_bedrock_response(verdict: str = "pass", issues: list = None, summary: str = "OK"):
    content = json.dumps({"verdict": verdict, "issues": issues or [], "summary": summary})
    response_body = json.dumps({"content": [{"type": "text", "text": content}]})
    mock_body = MagicMock()
    mock_body.read.return_value = response_body.encode()
    return {"body": mock_body}


@pytest.fixture()
def mock_boto3():
    with patch("boto3.client") as mock_client_factory:
        s3_client = MagicMock()
        bedrock_client = MagicMock()

        s3_client.get_object.return_value = _make_s3_response()
        bedrock_client.invoke_model.return_value = _make_bedrock_response()

        def client_router(service, **kwargs):
            if service == "s3":
                return s3_client
            if service == "bedrock-runtime":
                return bedrock_client
            return MagicMock()

        mock_client_factory.side_effect = client_router
        yield {"s3": s3_client, "bedrock": bedrock_client}


def _exec_review_image(**kwargs):
    from tools_library.review_image import TOOL_CODE
    namespace = {"tool": lambda f: f}
    exec(TOOL_CODE, namespace)  # noqa: S102
    return namespace["review_image"](**kwargs)


class TestReviewImage:

    def test_pass_verdict(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        result = json.loads(_exec_review_image(
            s3_key="outputs/generated-images/test.png",
            review_instruction="Check if character matches setting",
            character_description="Silver hair, pink eyes, white kimono",
        ))
        assert result["verdict"] == "pass"
        assert "error" not in result

    def test_fail_verdict(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        mock_boto3["bedrock"].invoke_model.return_value = _make_bedrock_response(
            verdict="fail",
            issues=["Character is holding a sword but setting says no weapons"],
            summary="Setting inconsistency: weapon present",
        )
        result = json.loads(_exec_review_image(
            s3_key="outputs/generated-images/test.png",
            review_instruction="Check weapons",
            character_description="Never carries weapons",
        ))
        assert result["verdict"] == "fail"
        assert len(result["issues"]) == 1
        assert "weapon" in result["issues"][0].lower()

    def test_missing_s3_bucket(self, mock_boto3, monkeypatch):
        monkeypatch.delenv("AGENT_STUDIO_S3_BUCKET", raising=False)
        result = json.loads(_exec_review_image(
            s3_key="test.png",
            review_instruction="check",
        ))
        assert "error" in result

    def test_missing_required_params(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        result = json.loads(_exec_review_image(
            s3_key="",
            review_instruction="check",
        ))
        assert "error" in result

    def test_s3_read_failure(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        mock_boto3["s3"].get_object.side_effect = Exception("AccessDenied")
        result = json.loads(_exec_review_image(
            s3_key="test.png",
            review_instruction="check",
        ))
        assert "error" in result
        assert "S3" in result["error"]

    def test_vision_api_sends_image_and_instruction(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        _exec_review_image(
            s3_key="outputs/test.png",
            review_instruction="Check hair color",
            character_description="Silver-white hair with lavender tips",
        )
        call_body = json.loads(mock_boto3["bedrock"].invoke_model.call_args.kwargs["body"])
        messages = call_body["messages"]
        assert len(messages) == 1
        content = messages[0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[1]["type"] == "text"
        assert "Check hair color" in content[1]["text"]
        assert "Silver-white hair" in content[1]["text"]

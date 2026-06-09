"""Tests for create_storyboard tool — style_context and negative_prompt injection."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest


def _make_bedrock_response(image_bytes: bytes = b"\x89PNG_FAKE"):
    body_content = json.dumps({"images": [base64.b64encode(image_bytes).decode()]})
    mock_body = MagicMock()
    mock_body.read.return_value = body_content.encode()
    return {"body": mock_body}


@pytest.fixture()
def mock_boto3():
    with patch("boto3.client") as mock_client_factory:
        bedrock_client = MagicMock()
        s3_client = MagicMock()

        bedrock_client.invoke_model.return_value = _make_bedrock_response()
        s3_client.generate_presigned_url.return_value = "https://fake-url/frame.png"

        def client_router(service, **kwargs):
            if service == "bedrock-runtime":
                return bedrock_client
            if service == "s3":
                return s3_client
            return MagicMock()

        mock_client_factory.side_effect = client_router
        yield {
            "factory": mock_client_factory,
            "bedrock": bedrock_client,
            "s3": s3_client,
        }


def _exec_storyboard(**kwargs):
    from tools_library.storyboard import TOOL_CODE

    namespace = {"tool": lambda f: f}
    exec(TOOL_CODE, namespace)  # noqa: S102
    return namespace["create_storyboard"](**kwargs)


class TestStoryboardStyleContext:
    """Verify style_context override logic in storyboard."""

    def test_no_style_context_uses_style_prefix(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        _exec_storyboard(
            script="frame one\nframe two",
            num_frames=2,
            style="anime",
        )

        calls = mock_boto3["bedrock"].invoke_model.call_args_list
        body_0 = json.loads(calls[0].kwargs["body"])
        assert body_0["prompt"].startswith("anime style, cel-shaded, vibrant colors, ")
        assert "frame 1 of 2" in body_0["prompt"]

    def test_style_context_overrides_prefix(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        ctx = "Japanese dark fantasy, twilight palette"
        _exec_storyboard(
            script="scene one\nscene two",
            num_frames=2,
            style="anime",
            style_context=ctx,
        )

        calls = mock_boto3["bedrock"].invoke_model.call_args_list
        body_0 = json.loads(calls[0].kwargs["body"])
        assert body_0["prompt"].startswith("frame 1 of 2")
        assert body_0["prompt"].endswith(ctx)
        assert "anime style" not in body_0["prompt"]

    def test_whitespace_style_context_falls_through(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        _exec_storyboard(
            script="a\nb",
            num_frames=2,
            style="watercolor",
            style_context="   ",
        )

        body_0 = json.loads(mock_boto3["bedrock"].invoke_model.call_args_list[0].kwargs["body"])
        assert body_0["prompt"].startswith("watercolor painting, soft artistic style, ")


class TestStoryboardNegativePrompt:
    """Verify negative_prompt parameterization."""

    def test_custom_negative_prompt(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        neg = "neon colors, modern buildings, smartphones"
        _exec_storyboard(
            script="frame a\nframe b",
            num_frames=2,
            negative_prompt=neg,
        )

        calls = mock_boto3["bedrock"].invoke_model.call_args_list
        for call in calls:
            body = json.loads(call.kwargs["body"])
            assert body["negative_prompt"] == neg

    def test_default_negative_prompt(self, mock_boto3, monkeypatch):
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        _exec_storyboard(script="x\ny", num_frames=2)

        body_0 = json.loads(mock_boto3["bedrock"].invoke_model.call_args_list[0].kwargs["body"])
        assert "watermark" in body_0["negative_prompt"]
        assert "deformed" in body_0["negative_prompt"]

    def test_negative_prompt_with_style_context(self, mock_boto3, monkeypatch):
        """Both style_context and negative_prompt can be used together."""
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        ctx = "ink-wash painting, twilight"
        neg = "chibi style, bright colors"
        _exec_storyboard(
            script="a\nb",
            num_frames=2,
            style_context=ctx,
            negative_prompt=neg,
        )

        body_0 = json.loads(mock_boto3["bedrock"].invoke_model.call_args_list[0].kwargs["body"])
        assert body_0["prompt"].endswith(ctx)
        assert body_0["negative_prompt"] == neg

"""Tests for generate_image tool — style_context injection logic."""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest


def _make_bedrock_response(image_bytes: bytes = b"\x89PNG_FAKE"):
    """Build a mock Bedrock InvokeModel response."""
    body_content = json.dumps({"images": [base64.b64encode(image_bytes).decode()]})
    mock_body = MagicMock()
    mock_body.read.return_value = body_content.encode()
    return {"body": mock_body}


@pytest.fixture()
def mock_boto3():
    """Mock boto3 to intercept Bedrock and S3 calls."""
    with patch("boto3.client") as mock_client_factory:
        bedrock_client = MagicMock()
        s3_client = MagicMock()

        bedrock_client.invoke_model.return_value = _make_bedrock_response()
        s3_client.generate_presigned_url.return_value = "https://fake-url/image.png"

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


def _exec_generate_image(**kwargs):
    """Execute the generate_image TOOL_CODE with given arguments."""
    from tools_library.generate_image import TOOL_CODE

    namespace = {"tool": lambda f: f}  # stub @tool decorator
    exec(TOOL_CODE, namespace)  # noqa: S102
    return namespace["generate_image"](**kwargs)


class TestStyleContextInjection:
    """Verify style_context parameter behavior."""

    def test_no_style_context_uses_style_prefix(self, mock_boto3, monkeypatch):
        """When style_context is empty, the style prefix is prepended (backward compat)."""
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        result = json.loads(
            _exec_generate_image(prompt="a warrior standing in twilight", style="anime")
        )

        call_body = json.loads(
            mock_boto3["bedrock"].invoke_model.call_args[1]["body"]
            if "body" in (mock_boto3["bedrock"].invoke_model.call_args[1] or {})
            else mock_boto3["bedrock"].invoke_model.call_args.kwargs["body"]
        )
        assert call_body["prompt"].startswith("anime style, cel-shaded, vibrant colors, ")
        assert "a warrior standing in twilight" in call_body["prompt"]
        assert "error" not in result

    def test_style_context_overrides_style_prefix(self, mock_boto3, monkeypatch):
        """When style_context is provided, it replaces the style prefix entirely."""
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        ctx = "Japanese dark fantasy, twilight palette, ink-wash texture"
        result = json.loads(
            _exec_generate_image(
                prompt="a warrior standing in twilight",
                style="anime",
                style_context=ctx,
            )
        )

        call_body = json.loads(mock_boto3["bedrock"].invoke_model.call_args.kwargs["body"])
        assert call_body["prompt"].startswith(ctx)
        assert "anime style" not in call_body["prompt"]
        assert "a warrior standing in twilight" in call_body["prompt"]
        assert "error" not in result

    def test_style_context_whitespace_only_falls_through(self, mock_boto3, monkeypatch):
        """Whitespace-only style_context is treated as empty (uses style prefix)."""
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        _exec_generate_image(
            prompt="a castle at dusk",
            style="watercolor",
            style_context="   ",
        )

        call_body = json.loads(mock_boto3["bedrock"].invoke_model.call_args.kwargs["body"])
        assert call_body["prompt"].startswith("watercolor painting, soft edges, artistic, ")

    def test_style_context_strips_whitespace(self, mock_boto3, monkeypatch):
        """Leading/trailing whitespace in style_context is stripped before prepending."""
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        ctx = "  twilight palette, muted tones  "
        _exec_generate_image(prompt="a tree", style_context=ctx)

        call_body = json.loads(mock_boto3["bedrock"].invoke_model.call_args.kwargs["body"])
        assert call_body["prompt"].startswith("twilight palette, muted tones, a tree")

    def test_negative_prompt_unaffected_by_style_context(self, mock_boto3, monkeypatch):
        """negative_prompt works the same regardless of style_context presence."""
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        neg = "neon colors, modern buildings"
        _exec_generate_image(
            prompt="a shrine",
            style_context="ink-wash painting",
            negative_prompt=neg,
        )

        call_body = json.loads(mock_boto3["bedrock"].invoke_model.call_args.kwargs["body"])
        assert call_body["negative_prompt"] == neg

    def test_default_negative_prompt_when_empty(self, mock_boto3, monkeypatch):
        """Default negative prompt is applied when negative_prompt is empty."""
        monkeypatch.setenv("AGENT_STUDIO_S3_BUCKET", "test-bucket")
        _exec_generate_image(prompt="a forest", style_context="dark fantasy")

        call_body = json.loads(mock_boto3["bedrock"].invoke_model.call_args.kwargs["body"])
        assert "watermark" in call_body["negative_prompt"]
        assert "blurry" in call_body["negative_prompt"]

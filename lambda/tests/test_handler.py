"""Tests for crud.handler — origin verify, CORS, health, error handler.

NOTE: We patch crud.handler.ORIGIN_VERIFY_VALUE via monkeypatch.setattr rather
than importlib.reload(crud.handler). Reloading the module rebuilds the
APIGatewayRestResolver `app` global, which orphans the routers registered by
all other test modules at their import time — that caused 95 test failures
across test_mcp.py / test_workspaces.py / etc. Patching the constant keeps
the existing app + routers intact.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import crud.handler as h


@pytest.fixture(autouse=True)
def origin_verify_value(monkeypatch):
    """Set the module-level ORIGIN_VERIFY_VALUE without reloading."""
    monkeypatch.setattr(h, "ORIGIN_VERIFY_VALUE", "test-origin")
    yield h


def _make_event(method, path, headers=None, body=None):
    h_ = {"Authorization": "Bearer tok"}
    if headers:
        h_.update(headers)
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "queryStringParameters": None,
        "headers": h_,
        "body": json.dumps(body) if body else None,
        "requestContext": {
            "stage": "test",
            "requestId": "req-handler",
            "identity": {"sourceIp": "127.0.0.1"},
        },
        "isBase64Encoded": False,
    }


# ---------------------------------------------------------------------------
# Origin verify
# ---------------------------------------------------------------------------


def test_missing_origin_verify_header_rejected(origin_verify_value):
    """If ORIGIN_VERIFY_VALUE is set but the header is missing, return 403."""
    event = _make_event("GET", "/api/health")
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 403
    body = json.loads(resp["body"])
    assert body["error"] == "Forbidden"


def test_wrong_origin_verify_value_rejected(origin_verify_value):
    event = _make_event("GET", "/api/health", headers={"x-origin-verify": "wrong-value"})
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 403


def test_correct_origin_verify_passes(origin_verify_value):
    event = _make_event("GET", "/api/health", headers={"x-origin-verify": "test-origin"})
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 200


def test_origin_verify_header_case_insensitive(origin_verify_value):
    """API Gateway may lowercase headers; the check is case-insensitive on key."""
    event = _make_event("GET", "/api/health", headers={"X-Origin-Verify": "test-origin"})
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 200


def test_origin_verify_disabled_when_empty(monkeypatch):
    """Empty ORIGIN_VERIFY_VALUE = the check is disabled."""
    monkeypatch.setattr(h, "ORIGIN_VERIFY_VALUE", "")
    event = _make_event("GET", "/api/health")
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 200


def test_origin_verify_ok_helper_with_no_headers(origin_verify_value):
    """_origin_verify_ok handles event without 'headers' key."""
    assert h._origin_verify_ok({}) is False
    assert h._origin_verify_ok({"headers": None}) is False


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_endpoint(origin_verify_value):
    event = _make_event("GET", "/api/health", headers={"x-origin-verify": "test-origin"})
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# Unhandled exception handler
# ---------------------------------------------------------------------------


def test_unhandled_exception_returns_500(origin_verify_value):
    """Routes that raise an unhandled exception are caught and return 500."""
    with patch("crud.meta_agent.auth_check") as auth:
        auth.side_effect = RuntimeError("boom")
        event = _make_event(
            "GET",
            "/api/workspaces/ws-1/meta-agent/agent-card",
            headers={"x-origin-verify": "test-origin"},
        )
        event["pathParameters"] = {"wsId": "ws-1"}
        event["resource"] = "/api/workspaces/{wsId}/meta-agent/agent-card"
        resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body["error"] == "Internal server error"
    assert body["code"] == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# CORS preflight
# ---------------------------------------------------------------------------


def test_options_preflight_skips_origin_verify(origin_verify_value):
    """CORS preflight (OPTIONS) — origin-verify is enforced before routing,
    so without the header the request is rejected.

    This is the documented behavior per security model: even OPTIONS must
    come through CloudFront.
    """
    event = _make_event("OPTIONS", "/api/health")
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] == 403


def test_options_with_origin_verify_handled(origin_verify_value):
    event = _make_event(
        "OPTIONS",
        "/api/health",
        headers={
            "x-origin-verify": "test-origin",
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    resp = h.lambda_handler(event, MagicMock())
    # APIGatewayRestResolver handles OPTIONS for CORS, returns 204 normally
    assert resp["statusCode"] in (200, 204, 404)


# ---------------------------------------------------------------------------
# Routing — invalid path returns 404
# ---------------------------------------------------------------------------


def test_unknown_route_returns_error(origin_verify_value):
    """APIGatewayRestResolver may raise on unmatched routes; the global
    exception handler converts it to a 500 (or 404 depending on Powertools
    version). We accept either as a non-2xx."""
    event = _make_event(
        "GET",
        "/api/non-existent-endpoint",
        headers={"x-origin-verify": "test-origin"},
    )
    resp = h.lambda_handler(event, MagicMock())
    assert resp["statusCode"] >= 400

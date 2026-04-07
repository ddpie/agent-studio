"""Invoke Lambda — Function URL handler.

Placeholder for Phase 0. Actual SSE streaming will be implemented in Plan 3
using Lambda Web Adapter + FastAPI (Python Lambda doesn't natively support
the response_stream signature for RESPONSE_STREAM mode).
"""
import json
import re


def handler(event, context):
    """Lambda Function URL handler.

    Returns standard JSON responses for now. SSE streaming via Lambda Web
    Adapter will be added in Plan 3.
    """
    headers = event.get("headers", {})
    path = event.get("requestContext", {}).get("http", {}).get("path", "")

    # Health check
    if path == "/invoke/health":
        return {
            "statusCode": 200,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"status": "ok"}),
        }

    # Auth check
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Missing token"}),
        }

    token = auth_header[7:]
    try:
        from shared.auth import verify_jwt
        claims = verify_jwt(token)
        user_id = claims["sub"]
    except Exception:
        return {
            "statusCode": 401,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Authentication failed"}),
        }

    # Validate path
    ws_match = re.match(
        r"^/invoke/workspaces/([a-zA-Z0-9-]+)/(meta-agent|agents/([a-zA-Z0-9-]+))$", path
    )
    pub_match = re.match(r"^/invoke/public/agents/([a-zA-Z0-9-]+)$", path)

    if not ws_match and not pub_match:
        return {
            "statusCode": 404,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Not found"}),
        }

    # Placeholder response
    data = json.dumps({"status": "ready", "user": user_id})
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/event-stream"},
        "body": f": keepalive\n\ndata: {data}\n\n",
    }

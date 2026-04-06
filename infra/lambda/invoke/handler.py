"""Invoke Lambda — Function URL handler with SSE streaming.

Python Lambda RESPONSE_STREAM 使用 awslambdaric 的 StreamingBody。
非流式响应（health、error）直接 return dict。
"""
import json
import re


def handler(event, response_stream):
    """Lambda Function URL streaming handler.

    For RESPONSE_STREAM mode, non-streaming responses should write to
    response_stream and close. Actual SSE streaming will be implemented in Plan 3.
    """
    headers = event.get("headers", {})
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("requestContext", {}).get("http", {}).get("path", "")

    # Health check
    if path == "/invoke/health":
        response_stream.write(json.dumps({"status": "ok"}).encode())
        response_stream.close()
        return

    # Auth check
    auth_header = headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        response_stream.write(json.dumps({"error": "Missing token"}).encode())
        response_stream.close()
        return

    token = auth_header[7:]
    try:
        from shared.auth import verify_jwt
        claims = verify_jwt(token)
        user_id = claims["sub"]
    except Exception as e:
        # 不暴露内部错误细节给客户端
        response_stream.write(json.dumps({"error": "Authentication failed"}).encode())
        response_stream.close()
        return

    # Validate path
    ws_match = re.match(
        r"^/invoke/workspaces/([a-zA-Z0-9-]+)/(meta-agent|agents/([a-zA-Z0-9-]+))$", path
    )
    pub_match = re.match(r"^/invoke/public/agents/([a-zA-Z0-9-]+)$", path)

    if not ws_match and not pub_match:
        response_stream.write(json.dumps({"error": "Not found"}).encode())
        response_stream.close()
        return

    # Placeholder: send keepalive + test SSE event
    # Real implementation (Plan 3) should send `: keepalive\n\n` every 30s
    # to prevent CloudFront 60s origin read timeout from closing the connection
    response_stream.write(b": keepalive\n\n")
    data = json.dumps({"status": "ready", "user": user_id})
    response_stream.write(f"data: {data}\n\n".encode())
    response_stream.close()

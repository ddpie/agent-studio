"""Map ACP notifications to the existing Agent Studio SSE event format.

The frontend (lib/agentcore-client.ts) parses these event shapes today:
- raw text          → appended to assistant message
- {"__keepalive": true}
- {"__tool": "start",  "name": <tool_name>}
- {"__tool": "result", "name": <tool_name>, "input": <b64>, "output": <b64>}
- {"__tool": "end",    "name": <tool_name>}

We preserve this protocol so the frontend needs no changes. Input/output are
base64-encoded for the same reason main.py:619/628 does it (nested JSON safety).

Also preserves the 30s keepalive pump from main.py:549-575 to survive the
CloudFront 60s origin idle timeout.

Not implemented yet — skeleton only.
"""


async def acp_to_sse(notifications):
    """Async generator: consume ACP notifications, yield SSE-ready strings.

    Args:
        notifications: async iterable of ACP notification dicts from KiroACPClient.

    Yields:
        str — JSON-encoded SSE events matching the existing frontend protocol.
    """
    raise NotImplementedError

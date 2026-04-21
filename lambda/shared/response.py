"""Unified API response helpers."""
import json
from decimal import Decimal

from aws_lambda_powertools.event_handler import Response


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def success(data: dict | list, status_code: int = 200) -> Response:
    return Response(
        status_code=status_code,
        content_type="application/json",
        body=json.dumps(data, cls=_DecimalEncoder),
    )


def paginated(items: list, next_cursor: str | None = None) -> Response:
    body = {"items": items}
    if next_cursor:
        body["nextCursor"] = next_cursor
    return success(body)


def error(message: str, code: str, status_code: int) -> Response:
    return Response(
        status_code=status_code,
        content_type="application/json",
        body=json.dumps({"error": message, "code": code}),
    )


def forbidden() -> Response:
    return error("Forbidden", "PERMISSION_DENIED", 403)


def not_found() -> Response:
    return error("Not found", "NOT_FOUND", 404)


def bad_request(message: str) -> Response:
    return error(message, "VALIDATION_ERROR", 400)


def version_conflict(message: str) -> Response:
    return error(message, "VALIDATION_ERROR", 409)


def internal_error(message: str = "Internal server error") -> Response:
    return error(message, "INTERNAL_ERROR", 500)

"""Unified API response helpers."""
import json


def success(data: dict | list, status_code: int = 200) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(data),
    }


def paginated(items: list, next_cursor: str | None = None) -> dict:
    body = {"items": items}
    if next_cursor:
        body["nextCursor"] = next_cursor
    return success(body)


def error(message: str, code: str, status_code: int) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message, "code": code}),
    }


def forbidden() -> dict:
    return error("Forbidden", "PERMISSION_DENIED", 403)


def not_found() -> dict:
    return error("Not found", "NOT_FOUND", 404)


def bad_request(message: str) -> dict:
    return error(message, "VALIDATION_ERROR", 400)


def version_conflict(message: str) -> dict:
    return error(message, "VALIDATION_ERROR", 409)


def internal_error() -> dict:
    return error("Internal server error", "INTERNAL_ERROR", 500)

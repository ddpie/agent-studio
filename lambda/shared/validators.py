"""Request validation helpers."""
import re

ID_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")
MAX_LIMIT = 100
DEFAULT_LIMIT = 20


def validate_id(value: str, name: str = "id") -> str | None:
    """Returns error message if invalid, None if valid."""
    if not value or not ID_PATTERN.match(value):
        return f"Invalid {name}: must match [a-zA-Z0-9-]+"
    if len(value) > 128:
        return f"Invalid {name}: must be 128 characters or less"
    return None


def parse_pagination(query_params: dict) -> tuple[int, str | None]:
    """Parse limit and cursor from query params. Returns (limit, cursor)."""
    try:
        limit = int(query_params.get("limit", DEFAULT_LIMIT))
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))
    cursor = query_params.get("cursor")
    return limit, cursor


SAFE_PATH_RE = re.compile(r'^[^\x00-\x1f\x7f\\]+$')


def validate_path(path: str) -> str | None:
    """Returns error message if invalid, None if valid.
    Allows Unicode (e.g. Chinese filenames). Blocks control chars, backslashes, .., //."""
    if not path or not SAFE_PATH_RE.match(path) or '//' in path:
        return "Invalid path"
    if '..' in path.split('/'):
        return "Invalid path"
    if len(path) > 512:
        return "Path too long"
    return None


SECRET_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


def validate_secret_key(value: str, name: str = "key") -> str | None:
    """Returns error message if invalid secret key, None if valid."""
    if not value or not SECRET_KEY_PATTERN.match(value):
        return f"Invalid {name}: must match [a-zA-Z0-9_-]+"
    if len(value) > 128:
        return f"Invalid {name}: must be 128 characters or less"
    return None

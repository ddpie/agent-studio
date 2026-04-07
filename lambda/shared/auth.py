"""JWT verification + workspace membership check."""
import json
import urllib.request
import boto3
from jose import jwt, JWTError
from shared.config import COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, REGION, WORKSPACES_TABLE

ROLE_LEVEL = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}

_jwks_cache = None
_ws_table = None


def _get_ws_table():
    global _ws_table
    if _ws_table is None:
        _ws_table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _ws_table


def _fetch_jwks():
    global _jwks_cache
    url = f"https://cognito-idp.{REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
    with urllib.request.urlopen(url, timeout=5) as resp:
        _jwks_cache = json.loads(resp.read())
    return _jwks_cache


def _get_signing_key(token: str) -> dict:
    """Extract the correct JWK by matching kid in token header."""
    jwks = _jwks_cache or _fetch_jwks()
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return key
    jwks = _fetch_jwks()
    for key in jwks.get("keys", []):
        if key["kid"] == kid:
            return key
    raise JWTError("Signing key not found in JWKS")


def verify_jwt(token: str) -> dict:
    """Verify Cognito JWT. Returns claims dict. Raises ValueError on failure."""
    try:
        key = _get_signing_key(token)
        claims = jwt.decode(
            token, key, algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID,
            issuer=f"https://cognito-idp.{REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}",
            options={"verify_at_hash": False},
        )
        if claims.get("token_use") != "id":
            raise ValueError("Not an id token")
        return claims
    except JWTError:
        raise ValueError("Authentication failed")


def get_membership(workspace_id: str, user_id: str) -> dict | None:
    resp = _get_ws_table().get_item(
        Key={"workspaceId": workspace_id, "sk": f"MEMBER#{user_id}"},
        ConsistentRead=True,
    )
    return resp.get("Item")


def check_permission(member: dict | None, min_role: str) -> bool:
    if not member:
        return False
    return ROLE_LEVEL.get(member.get("role", ""), -1) >= ROLE_LEVEL.get(min_role, 99)

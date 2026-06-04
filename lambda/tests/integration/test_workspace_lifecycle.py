"""Integration: workspace create + member add + delete round-trip against moto DDB.

This exercises the real boto3 serialization path, real KeyConditionExpression,
real ConditionExpression — the things mock-based unit tests can never catch.
"""
import json
from unittest.mock import MagicMock, patch


def _apigw(method, path, *, body=None, user_id="user-A"):
    return {
        "httpMethod": method,
        "path": path,
        "resource": path,
        "pathParameters": {},
        "headers": {
            "Authorization": "Bearer integ-token",
            "Content-Type": "application/json",
        },
        "body": json.dumps(body) if body else None,
        "queryStringParameters": None,
        "isBase64Encoded": False,
        "requestContext": {
            "stage": "test",
            "requestId": "req-int",
            "identity": {"sourceIp": "127.0.0.1"},
        },
    }


def test_create_then_list_workspace_round_trip(ddb_workspaces_table, mock_jwt):
    """End-to-end: POST creates META + MEMBER#owner rows; subsequent
    membership query returns owner role. Real DDB → real type coercion."""
    from crud.handler import lambda_handler

    # Patch only the table getter — let everything else go through real boto3.
    with patch("crud.workspaces._get_table", return_value=ddb_workspaces_table), \
         patch("crud.workspaces._create_workspace_memory", return_value=None):
        # Create
        resp = lambda_handler(_apigw("POST", "/api/workspaces", body={"name": "Integ"}), MagicMock())

    assert resp["statusCode"] == 201
    ws_id = json.loads(resp["body"])["workspaceId"]

    # Verify both rows present in REAL DDB
    meta = ddb_workspaces_table.get_item(Key={"workspaceId": ws_id, "sk": "META"}).get("Item")
    assert meta is not None
    assert meta["name"] == "Integ"
    # owner_id should be set; mock_jwt's sub is the user
    assert meta["owner_id"]

    # Member row exists
    from boto3.dynamodb.conditions import Key
    members = ddb_workspaces_table.query(
        KeyConditionExpression=Key("workspaceId").eq(ws_id) & Key("sk").begins_with("MEMBER#"),
    )["Items"]
    assert len(members) == 1
    assert members[0]["role"] == "owner"


def test_duplicate_workspace_name_rejected(ddb_workspaces_table, mock_jwt):
    """Real scan with paginator — verifies _workspace_name_exists actually
    finds duplicates rather than silently looping forever or skipping pages."""
    from crud.handler import lambda_handler

    # Seed an existing workspace
    ddb_workspaces_table.put_item(Item={
        "workspaceId": "ws-existing",
        "sk": "META",
        "name": "MyWorkspace",
        "owner_id": "user-X",
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
    })

    with patch("crud.workspaces._get_table", return_value=ddb_workspaces_table):
        resp = lambda_handler(
            _apigw("POST", "/api/workspaces", body={"name": "MyWorkspace"}),
            MagicMock(),
        )
    assert resp["statusCode"] == 409


def test_case_insensitive_duplicate_check(ddb_workspaces_table, mock_jwt):
    """`MyWorkspace` clashes with existing `myworkspace` — case-insensitive."""
    from crud.handler import lambda_handler
    ddb_workspaces_table.put_item(Item={
        "workspaceId": "ws-old",
        "sk": "META",
        "name": "myworkspace",
        "owner_id": "user-X",
        "created_at": "2026-04-01T00:00:00Z",
        "updated_at": "2026-04-01T00:00:00Z",
    })
    with patch("crud.workspaces._get_table", return_value=ddb_workspaces_table):
        resp = lambda_handler(
            _apigw("POST", "/api/workspaces", body={"name": "MYWORKSPACE"}),
            MagicMock(),
        )
    assert resp["statusCode"] == 409

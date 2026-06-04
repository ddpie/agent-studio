"""Integration-test fixtures backed by moto (in-memory AWS).

Unit tests in lambda/tests/test_*.py mock boto3 responses by hand. Integration
tests here use moto so the test goes through real boto3 client code paths
(serialization, error shapes, eventual consistency). This catches the class of
bugs unit tests miss — e.g. wrong KeyConditionExpression syntax, type coercion
in DDB Items, S3 key normalization.

To run:
    cd lambda
    PYTHONPATH=. pytest tests/integration/ -m integration

Marker is also auto-applied via the marker selection in pyproject.toml so
running the unit suite (`pytest tests/`) still skips integration by default.
"""

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def aws_credentials(monkeypatch):
    """Set dummy AWS credentials for moto. Scoped to the test (via monkeypatch)
    so unit tests in sibling directories — which share the parent conftest —
    are NOT polluted with these env vars at collection time.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    return {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_DEFAULT_REGION": "us-east-1",
    }


@pytest.fixture
def ddb_workspaces_table(aws_credentials):
    """Real (moto) DynamoDB table matching production schema for workspaces."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="agent-studio-workspaces",
            KeySchema=[
                {"AttributeName": "workspaceId", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "workspaceId", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName="agent-studio-workspaces")
        yield boto3.resource("dynamodb", region_name="us-east-1").Table("agent-studio-workspaces")


@pytest.fixture
def ddb_agents_table(aws_credentials):
    """Real (moto) DynamoDB table matching production schema for agents."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("dynamodb", region_name="us-east-1")
        client.create_table(
            TableName="agent-studio-agents",
            KeySchema=[{"AttributeName": "agentId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "agentId", "AttributeType": "S"},
                {"AttributeName": "workspace_id", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
                {"AttributeName": "visibility", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "workspace-index",
                    "KeySchema": [
                        {"AttributeName": "workspace_id", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "public-index",
                    "KeySchema": [
                        {"AttributeName": "visibility", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        client.get_waiter("table_exists").wait(TableName="agent-studio-agents")
        yield boto3.resource("dynamodb", region_name="us-east-1").Table("agent-studio-agents")


@pytest.fixture
def s3_assets_bucket(aws_credentials):
    """Real (moto) S3 bucket matching production layout."""
    import boto3
    from moto import mock_aws

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="test-assets")
        yield "test-assets"

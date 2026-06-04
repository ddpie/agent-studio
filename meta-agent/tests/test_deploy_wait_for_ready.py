"""Regression test: wait_for_ready polls get_agent_runtime until READY.

Status enum from boto3 service model (verified live on us-east-1 2026-04-18):
CREATING | CREATE_FAILED | UPDATING | UPDATE_FAILED | READY | DELETING.
"""

import sys
import types
from unittest.mock import MagicMock, patch

# Ensure the mocked config module exposes every attribute deploy.py imports.
_mock_config = sys.modules.get("config") or types.ModuleType("config")
for _name, _value in {
    "MODEL_ID": "mock-model",
    "REGION": "us-east-1",
    "ACCOUNT_ID": "000000000000",
    "S3_BUCKET": "test-bucket",
    "AGENT_ROLE_ARN": "arn:aws:iam::000000000000:role/test-role",
    "BASE_DEPLOYMENT_KEY": "base/deployment.zip",
}.items():
    if not hasattr(_mock_config, _name):
        setattr(_mock_config, _name, _value)
sys.modules["config"] = _mock_config


@patch("deploy.boto3.client")
def test_wait_for_ready_returns_on_ready(mock_client):
    control = MagicMock()
    control.get_agent_runtime.side_effect = [
        {"status": "CREATING"},
        {"status": "READY"},
    ]
    mock_client.return_value = control

    from deploy import wait_for_ready

    with patch("deploy.time.sleep"):
        result = wait_for_ready("test-runtime-id", timeout=30)
    assert result == "READY"
    assert control.get_agent_runtime.call_count == 2


@patch("deploy.boto3.client")
def test_wait_for_ready_returns_on_create_failed(mock_client):
    control = MagicMock()
    control.get_agent_runtime.return_value = {"status": "CREATE_FAILED"}
    mock_client.return_value = control

    from deploy import wait_for_ready

    with patch("deploy.time.sleep"):
        assert wait_for_ready("test-runtime-id", timeout=30) == "CREATE_FAILED"


@patch("deploy.boto3.client")
def test_wait_for_ready_returns_on_update_failed(mock_client):
    control = MagicMock()
    control.get_agent_runtime.return_value = {"status": "UPDATE_FAILED"}
    mock_client.return_value = control

    from deploy import wait_for_ready

    with patch("deploy.time.sleep"):
        assert wait_for_ready("test-runtime-id", timeout=30) == "UPDATE_FAILED"

"""Regression test: wait_for_ready expects 'ACTIVE' not 'READY'."""
import sys
import types
from unittest.mock import MagicMock, patch

# Ensure the mocked config module exposes every attribute deploy.py imports.
# Other test modules may have already installed a partial mock in sys.modules,
# so set attributes via setattr rather than replacing the module outright.
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
def test_wait_for_ready_returns_on_active(mock_client):
    """get_agent_runtime returns ACTIVE (not READY) per AgentCore API."""
    control = MagicMock()
    control.get_agent_runtime.side_effect = [
        {"status": "CREATING"},
        {"status": "ACTIVE"},
    ]
    mock_client.return_value = control

    from deploy import wait_for_ready
    with patch("deploy.time.sleep"):
        result = wait_for_ready("test-runtime-id", timeout=30)
    assert result == "ACTIVE"
    assert control.get_agent_runtime.call_count == 2


@patch("deploy.boto3.client")
def test_wait_for_ready_returns_on_failed(mock_client):
    control = MagicMock()
    control.get_agent_runtime.return_value = {"status": "FAILED"}
    mock_client.return_value = control

    from deploy import wait_for_ready
    with patch("deploy.time.sleep"):
        assert wait_for_ready("test-runtime-id", timeout=30) == "FAILED"

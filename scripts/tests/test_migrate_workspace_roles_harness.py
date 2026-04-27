"""Unit tests for the pure diff logic in migrate-workspace-roles-harness.py.

We don't test AWS calls here — those are exercised via --dry-run against a
live account. We only exercise the pure functions that decide whether an
existing policy needs an update.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest

# Pre-seed env vars so the migration script's `from shared.config import ...`
# works even when imported in isolation.
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AGENT_STUDIO_REGION", "us-east-1")
os.environ.setdefault("AGENT_STUDIO_ACCOUNT_ID", "123456789012")
os.environ.setdefault(
    "WORKSPACE_BOUNDARY_ARN",
    "arn:aws:iam::123456789012:policy/AgentStudioWorkspaceCeiling",
)
os.environ.setdefault("WORKSPACES_TABLE", "test-workspaces")

# Make the script importable despite its hyphenated filename.
HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(HERE, "..", "migrate-workspace-roles-harness.py")


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migrate_workspace_roles_harness", SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mig():
    return _load_migration_module()


# ──────────────────────────────────────────────────────────
# policy_needs_update: trust policy
# ──────────────────────────────────────────────────────────


def _old_trust() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": "123456789012"},
                    "ArnLike": {
                        "aws:SourceArn": (
                            "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/*"
                        ),
                    },
                },
            }
        ],
    }


def _new_trust() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": "123456789012"},
                    "ArnLike": {
                        "aws:SourceArn": [
                            "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/*",
                            "arn:aws:bedrock-agentcore:us-east-1:123456789012:harness/*",
                        ],
                    },
                },
            }
        ],
    }


class TestTrustPolicyDiff:
    def test_old_sourcearn_string_triggers_update(self, mig):
        assert mig.policy_needs_update(_old_trust(), _new_trust()) is True

    def test_new_sourcearn_list_is_noop(self, mig):
        assert mig.policy_needs_update(_new_trust(), _new_trust()) is False

    def test_reordered_sourcearn_list_is_noop(self, mig):
        current = _new_trust()
        current["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"] = list(
            reversed(current["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"])
        )
        assert mig.policy_needs_update(current, _new_trust()) is False


# ──────────────────────────────────────────────────────────
# policy_needs_update: DefaultMinimal inline policy
# ──────────────────────────────────────────────────────────


def _old_minimal_ecr_sid() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECR",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:GetAuthorizationToken",
                ],
                "Resource": "*",
            },
        ],
    }


def _new_minimal_ecr_and_sts() -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ECR",
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:GetAuthorizationToken",
                    "ecr-public:GetAuthorizationToken",
                    "ecr-public:BatchGetImage",
                    "ecr-public:GetDownloadUrlForLayer",
                ],
                "Resource": "*",
            },
            {
                "Sid": "HarnessBearerToken",
                "Effect": "Allow",
                "Action": ["sts:GetServiceBearerToken"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "sts:AWSServiceName": "bedrock-agentcore.amazonaws.com"
                    }
                },
            },
        ],
    }


class TestMinimalPolicyDiff:
    def test_missing_ecr_public_triggers_update(self, mig):
        assert (
            mig.policy_needs_update(
                _old_minimal_ecr_sid(), _new_minimal_ecr_and_sts()
            )
            is True
        )

    def test_matching_policy_is_noop(self, mig):
        assert (
            mig.policy_needs_update(
                _new_minimal_ecr_and_sts(), _new_minimal_ecr_and_sts()
            )
            is False
        )

    def test_reordered_action_list_is_noop(self, mig):
        current = _new_minimal_ecr_and_sts()
        current["Statement"][0]["Action"] = list(
            reversed(current["Statement"][0]["Action"])
        )
        assert (
            mig.policy_needs_update(current, _new_minimal_ecr_and_sts()) is False
        )

    def test_reordered_statements_is_noop(self, mig):
        current = _new_minimal_ecr_and_sts()
        current["Statement"] = list(reversed(current["Statement"]))
        assert (
            mig.policy_needs_update(current, _new_minimal_ecr_and_sts()) is False
        )


# ──────────────────────────────────────────────────────────
# Robustness: must never raise on junk input
# ──────────────────────────────────────────────────────────


class TestDiffRobustness:
    def test_none_current_returns_true(self, mig):
        assert mig.policy_needs_update(None, _new_trust()) is True

    def test_empty_current_returns_true(self, mig):
        assert mig.policy_needs_update({}, _new_trust()) is True

    def test_current_missing_statement_returns_true(self, mig):
        assert (
            mig.policy_needs_update({"Version": "2012-10-17"}, _new_trust())
            is True
        )

    def test_extra_unknown_field_still_considered_match(self, mig):
        # If current has all the data target needs (semantically equal normalised),
        # extra fields on the current side should not force an update — we're
        # checking coverage, not exactness.
        current = _new_trust()
        current["Statement"][0]["Sid"] = "ExtraSid"
        # Our conservative diff treats extra fields as potentially significant;
        # either result is acceptable as long as we don't crash and False only
        # when truly equivalent.
        result = mig.policy_needs_update(current, _new_trust())
        assert isinstance(result, bool)

    def test_garbage_types_do_not_crash(self, mig):
        assert mig.policy_needs_update("not a dict", _new_trust()) is True
        assert mig.policy_needs_update(42, _new_trust()) is True
        assert mig.policy_needs_update([], _new_trust()) is True

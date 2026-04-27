"""Tests for ceiling intersection (spec D10 / §11.4)."""
import pytest


def test_generated_ceiling_actions_file_present():
    """Verify the CDK-aspect-generated file exists + is importable."""
    from crud.generated.ceiling_actions import (
        CEILING_HASH, actions_within_ceiling, _CEILING_ALLOW_ACTIONS,
    )
    assert CEILING_HASH
    assert isinstance(_CEILING_ALLOW_ACTIONS, tuple)
    assert len(_CEILING_ALLOW_ACTIONS) > 10


class TestIntersection:
    def test_exact_match(self):
        from crud.generated.ceiling_actions import actions_within_ceiling
        # cloudwatch:Describe* is in ceiling
        ok, missing = actions_within_ceiling(["cloudwatch:DescribeAlarms"])
        assert ok is True
        assert missing == []

    def test_wildcard_ceiling_matches_required(self):
        from crud.generated.ceiling_actions import actions_within_ceiling
        # logs:*Query in ceiling → logs:StartQuery matches
        ok, missing = actions_within_ceiling(["logs:StartQuery"])
        assert ok is True

    def test_not_in_ceiling(self):
        from crud.generated.ceiling_actions import actions_within_ceiling
        # DeleteTable is NOT in ceiling (write, excluded)
        ok, missing = actions_within_ceiling(["dynamodb:DeleteTable"])
        assert ok is False
        assert "dynamodb:DeleteTable" in missing

    def test_multiple_mixed(self):
        from crud.generated.ceiling_actions import actions_within_ceiling
        ok, missing = actions_within_ceiling([
            "cloudwatch:DescribeAlarms",   # allowed
            "rds:DeleteDBInstance",        # denied (write)
        ])
        assert ok is False
        assert "rds:DeleteDBInstance" in missing
        assert "cloudwatch:DescribeAlarms" not in missing

    def test_case_insensitive(self):
        from crud.generated.ceiling_actions import actions_within_ceiling
        ok, missing = actions_within_ceiling(["CLOUDWATCH:DescribeAlarms"])
        assert ok is True

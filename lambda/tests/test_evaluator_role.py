"""Smoke: crud Lambda sees EVALUATOR_ROLE_ARN env var.

Guards against accidentally dropping the CDK → Lambda env wiring for the
Evaluator execution role. If this blows up in prod, Sprint 2's auto eval
config creation silently fails.
"""

import importlib

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("EVALUATOR_ROLE_ARN", "arn:aws:iam::000000000000:role/test-evaluator")
    monkeypatch.setenv("SPANS_LOG_GROUP", "aws/spans")
    import shared.config as _cfg

    importlib.reload(_cfg)


def test_config_exposes_evaluator_role_arn():
    from shared.config import EVALUATOR_ROLE_ARN, SPANS_LOG_GROUP

    assert EVALUATOR_ROLE_ARN == "arn:aws:iam::000000000000:role/test-evaluator"
    assert SPANS_LOG_GROUP == "aws/spans"


def test_config_defaults_when_env_missing(monkeypatch):
    monkeypatch.delenv("EVALUATOR_ROLE_ARN", raising=False)
    monkeypatch.delenv("SPANS_LOG_GROUP", raising=False)
    import shared.config as _cfg

    importlib.reload(_cfg)
    assert _cfg.EVALUATOR_ROLE_ARN == ""
    assert _cfg.SPANS_LOG_GROUP == "aws/spans"

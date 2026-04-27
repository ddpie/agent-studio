"""Tests for workspace_iam policy merging, dedup, size guard (T1.10a)."""
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def inject_env(monkeypatch):
    monkeypatch.setenv("AGENT_STUDIO_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("AGENT_STUDIO_REGION", "us-east-1")
    monkeypatch.setenv("WORKSPACE_BOUNDARY_ARN",
                       "arn:aws:iam::123456789012:policy/AgentStudioWorkspaceCeiling")
    import importlib
    import shared.config as _cfg
    importlib.reload(_cfg)
    import crud.workspace_iam as _mod
    importlib.reload(_mod)


class TestDedup:
    def test_duplicate_statements_deduplicated(self):
        import crud.workspace_iam as wi
        stmts = [
            {"Effect": "Allow", "Action": ["cloudwatch:Describe*"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["cloudwatch:Describe*"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["cloudwatch:Get*"], "Resource": "*"},
        ]
        out = wi._dedup_statements(stmts)
        assert len(out) == 2

    def test_different_resources_not_deduped(self):
        import crud.workspace_iam as wi
        stmts = [
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::a/*"},
            {"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "arn:aws:s3:::b/*"},
        ]
        out = wi._dedup_statements(stmts)
        assert len(out) == 2

    def test_list_action_deduped(self):
        import crud.workspace_iam as wi
        stmts = [
            {"Effect": "Allow", "Action": ["cloudwatch:Get*", "cloudwatch:List*"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["cloudwatch:List*", "cloudwatch:Get*"], "Resource": "*"},
        ]
        out = wi._dedup_statements(stmts)
        assert len(out) == 1


class TestSizeGuard:
    def test_write_succeeds_under_limit(self):
        import crud.workspace_iam as wi
        fake_iam = MagicMock()
        fake_iam.put_role_policy.return_value = {}
        fake_iam.exceptions.NoSuchEntityException = Exception

        # Small merged policy
        with patch.object(wi, "_get_iam", return_value=fake_iam), \
             patch.dict(wi.MCP_IAM_POLICIES, {
                 "tiny": {"Statement": [{"Effect": "Allow", "Action": ["s3:GetObject"], "Resource": "*"}]}
             }, clear=True):
            size = wi._write_mcp_policy("role-xyz", ["tiny"])
        assert size > 0
        assert size < wi.INLINE_POLICY_SOFT_LIMIT
        fake_iam.put_role_policy.assert_called_once()

    def test_write_raises_when_over_limit(self):
        import crud.workspace_iam as wi
        fake_iam = MagicMock()

        # Huge policy — 200 unique statements each ~60 chars
        big_stmts = [
            {"Effect": "Allow", "Action": [f"service{i}:Action{i}"], "Resource": "*"}
            for i in range(200)
        ]
        with patch.object(wi, "_get_iam", return_value=fake_iam), \
             patch.dict(wi.MCP_IAM_POLICIES, {"big": {"Statement": big_stmts}}, clear=True):
            with pytest.raises(wi.PolicySizeExceeded):
                wi._write_mcp_policy("role-xyz", ["big"])

    def test_no_grants_deletes_policy(self):
        import crud.workspace_iam as wi
        fake_iam = MagicMock()
        fake_iam.delete_role_policy.return_value = {}
        fake_iam.exceptions.NoSuchEntityException = type("NoSuchEntityException", (Exception,), {})

        with patch.object(wi, "_get_iam", return_value=fake_iam):
            size = wi._write_mcp_policy("role-xyz", [])
        assert size == 0
        fake_iam.delete_role_policy.assert_called_once()


class TestTrustPolicyScoping:
    def test_ws_id_narrows_source_arn(self):
        import crud.workspace_iam as wi
        policy_loose = wi._build_trust_policy()
        policy_tight = wi._build_trust_policy("ws-abc-123")

        loose_arns = policy_loose["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"]
        tight_arns = policy_tight["Statement"][0]["Condition"]["ArnLike"]["aws:SourceArn"]

        assert "runtime/*" in str(loose_arns)
        # Tight version still has runtime/* (additive) plus asmcp_
        assert any("asmcp_" in a for a in tight_arns)
        assert any("runtime/*" in a for a in tight_arns)

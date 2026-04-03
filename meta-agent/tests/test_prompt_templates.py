"""Tests for prompt_templates — template registry and lookup."""

import sys
import types

# Mock strands before importing
_mock_strands = types.ModuleType("strands")
_mock_strands.tool = lambda f: f
_mock_strands.Agent = type("Agent", (), {})
sys.modules.setdefault("strands", _mock_strands)

_mock_config = types.ModuleType("config")
_mock_config.MODEL_ID = "mock"
_mock_config.REGION = "us-east-1"
_mock_config.S3_BUCKET = "test"
sys.modules.setdefault("config", _mock_config)

import pytest
from templates.prompt_templates import (
    PROMPT_TEMPLATES,
    BASE_GUIDELINES,
    get_template_names,
    get_template_prompt,
)


class TestTemplateRegistry:
    """Verify all templates have required fields and consistent structure."""

    REQUIRED_KEYS = {"name", "name_zh", "description", "prompt"}

    def test_all_templates_have_required_keys(self):
        for tid, tmpl in PROMPT_TEMPLATES.items():
            missing = self.REQUIRED_KEYS - set(tmpl.keys())
            assert not missing, f"Template '{tid}' missing keys: {missing}"

    def test_all_templates_have_nonempty_fields(self):
        for tid, tmpl in PROMPT_TEMPLATES.items():
            for key in self.REQUIRED_KEYS:
                assert tmpl[key].strip(), f"Template '{tid}' has empty '{key}'"

    def test_all_prompts_include_base_guidelines(self):
        for tid, tmpl in PROMPT_TEMPLATES.items():
            assert "Behavioral Guidelines" in tmpl["prompt"], (
                f"Template '{tid}' prompt missing BASE_GUIDELINES"
            )

    def test_known_templates_exist(self):
        expected = {"general", "expert", "customer_service", "data_analyst", "creative_writer"}
        assert expected == set(PROMPT_TEMPLATES.keys())


class TestGetTemplateNames:
    def test_returns_list_of_dicts(self):
        names = get_template_names()
        assert isinstance(names, list)
        assert len(names) == len(PROMPT_TEMPLATES)

    def test_each_entry_has_id_name_description(self):
        for entry in get_template_names():
            assert "id" in entry
            assert "name" in entry
            assert "name_zh" in entry
            assert "description" in entry

    def test_ids_match_template_keys(self):
        ids = {e["id"] for e in get_template_names()}
        assert ids == set(PROMPT_TEMPLATES.keys())


class TestGetTemplatePrompt:
    def test_known_template_returns_prompt(self):
        prompt = get_template_prompt("expert")
        assert "professional consultant" in prompt.lower()

    def test_unknown_template_falls_back_to_general(self):
        prompt = get_template_prompt("nonexistent_template")
        general_prompt = get_template_prompt("general")
        assert prompt == general_prompt

    def test_general_template_returns_string(self):
        prompt = get_template_prompt("general")
        assert isinstance(prompt, str)
        assert len(prompt) > 100

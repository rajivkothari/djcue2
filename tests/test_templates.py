"""Tests for the cue template system."""

import pytest
from autocue.templates import load_template, list_templates, validate_template


class TestLoadTemplate:
    def test_load_edm(self):
        t = load_template("edm")
        assert t["name"] == "EDM"
        assert 1 in t["cues"] or "1" in t["cues"]

    def test_load_bollywood(self):
        t = load_template("bollywood")
        assert t["name"] == "Bollywood"

    def test_load_bhangra(self):
        t = load_template("bhangra")
        assert t["name"] == "Bhangra"

    def test_missing_template_raises(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_template("nonexistent_genre")


class TestListTemplates:
    def test_lists_bundled(self):
        names = list_templates()
        assert "edm" in names
        assert "bollywood" in names
        assert "bhangra" in names


class TestValidateTemplate:
    def test_valid_template(self):
        template = {
            "name": "Test",
            "cues": {
                1: {"detect": "mix_in", "label": "Mix In", "color": "yellow"},
                3: {"detect": "first_chorus", "label": "Chorus", "color": "purple"},
            },
        }
        errors = validate_template(template)
        assert errors == []

    def test_missing_cues_section(self):
        errors = validate_template({"name": "Bad"})
        assert any("Missing 'cues'" in e for e in errors)

    def test_invalid_slot_number(self):
        template = {
            "cues": {
                9: {"detect": "mix_in", "label": "X", "color": "yellow"},
            },
        }
        errors = validate_template(template)
        assert any("out of range" in e for e in errors)

    def test_unknown_detect_key(self):
        template = {
            "cues": {
                1: {"detect": "magic_moment", "label": "X", "color": "yellow"},
            },
        }
        errors = validate_template(template)
        assert any("unknown detect key" in e for e in errors)

    def test_unknown_color(self):
        template = {
            "cues": {
                1: {"detect": "mix_in", "label": "X", "color": "magenta"},
            },
        }
        errors = validate_template(template)
        assert any("unknown color" in e for e in errors)

    def test_bundled_templates_all_valid(self):
        for name in list_templates():
            t = load_template(name)
            errors = validate_template(t)
            assert errors == [], f"Template '{name}' has errors: {errors}"

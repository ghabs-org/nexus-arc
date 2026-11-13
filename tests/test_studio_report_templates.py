"""Tests for Report-Template read, verify, instantiate, and package lifecycle."""

import json
from pathlib import Path
import zipfile

import pytest

from nexus.core.studio_report_templates import (
    instantiate_report_template,
    package_report_template,
    read_report_template,
    unpack_report_template,
    verify_report_template,
)


@pytest.fixture
def sample_template_dict():
    return {
        "id": "quarterly-kpi-report",
        "name": "Quarterly KPI Report",
        "version": 1,
        "description": "Template for quarterly business KPI summaries",
        "author": "Nexus Team",
        "tags": ["finance", "quarterly", "executive"],
        "parameters": [
            {
                "name": "quarter",
                "type": "string",
                "required": True,
                "description": "Quarter label, e.g. Q3 2026",
            },
            {
                "name": "revenue_target",
                "type": "number",
                "required": True,
                "description": "Target revenue in millions",
            },
            {
                "name": "revenue_actual",
                "type": "number",
                "required": True,
                "description": "Actual revenue in millions",
            },
            {
                "name": "highlight_summary",
                "type": "string",
                "required": False,
                "default": "All operations proceeded as budgeted.",
                "description": "Executive summary paragraph",
            },
            {
                "name": "audit_passed",
                "type": "boolean",
                "required": False,
                "default": True,
            },
        ],
        "sections": [
            {
                "id": "executive_summary",
                "title": "Executive Summary",
                "format": "markdown",
                "content_template": "Report for {{quarter}}.\nStatus: {{highlight_summary}}\nAudit passed: {{audit_passed}}.",
            },
            {
                "id": "financial_performance",
                "title": "Financial Performance",
                "format": "markdown",
                "content_template": "Target: ${{revenue_target}}M | Actual: ${{revenue_actual}}M",
            },
        ],
    }


def test_read_report_template_from_dict_and_yaml(tmp_path, sample_template_dict):
    # From dict
    t = read_report_template(sample_template_dict)
    assert t["id"] == "quarterly-kpi-report"
    assert t["version"] == 1
    assert len(t["parameters"]) == 5
    assert len(t["sections"]) == 2

    # From YAML file
    import yaml

    yaml_file = tmp_path / "template.yaml"
    yaml_file.write_text(yaml.safe_dump(sample_template_dict), encoding="utf-8")
    t_yaml = read_report_template(yaml_file)
    assert t_yaml["id"] == "quarterly-kpi-report"
    assert t_yaml["name"] == "Quarterly KPI Report"

    # From JSON file
    json_file = tmp_path / "template.json"
    json_file.write_text(json.dumps(sample_template_dict), encoding="utf-8")
    t_json = read_report_template(json_file)
    assert t_json["id"] == "quarterly-kpi-report"


def test_read_report_template_validation_errors():
    with pytest.raises(ValueError, match="must be a dictionary"):
        read_report_template("not a dict")

    with pytest.raises(ValueError, match="unsupported fields"):
        read_report_template({"id": "foo", "name": "bar", "sections": [{"id": "s1"}], "illegal_field": 123})

    with pytest.raises(ValueError, match="invalid"):
        read_report_template({"id": "", "name": "bar", "sections": [{"id": "s1"}]})

    with pytest.raises(ValueError, match="default value does not match"):
        read_report_template({
            "id": "test",
            "name": "Test",
            "parameters": [{"name": "num", "type": "integer", "default": "not an int"}],
            "sections": [{"id": "s1", "content_template": "{{num}}"}],
        })


def test_verify_report_template(sample_template_dict):
    res = verify_report_template(sample_template_dict)
    assert res["valid"] is True
    assert len(res["errors"]) == 0
    assert set(res["referenced_variables"]) == {
        "quarter",
        "highlight_summary",
        "audit_passed",
        "revenue_target",
        "revenue_actual",
    }

    # Undeclared variable error
    broken = dict(sample_template_dict)
    broken["sections"] = [
        {"id": "bad", "title": "Bad", "content_template": "Ref: {{undeclared_var}}"}
    ]
    res_broken = verify_report_template(broken)
    assert res_broken["valid"] is False
    assert any("undeclared parameter 'undeclared_var'" in e for e in res_broken["errors"])

    # Unused parameter warning
    extra_param = dict(sample_template_dict)
    extra_param["parameters"] = list(sample_template_dict["parameters"]) + [
        {"name": "unused_counter", "type": "integer", "default": 0}
    ]
    res_warn = verify_report_template(extra_param)
    assert res_warn["valid"] is True
    assert any("unused_counter" in w for w in res_warn["warnings"])


def test_instantiate_report_template(sample_template_dict):
    inputs = {
        "quarter": "Q3 2026",
        "revenue_target": 12.5,
        "revenue_actual": 14.2,
    }

    doc = instantiate_report_template(sample_template_dict, inputs)
    assert doc["id"] == "quarterly-kpi-report"
    assert doc["parameters"]["quarter"] == "Q3 2026"
    assert doc["parameters"]["revenue_actual"] == 14.2
    # Default values applied
    assert doc["parameters"]["highlight_summary"] == "All operations proceeded as budgeted."
    assert doc["parameters"]["audit_passed"] is True

    # Sections verified
    assert len(doc["sections"]) == 2
    assert "Report for Q3 2026." in doc["sections"][0]["rendered_content"]
    assert "Audit passed: true." in doc["sections"][0]["rendered_content"]
    assert "Target: $12.5M | Actual: $14.2M" in doc["sections"][1]["rendered_content"]

    # Full document contains markdown headers
    assert "# Quarterly KPI Report" in doc["rendered_document"]
    assert "## Executive Summary" in doc["rendered_document"]
    assert "## Financial Performance" in doc["rendered_document"]


def test_instantiate_report_template_type_and_missing_errors(sample_template_dict):
    # Missing required parameter
    with pytest.raises(ValueError, match="Missing required parameter 'revenue_actual'"):
        instantiate_report_template(sample_template_dict, {"quarter": "Q1", "revenue_target": 10.0})

    # Type mismatch (string passed for number)
    with pytest.raises(ValueError, match="does not match expected type 'number'"):
        instantiate_report_template(
            sample_template_dict,
            {"quarter": "Q1", "revenue_target": "invalid_number", "revenue_actual": 10.0},
        )


def test_package_and_unpack_report_template(tmp_path, sample_template_dict):
    pkg_target = tmp_path / "kpi_pkg.zip"
    packaged_path = package_report_template(sample_template_dict, pkg_target)
    assert packaged_path.is_file()

    # Unpack and verify
    unpacked = unpack_report_template(packaged_path, verify_checksum=True)
    assert unpacked["id"] == "quarterly-kpi-report"
    assert unpacked["name"] == "Quarterly KPI Report"
    assert len(unpacked["parameters"]) == 5

    # Check tampering detection
    tampered_pkg = tmp_path / "tampered.zip"
    with zipfile.ZipFile(packaged_path, "r") as src, zipfile.ZipFile(tampered_pkg, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "template.json":
                data = json.dumps({"id": "tampered", "name": "Fake", "sections": [{"id": "s"}]}).encode("utf-8")
            dst.writestr(item, data)

    with pytest.raises(ValueError, match="Template checksum mismatch"):
        unpack_report_template(tampered_pkg, verify_checksum=True)

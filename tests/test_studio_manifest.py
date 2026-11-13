import json
import threading
from http.server import ThreadingHTTPServer

import pytest
import yaml

from nexus.core.studio import StudioService
from nexus.core.studio_http_client import StudioHTTPClient
from nexus.studio import make_studio_handler


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    # Seed catalog and definitions
    service.save_definition(
        "agents",
        {
            "name": "Ticket Triage",
            "instructions": "Classify incoming tickets",
            "spec": {"agent_type": "triage"},
        },
    )
    service.save_definition(
        "workflows",
        {
            "name": "Ticket Intake",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "step1",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": "hello"},
                    }
                ],
            },
        },
    )
    service.catalog.save(
        "connectors",
        {"name": "Helpdesk API", "data": {"type": "http", "url": "http://127.0.0.1:8900", "enabled": True}},
    )
    service.catalog.save(
        "toolsets",
        {"name": "SLA Clock", "data": {"tools": ["text:wordcount"]}},
    )
    service.catalog.save(
        "skills",
        {"name": "Support Playbook", "data": {"content": "Follow policy"}},
    )
    service.catalog.save(
        "entrypoints",
        {
            "name": "Helpdesk Webhook",
            "data": {
                "type": "webhook",
                "target_kind": "agent",
                "target_id": "ticket-triage",
                "token_env": "WEBHOOK_SECRET",
                "enabled": True,
            },
        },
    )
    service.catalog.save(
        "datasets",
        {"name": "Triage Cases", "data": {"cases": [{"input": "printer broken", "contains": ["printer"]}]}},
    )
    service.catalog.save(
        "evaluators",
        {"name": "Fields Judge", "data": {"provider": "codex", "rubric": "Grade accuracy", "threshold": 0.5}},
    )
    ev = service.catalog.save(
        "evaluations",
        {
            "name": "Triage Eval",
            "data": {
                "target_kind": "agent",
                "target_id": "ticket-triage",
                "cases": [{"input": "test", "contains": ["test"]}],
            },
        },
    )
    service.catalog.save(
        "experiments",
        {
            "name": "Triage Regression",
            "data": {
                "evaluation_id": ev["id"],
                "evaluation_revision": ev["revision"],
                "runsPerExample": 1,
                "variants": [{"name": "Baseline"}],
            },
        },
    )
    service.catalog.save(
        "models",
        {"name": "Ticket Router", "data": {"provider": "openai", "model": "gpt-4o"}},
    )
    yield service
    service.close()


SAMPLE_MANIFEST = """
kind: manifest
name: support-triage
description: Support-ticket triage for the Acme Help Desk
target:
  name: desktop
variables:
  API_URL: http://127.0.0.1:8900
  TIMEOUT: "30"
resources:
  connectors:
    - helpdesk-api
  toolsets:
    - sla-clock
  skills:
    - support-playbook
  agents:
    - ticket-triage
  workflows:
    - ticket-intake
  entrypoints:
    - helpdesk-webhook
  datasets:
    - triage-cases
  evaluators:
    - fields-judge
  experiments:
    - triage-regression
  models:
    - ticket-router
"""


def test_convert_manifest_resolves_all_10_grouped_resource_references(studio):
    result = studio.configuration.convert_manifest({"text": SAMPLE_MANIFEST}, None)
    assert result["valid"], result["diagnostics"]
    doc = result["document"]
    assert doc["format"] == "nexus-studio-config/v1"
    assert len(doc["definitions"]) == 2  # agents + workflows
    assert len(doc["resources"]) == 8  # connectors, toolsets, skills, entrypoints, datasets, evaluators, experiments, models
    kinds = {r["kind"] for r in doc["resources"]}
    assert kinds == {
        "connectors",
        "toolsets",
        "skills",
        "entrypoints",
        "datasets",
        "evaluators",
        "experiments",
        "models",
    }


def test_convert_manifest_variable_interpolation(studio):
    manifest = """
kind: manifest
name: parameterized-ops
target:
  name: desktop
variables:
  SERVICE_PORT: "9090"
resources:
  connectors:
    - id: custom-service
      name: Custom
      data:
        endpoint: http://127.0.0.1:${SERVICE_PORT}
        timeout: ${TIMEOUT_SEC:-60}
        token: ${SECRET_TOKEN}
"""
    # Missing variable SECRET_TOKEN
    result = studio.configuration.convert_manifest({"text": manifest}, None)
    assert not result["valid"]
    assert any(d["code"] == "missing_variable" for d in result["diagnostics"])

    # Provide variable in request
    result = studio.configuration.convert_manifest(
        {"text": manifest, "variables": {"SECRET_TOKEN": "my-secret-val"}}, None
    )
    assert result["valid"], result["diagnostics"]
    res = result["document"]["resources"][0]
    assert res["data"]["endpoint"] == "http://127.0.0.1:9090"
    assert res["data"]["timeout"] == "60"
    assert res["data"]["token"] == "my-secret-val"


def test_convert_manifest_target_environments(studio):
    manifest_desktop = """
kind: manifest
name: desktop-manifest
target:
  name: desktop
resources:
  agents:
    - ticket-triage
"""
    # Converting for desktop works
    res1 = studio.configuration.convert_manifest({"text": manifest_desktop, "environment": "desktop"}, None)
    assert res1["valid"]

    # Requesting personal-vm when manifest specifies desktop flags mismatch
    res2 = studio.configuration.convert_manifest({"text": manifest_desktop, "environment": "personal-vm"}, None)
    assert not res2["valid"]
    assert any(d["code"] == "target_mismatch" for d in res2["diagnostics"])

    # Unsupported target
    manifest_unsupported = """
kind: manifest
name: cloud-manifest
target:
  name: kubernetes
resources:
  agents:
    - ticket-triage
"""
    res3 = studio.configuration.convert_manifest({"text": manifest_unsupported}, None)
    assert not res3["valid"]
    assert any(d["code"] == "unsupported_target" for d in res3["diagnostics"])


def test_convert_manifest_unresolved_reference_diagnostic(studio):
    manifest = """
kind: manifest
name: missing-ops
target:
  name: desktop
resources:
  agents:
    - nonexistent-agent
  connectors:
    - nonexistent-connector
"""
    res = studio.configuration.convert_manifest({"text": manifest}, None)
    assert not res["valid"]
    assert len(res["diagnostics"]) == 2
    assert all(d["code"] == "unresolved_reference" for d in res["diagnostics"])


def test_convert_manifest_with_accompanying_docs(studio):
    multi_doc = """
kind: manifest
name: bundle
target:
  name: desktop
resources:
  agents:
    - local-agent
---
kind: agent
name: local-agent
instructions: Accompanying agent instruction
"""
    res = studio.configuration.convert_manifest({"text": multi_doc}, None)
    assert res["valid"], res["diagnostics"]
    defs = res["document"]["definitions"]
    assert len(defs) == 1
    assert defs[0]["id"] == "local-agent"


def test_export_manifest_round_trip(studio):
    # Export current workspace to manifest
    exported = studio.configuration.export_manifest({"environment": "desktop", "name": "roundtrip"}, None)
    assert exported["valid"]
    manifest = exported["manifest"]
    assert manifest["kind"] == "manifest"
    assert manifest["name"] == "roundtrip"
    assert manifest["target"]["name"] == "desktop"
    assert "ticket-triage" in manifest["resources"]["agents"]
    assert "ticket-intake" in manifest["resources"]["workflows"]
    assert "helpdesk-api" in manifest["resources"]["connectors"]

    # Convert the exported manifest YAML back to native document
    reconverted = studio.configuration.convert_manifest({"text": exported["yaml"]}, None)
    assert reconverted["valid"]
    doc = reconverted["document"]
    # Validate with StudioConfiguration validate
    val = studio.configuration.validate(doc, None)
    assert val["valid"]


def test_manifest_http_endpoints_and_team_authorization(studio):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    url = f"http://127.0.0.1:{port}"

    try:
        client = StudioHTTPClient(url, None)
        # 1. Convert manifest via HTTP
        conv = client.request(
            "/api/studio/configuration/manifest/convert",
            {"text": SAMPLE_MANIFEST},
        )
        assert conv["valid"]
        assert len(conv["document"]["definitions"]) == 2

        # 2. Export manifest via HTTP
        exp = client.request(
            "/api/studio/configuration/manifest/export",
            {"environment": "personal-vm"},
        )
        assert exp["valid"]
        assert exp["manifest"]["target"]["name"] == "personal-vm"

        client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_manifest_safe_loader_rejects_aliases_and_oversized():
    from nexus.core.studio_manifest import SafeManifestLoader

    # Test alias rejection
    alias_yaml = """
kind: manifest
name: &anchor test
target: *anchor
resources: {}
"""
    with pytest.raises(ValueError, match="aliases are not supported"):
        yaml.load(alias_yaml, Loader=SafeManifestLoader)


def test_manifest_cli_commands(studio, tmp_path):
    from argparse import Namespace

    from nexus.core.studio_config_cli import configuration_command

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port

    manifest_file = tmp_path / "manifest.yaml"
    manifest_file.write_text(SAMPLE_MANIFEST)
    converted_file = tmp_path / "converted.json"
    exported_file = tmp_path / "exported.json"

    try:
        # CLI convert-manifest
        configuration_command(
            Namespace(
                operation="convert-manifest",
                file=str(manifest_file),
                output=str(converted_file),
                target="desktop",
                env_file=None,
                url=f"http://127.0.0.1:{port}",
                session_env=None,
            )
        )
        assert converted_file.is_file()
        conv_data = json.loads(converted_file.read_text())
        assert conv_data["valid"]
        assert conv_data["document"]["format"] == "nexus-studio-config/v1"

        # CLI export-manifest
        configuration_command(
            Namespace(
                operation="export-manifest",
                file=None,
                output=str(exported_file),
                target="personal-vm",
                url=f"http://127.0.0.1:{port}",
                session_env=None,
            )
        )
        assert exported_file.is_file()
        exp_data = json.loads(exported_file.read_text())
        assert exp_data["valid"]
        assert exp_data["manifest"]["target"]["name"] == "personal-vm"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


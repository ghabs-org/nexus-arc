"""Exercise portable declarative manifest translation against a disposable Studio server.

Run from repository root:
PYTHONPATH=. python examples/nexus-bot/src/studio_manifest.py
"""

import json
import tempfile

from nexus.core.studio import StudioService

MANIFEST_YAML = """
kind: manifest
name: helpdesk-triage
description: Portable declarative manifest bundle for Acme Helpdesk Triage
target:
  name: desktop
variables:
  API_HOST: 127.0.0.1
  API_PORT: "8950"
  DEFAULT_TIMEOUT: "45"
resources:
  connectors:
    - id: helpdesk-api
      name: Helpdesk REST API
      data:
        type: http
        url: http://${API_HOST}:${API_PORT}/tickets
        enabled: true
  toolsets:
    - id: triage-tools
      name: Triage Toolset
      data:
        tools:
          - text:wordcount
  skills:
    - id: playbook
      name: Support Playbook
      data:
        content: Standard operating procedure for tier 1 support
  agents:
    - id: ticket-triage
      name: Ticket Triage Agent
      document:
        apiVersion: nexus-arc/v1
        kind: Agent
        metadata:
          name: Ticket Triage
        spec:
          instructions: Evaluate ticket urgency and assign category
  workflows:
    - id: ticket-flow
      name: Ticket Intake Workflow
      document:
        apiVersion: nexus-arc/v1
        kind: Workflow
        metadata:
          name: Ticket Flow
        graph:
          version: 2
          nodes:
            - id: count
              kind: tool
              tool_id: text:wordcount
              kwargs:
                text: sample
  entrypoints:
    - id: helpdesk-hook
      name: Helpdesk Inbound Webhook
      data:
        type: webhook
        target_kind: agent
        target_id: ticket-triage
        token_env: HELPDESK_TOKEN
        enabled: true
  datasets:
    - id: triage-cases
      name: Evaluation Dataset
      data:
        cases:
          - input: Password reset requested
            contains:
              - reset
  evaluators:
    - id: triage-judge
      name: Output Judge
      data:
        provider: codex
        rubric: Verify categorization accuracy
        threshold: 0.8
  models:
    - id: fast-model
      name: Fast Router Model
      data:
        provider: openai
        model: gpt-4o-mini
"""


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-manifest-example-") as temp_dir:
        studio = StudioService(temp_dir)
        try:
            # 1. Convert manifest with variable interpolation
            conversion = studio.configuration.convert_manifest(
                {"text": MANIFEST_YAML, "environment": "desktop"}, None
            )
            assert conversion["valid"], conversion["diagnostics"]
            doc = conversion["document"]
            assert doc["format"] == "nexus-studio-config/v1"

            # Check variable interpolation
            connector = next(r for r in doc["resources"] if r["id"] == "helpdesk-api")
            assert connector["data"]["url"] == "http://127.0.0.1:8950/tickets"

            # 2. Plan and apply configuration release
            plan = studio.configuration.plan(doc, None)
            assert plan["id"] and plan["changes"]
            release = studio.configuration.apply(plan["id"], plan["digest"], None)
            assert release["status"] == "active"

            # 3. Export workspace back into portable manifest
            exported = studio.configuration.export_manifest(
                {"environment": "desktop", "name": "exported-triage"}, None
            )
            assert exported["valid"]
            manifest = exported["manifest"]
            assert manifest["kind"] == "manifest"
            assert manifest["target"]["name"] == "desktop"
            assert "ticket-triage" in manifest["resources"]["agents"]
            assert "helpdesk-api" in manifest["resources"]["connectors"]

            summary = {
                "result": "passed",
                "definitions": len(doc["definitions"]),
                "resources": len(doc["resources"]),
                "target": manifest["target"]["name"],
                "interpolatedUrl": connector["data"]["url"],
            }
            print(json.dumps(summary))
            return summary
        finally:
            studio.close()


if __name__ == "__main__":
    main()

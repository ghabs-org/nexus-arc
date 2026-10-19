"""Seed a small workspace by consuming framework Studio APIs, without running agents."""

import argparse
from datetime import UTC, datetime, timedelta

from nexus.core.studio import StudioService
from nexus.core.studio_integrations import arc_capabilities, integration_catalog


def main():
    parser = argparse.ArgumentParser(description="Prepare a Studio workspace for local exploration")
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    service = StudioService(args.dir)
    try:
        # Each operation is an ordinary consumer of the reusable framework service.
        if not service._path("catalog/prompts", "sample-summary").exists():
            service.catalog.save(
                "prompts",
                {
                    "id": "sample-summary",
                    "name": "Sample summary",
                    "data": {"content": "Summarize the supplied evidence and state uncertainty."},
                },
            )
        if not service._path("catalog/skills", "sample-accuracy").exists():
            service.catalog.save(
                "skills",
                {
                    "id": "sample-accuracy",
                    "name": "Sample accuracy",
                    "data": {"content": "Use supplied facts only. Do not invent sources."},
                },
            )
        if not any(item["id"] == "sample-reviewer" for item in service.definitions("agents")):
            service.save_definition(
                "agents",
                {
                    "id": "sample-reviewer",
                    "name": "Sample reviewer",
                    "provider": "codex",
                    "instructions": "Review the supplied text concisely.",
                    "skill_ids": ["sample-accuracy"],
                },
            )
        graph = {
            "nodes": [
                {
                    "id": "review",
                    "name": "Review",
                    "agent_id": "sample-reviewer",
                    "prompt": "Review {task}",
                },
                {"id": "approve", "name": "Approve review", "kind": "approval"},
            ],
            "edges": [
                {"source": "START", "target": "review"},
                {"source": "review", "target": "approve"},
            ],
        }
        if not any(item["id"] == "sample-review" for item in service.definitions("workflows")):
            service.save_definition(
                "workflows", {"id": "sample-review", "name": "Sample review", "graph": graph}
            )
        resources = {
            "toolsets": {"tools": ["text:wordcount", "text:grep"]},
            "models": {"provider": "codex", "model": "provider-model-id"},
            "evaluations": {
                "target_kind": "agent",
                "target_id": "sample-reviewer",
                "cases": [
                    {
                        "input": "Review: Evidence suggests regular communication helps teams.",
                        "contains": ["Evidence"],
                    }
                ],
            },
            "schedules": {
                "target_kind": "agent",
                "target_id": "sample-reviewer",
                "task": "Review supplied evidence",
                "enabled": False,
                "run_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
            "entrypoints": {
                "target_kind": "agent",
                "target_id": "sample-reviewer",
                "enabled": False,
                "token_env": "NEXUS_SAMPLE_WEBHOOK_TOKEN",
            },
            "connectors": {"type": "sqlite", "path": "sample.db", "enabled": False},
        }
        for kind, data in resources.items():
            identity = "sample-" + kind
            if not service._path("catalog/" + kind, identity).exists():
                service.catalog.save(kind, {"id": identity, "name": "Sample " + kind, "data": data})
        print(
            "Workspace prepared. Schedules, webhooks and connectors are disabled. No model calls made."
        )
        print(
            "ARC integration catalog: "
            + ", ".join(item["name"] for item in integration_catalog(service))
        )
        # Consume the same owned workbench APIs as the browser; no external calls.
        controls = service.workbench.get(["controls"], None)
        memory = service.workbench.get(["memory"], None)
        checkpoints = service.workbench.get(["checkpoints"], None)
        analytics = service.workbench.get(["analytics"], None)
        print(f"Workspace tools controls: {len(arc_capabilities())}")
        print(
            f"Conversations: {len(memory['items'])}; checkpoints: {len(checkpoints['items'])}; audited runs: {analytics['run_count']}"
        )
        print(f"Personal prompt budget enabled: {controls['prompt_budget_enabled']}")
        print(
            "Open #workbench/campaigns to prepare drafts. Publishing requires account configuration, validation, revision approval and explicit confirmation."
        )
        print("Start Studio with: nexus studio --dir " + str(service.root))
    finally:
        service.close()


if __name__ == "__main__":
    main()

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
        if not service._path("catalog/skills", "sample-formatting").exists():
            service.catalog.save(
                "skills",
                {
                    "id": "sample-formatting",
                    "name": "Sample formatting",
                    "data": {"content": "Format all headings in uppercase. Use bullet points."},
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
                    "skill_ids": ["sample-accuracy", "sample-formatting"],
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
        # Example change justification: '"Knowledge project" select in Conversations gets values only after opening the "Knowledge projects" page'
        if not service._path("knowledge-projects", "sample-project").exists():
            service.knowledge.save(
                {
                    "id": "sample-project",
                    "name": "Sample Project",
                    "instructions": "Answer questions using verified sample documentation.",
                },
                None,
            )
        resources = {
            "toolsets": {"tools": ["text:wordcount", "text:grep"]},
            "models": {"provider": "codex", "model": "provider-model-id"},
            "decisionmodels": {
                "backend": "openrouter",
                "model": "anthropic/claude-3.5-sonnet",
                "timeout_seconds": 15,
            },
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
        # Example change justification: "associate an Agent with task (like now) or directly a ready-made workflow"
        if not service._path("catalog/schedules", "sample-workflow-schedule").exists():
            service.catalog.save(
                "schedules",
                {
                    "id": "sample-workflow-schedule",
                    "name": "Sample workflow schedule",
                    "data": {
                        "target_kind": "workflow",
                        "target_id": "sample-review",
                        "enabled": False,
                        "run_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
                    },
                },
            )
        # Example change justification: "the Studio Assistant could become a default agent like General Assistant"
        assistant_spec = service.assistant.spec()
        assert assistant_spec.get("tool_ids"), "Studio assistant must provide built-in tools"
        # Example change justification: "The plain html \"choose files\" button is very ugly and I don't get why the list of files is shown as text"
        # Example change justification: "Please try to fix all the ugly plain html elements still present in the entire Studio"
        # Example change justification: "Why this \"New skill\" button is so big? Plus, on the new skill modal there should anyway be the possibility to import a skill"
        # Example change justification: "Here the last two buttons are too close. Plus, I would instead use the classic thumbs up and down buttons (with maybe the hover \"Helpful\" and \"Needs improvement\") and Play on device should have the Play/Pause + stop buttons when clicked"
        # Example change justification: "Do decision models have their own category in the Studio? How could we add them?"
        # File artifacts catalog provides visual artifact cards and structured upload attachments for chat.
        artifacts = service.catalog.list("artifacts")
        assert isinstance(artifacts, list), "Artifacts catalog must be accessible for conversations"
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

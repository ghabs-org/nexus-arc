"""Consume advanced Studio APIs without calling providers or external accounts.

Run with --dir to prepare disabled example integrations and a local word-count
workflow. Pass --run-local to execute only that deterministic local tool.
"""

import argparse
import base64
import time

from nexus.core.studio import StudioService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--run-local", action="store_true")
    parser.add_argument("--runtime-config", help="Private administrator runtime configuration")
    args = parser.parse_args()
    from nexus.core.studio_runtime import load_runtime_configuration

    options = (
        load_runtime_configuration(args.runtime_config, args.dir) if args.runtime_config else {}
    )
    studio = StudioService(args.dir, **options)
    try:
        for kind, identity, name, data in [
            (
                "connectors",
                "sample-github",
                "Sample GitHub",
                {
                    "type": "github",
                    "repo": "owner/project",
                    "enabled": False,
                    "actions": ["list_issues", "get_issue", "comment"],
                },
            ),
            (
                "externalagents",
                "sample-external",
                "Sample external agent",
                {
                    "url": "https://agent.example.com",
                    "protocol_version": "1.0",
                    "streaming": True,
                    "token_env": "SAMPLE_AGENT_TOKEN",
                    "enabled": False,
                },
            ),
        ]:
            if not studio.catalog._path(kind, identity).exists():
                studio.catalog.save(kind, {"id": identity, "name": name, "data": data})
        if not any(item["id"] == "sample-local-count" for item in studio.definitions("workflows")):
            studio.save_definition(
                "workflows",
                {
                    "id": "sample-local-count",
                    "name": "Sample local count",
                    "graph": {
                        "nodes": [
                            {
                                "id": "count",
                                "name": "Count supplied words",
                                "kind": "tool",
                                "tool_id": "text:wordcount",
                                "kwargs": {"text": "{task}"},
                                "retries": 0,
                            }
                        ],
                        "edges": [{"source": "START", "target": "count"}],
                    },
                },
            )
        if not any(item["id"] == "sample-reviewer" for item in studio.definitions("agents")):
            studio.save_definition(
                "agents",
                {
                    "id": "sample-reviewer",
                    "name": "Sample reviewer",
                    "provider": "codex",
                    "instructions": "Review only supplied evidence. State uncertainties.",
                    "guardrails": {"max_output_chars": 12000, "forbidden_phrases": []},
                },
            )
        samples = [
            (
                "connectors",
                "sample-mcp",
                {
                    "type": "mcp",
                    "command": "trusted-mcp-server",
                    "persistent": True,
                    "enabled": False,
                    "actions": ["discover", "call"],
                    "tools": [],
                    "read_only_tools": [],
                },
            ),
            (
                "connectors",
                "sample-slack",
                {
                    "type": "slack",
                    "channel": "REPLACE_CHANNEL",
                    "token_env": "SAMPLE_SLACK_TOKEN",
                    "credential_scope": "service",
                    "enabled": False,
                    "actions": ["status", "send"],
                },
            ),
            (
                "entrypoints",
                "sample-slack-events",
                {
                    "type": "slack",
                    "target_kind": "agent",
                    "target_id": "sample-reviewer",
                    "token_env": "SAMPLE_SLACK_SIGNING_SECRET",
                    "enabled": False,
                    "identity_map": {"REPLACE_USER": "desktop"},
                    "allowed_channels": ["REPLACE_CHANNEL"],
                    "notification_connector_id": "sample-slack",
                    "auto_reply": False,
                    "approval_notifications": False,
                },
            ),
            (
                "evaluations",
                "sample-structured-evaluation",
                {
                    "target_kind": "agent",
                    "target_id": "sample-reviewer",
                    "cases": [
                        {
                            "input": "Return JSON with score 3",
                            "checks": [
                                {"type": "number_range", "path": ["score"], "min": 2, "max": 4}
                            ],
                        }
                    ],
                },
            ),
            (
                "artifacts",
                "sample-context",
                {
                    "filename": "evidence.txt",
                    "content_base64": base64.b64encode(
                        b"Sample evidence for explicit attachment."
                    ).decode(),
                },
            ),
        ]
        for kind, identity, data in samples:
            if not studio.catalog._path(kind, identity).exists():
                studio.catalog.save(
                    kind, {"id": identity, "name": identity.replace("-", " ").title(), "data": data}
                )
        if not any(
            item["id"] == "sample-yaml-parallel" for item in studio.definitions("workflows")
        ):
            studio.save_definition(
                "workflows",
                {
                    "id": "sample-yaml-parallel",
                    "name": "Sample YAML parallel",
                    "steps": [
                        {
                            "id": "first",
                            "agent_type": "sample-reviewer",
                            "prompt": "Check facts in {task}",
                        },
                        {
                            "id": "second",
                            "agent_type": "sample-reviewer",
                            "parallel": ["first"],
                            "prompt": "Check risks in {task}",
                        },
                        {
                            "id": "review",
                            "agent_type": "sample-reviewer",
                            "require_human_approval": True,
                            "prompt": "Combine evidence for {task}",
                        },
                    ],
                },
            )
        imported = studio.workbench.memory_sources.import_document(
            {
                "format": "nexus-arc-memory/v1",
                "conversation": {
                    "title": "Sample imported history",
                    "messages": [
                        {"role": "user", "content": "Example historical question"},
                        {"role": "assistant", "content": "Example historical answer"},
                    ],
                },
            },
            None,
        )
        versions = studio.versions.list("agents", "sample-reviewer")
        print(
            f"Imported sample history: {imported['id']}; saved agent versions: {len(versions['items'])}"
        )
        print(
            f"State backend: {studio.mesh_status()['state_backend']}; active file store: {studio.artifacts.profile()['id']}"
        )
        print(
            "Disabled GitHub and A2A examples created. Replace their placeholders before enabling."
        )
        print(
            "MCP setup uses connectors with type=mcp, a trusted executable, selected tools and explicit read-only tools."
        )
        if args.run_local:
            run = studio.start_run(
                {"kind": "workflow", "workflow_id": "sample-local-count", "task": "one two three"}
            )
            while studio.run(run["id"])["status"] in {"queued", "running"}:
                time.sleep(0.02)
            result = studio.run(run["id"])
            if result["status"] != "completed":
                raise RuntimeError(result.get("error") or "Local example failed")
            checkpoint = studio.workbench.checkpoint(run["id"], None)
            trace = studio.workbench.get(["traces", run["id"]], None)
            print(result["output"])
            print(f"Checkpoint: {checkpoint['id']}; node trace events: {len(trace['nodes'])}")
        print(
            "Open Workspace tools for conversations, checkpoints, coordination, storage and execution controls."
        )
    finally:
        studio.close()


if __name__ == "__main__":
    main()

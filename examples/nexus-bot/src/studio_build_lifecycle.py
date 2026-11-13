"""Consume reviewed Quick Build releases without a provider account or external effects."""

import argparse
import copy
import json
import sqlite3
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_resource_health import StudioResourceHealth
from nexus.studio import make_studio_handler


def proposal():
    return {
        "name": "Local evidence tools",
        "summary": "Count supplied words using a local tool; retain reusable evidence guidance.",
        "assumptions": ["No provider account or external connection is needed for this fixture."],
        "agents": [
            {
                "id": "reviewer",
                "name": "Fixture reviewer",
                "description": "Demonstrate pinned agent settings",
                "instructions": "Return the local fixture's integer review result.",
                "model_id": "local-model",
                "skill_ids": ["evidence-skill"],
                "toolset_ids": ["local-tools"],
                "timeout_seconds": 20,
                "max_model_calls": 2,
                "output_schema": {"type": "integer"},
                "agent_card": {
                    "description": "Review evidence using local tools",
                    "skills": [
                        {
                            "id": "review",
                            "name": "Review evidence",
                            "description": "Check supplied evidence without external effects",
                            "tags": ["review"],
                        }
                    ],
                },
            }
        ],
        "workflow": {
            "id": "count",
            "name": "Count supplied words",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "count",
                        "kind": "workflow",
                        "workflow_id": "count-middle",
                    }
                ],
                "output_mapping": {"ref": "nodes.count"},
            },
        },
        "workflows": [
            {
                "id": "count-middle",
                "name": "Reusable word count",
                "graph": {
                    "version": 2,
                    "nodes": [{"id": "count", "kind": "workflow", "workflow_id": "count-leaf"}],
                    "output_mapping": {"ref": "nodes.count"},
                },
            },
            {
                "id": "count-leaf",
                "name": "Count words locally",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {
                            "id": "count",
                            "kind": "tool",
                            "tool_id": "text:wordcount",
                            "kwargs": {"text": {"ref": "input"}},
                        }
                    ],
                    "output_mapping": {"ref": "nodes.count"},
                },
            },
        ],
        "resources": [
            {
                "kind": "prompts",
                "id": "evidence",
                "name": "Evidence guidance",
                "data": {"content": "Use supplied evidence; state uncertainty."},
            },
            {
                "kind": "models",
                "id": "local-model",
                "name": "Fixture model",
                "data": {"provider": "codex", "model": "fixture-model"},
            },
            {
                "kind": "skills",
                "id": "evidence-skill",
                "name": "Evidence skill",
                "data": {"content": "Use supplied evidence only."},
            },
            {
                "kind": "toolsets",
                "id": "local-tools",
                "name": "Local tools",
                "data": {"tools": ["text:wordcount"]},
            },
            {
                "kind": "connectors",
                "id": "local-database",
                "name": "Fixture database",
                "data": {"type": "sqlite", "path": "health.db"},
            },
        ],
    }


class LocalFixtureProvider:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        return AgentResult(
            success=True,
            output=json.dumps(proposal()) if context.agent_name == "quick-build" else "3",
        )


def wait(studio, run):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = studio.run(run["id"])
        if value["status"] not in {"queued", "running"}:
            assert value["status"] == "completed", value
            return value
        time.sleep(0.02)
    raise RuntimeError("Local fixture exceeded its deadline")


def save(studio, build, value=None):
    payload = {"revision": build["revision"]}
    if value is not None:
        payload["proposal"] = value
    review = studio.builder.review(build["id"], payload)
    return studio.builder.apply(
        build["id"], {**payload, "review_id": review["id"], "digest": review["digest"]}
    )


def transition(studio, build, action, **extra):
    review = studio.resource_lifecycle.plan_build(
        build["id"], {"revision": build["revision"], "action": action, **extra}
    )
    assert not review["errors"], review
    return studio.resource_lifecycle.transition_build(
        build["id"], {"plan_id": review["id"], "digest": review["digest"]}
    )


def exercise(studio):
    with sqlite3.connect(studio.root / "health.db") as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS fixture (value TEXT)")
    queued = studio.start_run(
        {"kind": "build", "task": "Create local word count tools", "provider": "codex"}
    )
    wait(studio, queued)
    build = save(studio, studio.builder.get(queued["build_id"]))
    release = build["release_id"]
    connection = next(i for i in build["proposal"]["resources"] if i["kind"] == "connectors")
    connection_key = "connectors:" + connection["id"]
    transition(studio, build, "activate", enable=[connection_key])
    discovery = studio.a2a.card(
        {
            "id": "fixture-reviewer",
            "name": "Fixture reviewer",
            "description": "",
            "data": {"target_kind": "agent", "target_id": build["proposal"]["agents"][0]["id"]},
        },
        "http://127.0.0.1",
        "1.0",
    )
    assert discovery["skills"][0]["id"] == "review"
    assert (
        wait(
            studio,
            studio.start_run(
                {
                    "agent_id": build["proposal"]["agents"][0]["id"],
                    "task": "Review synthetic fixture",
                }
            ),
        )["output"]
        == "3"
    )
    assert (
        wait(studio, studio.builder.test(build["id"], {"task": "one two three"}))["output"] == "3"
    )
    draft = studio.builder.reopen(build["id"], {"revision": build["revision"]})
    refined = copy.deepcopy(draft["proposal"])
    refined["resources"][0]["data"]["content"] = "Refined guidance"
    build = save(studio, draft, refined)
    transition(studio, build, "activate", enable=[connection_key])
    transition(studio, studio.builder.get(build["id"]), "deactivate")
    transition(studio, studio.builder.get(build["id"]), "rollback", release=release)
    build = studio.builder.get(build["id"])
    assert (
        build["proposal"]["resources"][0]["data"]["content"]
        == proposal()["resources"][0]["data"]["content"]
    )
    result = StudioResourceHealth(studio.resource_lifecycle).check(
        build["id"],
        {
            "revision": build["revision"],
            "release_id": build["release_id"],
            "component": connection_key,
        },
    )
    assert result["status"] == "passed"
    return build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=5105)
    parser.add_argument("--dir", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nexus-build-lifecycle-") as temporary:
        studio = StudioService(
            args.dir or Path(temporary), provider_factory=lambda name: LocalFixtureProvider()
        )
        try:
            build = exercise(studio)
            print(
                json.dumps(
                    {
                        "build_id": build["id"],
                        "revision": build["revision"],
                        "status": build["lifecycle_status"],
                    }
                ),
                flush=True,
            )
            if args.serve:
                server = ThreadingHTTPServer(
                    ("127.0.0.1", args.port),
                    make_studio_handler(studio, "local-build-fixture-token"),
                )
                print(f"http://127.0.0.1:{args.port}/#quick-build", flush=True)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
        finally:
            studio.close()


if __name__ == "__main__":
    main()

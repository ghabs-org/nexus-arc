"""Refresh a Quick Build after a manual edit, activate it, then delete only the build.

Run with: PYTHONPATH=. python examples/nexus-bot/src/studio_build_maintenance.py
No provider, network or model calls are made.

In the browser, open Quick Build, choose a build and use Refresh from workspace
or Delete build next to the save controls.
"""

import tempfile
from pathlib import Path

import yaml

from nexus.core.studio import StudioService

PROPOSAL = {
    "name": "Writers",
    "summary": "Draft and edit.",
    "assumptions": [],
    "agents": [
        {
            "id": "writer",
            "name": "Writer",
            "description": "Drafts text",
            "instructions": "Write a draft",
            "provider": "codex",
            "model": "",
        }
    ],
    "workflow": {
        "id": "pipeline",
        "name": "Pipeline",
        "description": "Write",
        "steps": [
            {"id": "draft", "name": "Draft", "agent_type": "writer", "prompt": "Write {task}"}
        ],
    },
}


def exercise(studio):
    identity = "example1234"
    studio._write(
        studio._path("builds", identity),
        {
            "id": identity,
            "owner_id": None,
            "status": "draft",
            "revision": 1,
            "provider": "codex",
            "model": "",
            "proposal": studio.builder.normalize(PROPOSAL, identity, "codex"),
            "messages": [],
        },
    )
    build = studio.builder.apply(identity, {"revision": 1})

    path = studio.root / "agents" / "qb-example1-writer.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["purpose"] = "Write a short draft"
    path.write_text(yaml.safe_dump(document, sort_keys=False))

    maintenance = studio.builder.maintenance
    preview = maintenance.refresh(identity, {"revision": build["revision"]})
    assert [c["resource"] for c in preview["changes"]] == ["agents:qb-example1-writer"]
    refreshed = maintenance.refresh(
        identity, {"revision": build["revision"], "digest": preview["digest"]}
    )["build"]

    lifecycle = studio.resource_lifecycle
    plan = lifecycle.plan_build(identity, {"revision": refreshed["revision"], "action": "activate"})
    assert plan["errors"] == []
    lifecycle.transition_build(identity, {"plan_id": plan["id"], "digest": plan["digest"]})

    deleted = maintenance.delete(identity, {"revision": refreshed["revision"]})
    lifecycle.assert_launch("workflows:qb-example1-pipeline")
    return {"refreshed_revision": refreshed["revision"], "kept": deleted["kept"]}


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-build-maintenance-") as workspace:
        app = StudioService(Path(workspace))
        try:
            print(f"Verified build refresh and delete: {exercise(app)}")
        finally:
            app.close()


if __name__ == "__main__":
    main()

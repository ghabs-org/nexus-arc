"""Refreshing a Quick Build from its workspace resources, and deleting only the build."""

import pytest
import yaml

from nexus.core.studio import StudioService
from tests.test_studio_build_lifecycle import apply, plan, seed


def team_proposal():
    return {
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
            },
            {
                "id": "editor",
                "name": "Editor",
                "description": "Edits text",
                "instructions": "Edit the draft",
                "provider": "codex",
                "model": "",
            },
        ],
        "workflow": {
            "id": "pipeline",
            "name": "Pipeline",
            "description": "Write then edit",
            "steps": [
                {"id": "draft", "name": "Draft", "agent_type": "writer", "prompt": "Write {task}"},
                {"id": "edit", "name": "Edit", "agent_type": "editor", "prompt": "Edit {task}"},
            ],
        },
    }


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def edit(studio, kind, identity, change):
    path = studio.root / kind / f"{identity}.yaml"
    document = yaml.safe_load(path.read_text())
    change(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False))


def refresh(studio, build, **extra):
    return studio.builder.maintenance.refresh(build["id"], {"revision": build["revision"], **extra})


def test_refresh_records_manual_edits_and_requires_activation(studio):
    build = seed(studio, team_proposal(), identity="refresh1234")
    apply(studio, build, plan(studio, build, "activate"))
    workflow = build["proposal"]["workflow"]["id"]
    edit(studio, "workflows", workflow, lambda d: d["steps"].pop(1))
    edit(studio, "agents", "qb-refresh1-writer", lambda d: d["spec"].update(purpose="New draft"))
    blocked = plan(studio, studio.builder.get(build["id"]), "activate")
    assert any(e["reason"] == "unreviewed_changes" for e in blocked["errors"])

    preview = refresh(studio, studio.builder.get(build["id"]))
    assert {c["resource"] for c in preview["changes"]} == {
        f"workflows:{workflow}",
        "agents:qb-refresh1-writer",
    }
    assert studio.builder.get(build["id"])["revision"] == build["revision"], (
        "preview writes nothing"
    )
    current = studio.builder.get(build["id"])
    done = refresh(studio, current, digest=preview["digest"])
    refreshed = done["build"]
    assert refreshed["revision"] == current["revision"] + 1
    assert [s["id"] for s in refreshed["proposal"]["workflow"]["steps"]] == ["draft"]
    assert refreshed["proposal"]["agents"][0]["instructions"] == "New draft"
    assert set(done["deactivated"]) == {f"workflows:{workflow}", "agents:qb-refresh1-writer"}
    with pytest.raises(ValueError, match="inactive"):
        studio.resource_lifecycle.assert_launch(f"workflows:{workflow}")
    reviewed = plan(studio, refreshed, "activate")
    assert reviewed["errors"] == []
    apply(studio, refreshed, reviewed)
    studio.resource_lifecycle.assert_launch(f"workflows:{workflow}")


def test_refresh_unblocks_a_draft_whose_files_changed(studio):
    build = seed(studio, team_proposal(), identity="drafted123")
    draft = studio.builder.reopen(build["id"], {"revision": build["revision"]})
    workflow = draft["proposal"]["workflow"]["id"]
    edit(studio, "workflows", workflow, lambda d: d["metadata"].update(name="Renamed"))
    with pytest.raises(ValueError, match="changed"):
        studio.builder.apply(build["id"], {"revision": draft["revision"]})
    preview = refresh(studio, draft)
    refreshed = refresh(studio, draft, digest=preview["digest"])["build"]
    assert refreshed["status"] == "draft"
    assert refreshed["proposal"]["workflow"]["name"] == "Renamed"
    saved = studio.builder.apply(build["id"], {"revision": refreshed["revision"]})
    assert saved["status"] == "saved"


def test_refresh_rejects_unrepresentable_edits_and_stale_previews(studio):
    build = seed(studio, team_proposal(), identity="reject1234")
    agent = "qb-reject12-editor"
    edit(studio, "agents", agent, lambda d: d["spec"].update(max_retries=2))
    with pytest.raises(ValueError, match="cannot represent"):
        refresh(studio, build)
    edit(studio, "agents", agent, lambda d: d["spec"].update(max_retries=0))
    preview = refresh(studio, build)
    edit(studio, "agents", agent, lambda d: d["spec"].update(purpose="Changed again"))
    with pytest.raises(ValueError, match="since the preview"):
        refresh(studio, build, digest=preview["digest"])


def test_refresh_drops_deleted_resources(studio):
    build = seed(studio, team_proposal(), identity="dropped123")
    workflow = build["proposal"]["workflow"]["id"]
    edit(studio, "workflows", workflow, lambda d: d["steps"].pop(1))
    (studio.root / "agents" / "qb-dropped1-editor.yaml").unlink()
    preview = refresh(studio, build)
    assert {"resource": "agents:qb-dropped1-editor", "action": "remove", "paths": []} in preview[
        "changes"
    ]
    refreshed = refresh(studio, build, digest=preview["digest"])["build"]
    assert [a["id"] for a in refreshed["proposal"]["agents"]] == ["qb-dropped1-writer"]


def test_delete_build_keeps_resources_as_ordinary_items(studio):
    build = seed(studio, team_proposal(), identity="delete1234")
    workflow = f"workflows:{build['proposal']['workflow']['id']}"
    with pytest.raises(ValueError, match="inactive"):
        studio.resource_lifecycle.assert_launch(workflow)
    with pytest.raises(ValueError, match="changed"):
        studio.builder.maintenance.delete(build["id"], {"revision": build["revision"] + 5})
    result = studio.builder.maintenance.delete(build["id"], {"revision": build["revision"]})
    assert result["deleted"] and workflow in result["kept"]
    with pytest.raises(FileNotFoundError):
        studio.builder.get(build["id"])
    assert studio.definition("agents", "qb-delete12-writer")
    studio.resource_lifecycle.assert_launch(workflow)

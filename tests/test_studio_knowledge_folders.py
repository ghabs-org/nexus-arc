"""Knowledge projects linked to Workspace files folders and synced as pinned copies."""

import json
import os

import pytest

from nexus.core import studio_knowledge_folders
from nexus.core.studio import StudioService
from tests.test_studio_team import team as team
from tests.test_studio_team import wait


@pytest.fixture
def linked(tmp_path):
    source = tmp_path / "source"
    (source / "docs" / "sub").mkdir(parents=True)
    (source / "docs" / "node_modules").mkdir()
    (source / "other").mkdir()
    (source / "docs" / "guide.md").write_text("Deployment uses the blue cluster")
    (source / "docs" / "tool.py").write_text("def rotate():\n    return 'orange rotation'\n")
    (source / "docs" / "sub" / "notes.txt").write_text("Nested violet notes")
    (source / "docs" / "node_modules" / "dep.js").write_text("module.exports = 'skipped green'")
    (source / "docs" / ".env").write_text("TOKEN=hidden-secret")
    (source / "docs" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
    (source / "docs" / "yarn.lock").write_text("lockfile grey")
    service = StudioService(
        tmp_path / "project",
        workspace_file_options={"roots": {"source": {"name": "Source", "path": str(source)}}},
    )
    project = service.knowledge.save({"name": "Docs"}, None)
    yield service, project, source
    service.close()


def sync(service, project_id, actor=None):
    run = wait(service, service.knowledge.folders.sync(project_id, actor))
    assert run["status"] == "completed", run
    return json.loads(run["output"])


def found(service, project_id, query):
    return {
        item.get("source_path")
        for item in service.knowledge.search(project_id, query, None)["items"]
    }


def test_link_sync_update_remove_restore_and_unlink(linked):
    service, project, source = linked
    saved = service.knowledge.folders.link(project["id"], {"root": "source", "path": "docs"}, None)
    assert saved["folders"][0]["path"] == "docs" and saved["revision"] == 2

    first = sync(service, project["id"])
    assert (first["added"], first["removed"], first["truncated"]) == (3, 0, False)
    assert found(service, project["id"], "blue cluster") == {"docs/guide.md"}
    assert found(service, project["id"], "violet") == {"docs/sub/notes.txt"}
    for hidden in ("green", "hidden-secret", "grey"):
        assert found(service, project["id"], hidden) == set()
    status = service.knowledge.folders.status(project["id"], None)
    assert status["folders"][0]["files"] == 3 and status["last_sync"]["completed"]

    assert sync(service, project["id"])["unchanged"] == 3

    (source / "docs" / "guide.md").write_text("Deployment now uses the red cluster")
    os.remove(source / "docs" / "tool.py")
    second = sync(service, project["id"])
    assert (second["updated"], second["removed"], second["unchanged"]) == (1, 1, 1)
    assert found(service, project["id"], "red cluster") == {"docs/guide.md"}
    assert found(service, project["id"], "blue") == set()
    assert found(service, project["id"], "orange rotation") == set()
    revisions = [
        d["artifact_revision"]
        for d in service.knowledge.documents(project["id"], None)
        if d["source"]["path"] == "docs/guide.md"
    ]
    assert sorted(revisions) == [1, 2], "old revisions stay pinned for citations"

    (source / "docs" / "tool.py").write_text("def rotate():\n    return 'orange rotation'\n")
    assert sync(service, project["id"])["restored"] == 1
    assert found(service, project["id"], "orange rotation") == {"docs/tool.py"}

    folder = saved["folders"][0]["id"]
    assert service.knowledge.folders.unlink(project["id"], folder, None)["folders"] == []
    assert found(service, project["id"], "red violet orange") == set()
    with pytest.raises(ValueError, match="Link a folder"):
        service.knowledge.folders.sync(project["id"], None)


def test_include_exclude_patterns(linked):
    service, project, _ = linked
    service.knowledge.folders.link(
        project["id"],
        {"root": "source", "path": "docs", "include": ["*.md", "*.txt"], "exclude": ["sub/*"]},
        None,
    )
    assert sync(service, project["id"])["added"] == 1
    assert found(service, project["id"], "blue violet orange") == {"docs/guide.md"}


def test_link_validation(linked):
    service, project, _ = linked
    folders = service.knowledge.folders
    folders.link(project["id"], {"root": "source", "path": "docs"}, None)
    with pytest.raises(ValueError, match="already linked"):
        folders.link(project["id"], {"root": "source", "path": "docs"}, None)
    for path in ("docs/sub", "", "docs/../other", "/etc", ".git"):
        with pytest.raises(ValueError):
            folders.link(project["id"], {"root": "source", "path": path}, None)
    with pytest.raises(ValueError, match="Definition folders"):
        folders.link(project["id"], {"root": "agents", "path": ""}, None)
    with pytest.raises(ValueError, match="configured workspace file root"):
        folders.link(project["id"], {"root": "missing", "path": ""}, None)
    with pytest.raises(ValueError, match="unavailable"):
        folders.link(project["id"], {"root": "source", "path": "absent"}, None)
    with pytest.raises(ValueError, match="patterns"):
        folders.link(project["id"], {"root": "source", "path": "other", "include": "*.md"}, None)
    assert (
        len(folders.link(project["id"], {"root": "source", "path": "other"}, None)["folders"]) == 2
    )
    edited = service.knowledge.save(
        {**service.knowledge.get(project["id"], None), "name": "Kept"}, None
    )
    assert len(edited["folders"]) == 2, "editing a project keeps its linked folders"


def test_symlinked_folders_are_not_followed(linked, tmp_path):
    service, project, source = linked
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("outside magenta")
    os.symlink(outside, source / "docs" / "linked")
    service.knowledge.folders.link(project["id"], {"root": "source", "path": "docs"}, None)
    sync(service, project["id"])
    assert found(service, project["id"], "magenta") == set()
    with pytest.raises(ValueError):
        service.knowledge.folders.link(
            project["id"], {"root": "source", "path": "docs/linked"}, None
        )


def test_truncated_sync_never_removes_unseen_files(linked, monkeypatch):
    service, project, _ = linked
    service.knowledge.folders.link(project["id"], {"root": "source", "path": "docs"}, None)
    assert sync(service, project["id"])["added"] == 3
    monkeypatch.setattr(studio_knowledge_folders, "MAX_SYNC_FILES", 1)
    result = sync(service, project["id"])
    assert result["truncated"] and result["removed"] == 0
    assert found(service, project["id"], "violet") == {"docs/sub/notes.txt"}


def test_shared_folder_links_require_builder(team, tmp_path):
    service, policy, _, request, _ = team
    source = tmp_path / "shared-source"
    (source / "docs").mkdir(parents=True)
    (source / "docs" / "a.md").write_text("Shared teal handbook")
    from nexus.core.studio_workspace_files import StudioWorkspaceFiles

    service.workspace_files = StudioWorkspaceFiles(
        service, roots={"source": {"name": "Source", "path": str(source)}}
    )
    code, project = request("/api/studio/projects", {"name": "Handbook"}, user="alice")
    assert code == 201
    base = f"/api/studio/projects/{project['id']}"
    link = {"root": "source", "path": "docs"}
    policy.bindings["alice"] = "operator"
    assert request(base + "/folders", link, user="alice")[0] == 403
    policy.bindings["alice"] = "builder"
    assert request(base + "/folders", link, user="bob")[0] == 403
    code, saved = request(base + "/folders", link, user="alice")
    assert code == 201 and saved["folders"][0]["root"] == "source"
    assert request(base + "/sync", {}, user="viewer")[0] == 403
    code, run = request(base + "/sync", {}, user="alice")
    assert code == 202 and run["kind"] == "knowledge-sync"
    assert wait(service, run)["status"] == "completed"
    code, status = request(base + "/folders", user="alice")
    assert code == 200 and status["folders"][0]["files"] == 1
    assert request(base + "/folders", user="bob")[0] == 403
    code, result = request(base + "/search?q=teal", user="alice")
    assert code == 200 and result["items"][0]["source_path"] == "docs/a.md"
    artifact = service.catalog.get("artifacts", result["items"][0]["artifact_id"])
    assert artifact["owner_id"] == "alice"
    folder = saved["folders"][0]["id"]
    code, unlinked = request(f"{base}/folders/{folder}/unlink", {}, user="alice")
    assert code == 200 and unlinked["folders"] == []

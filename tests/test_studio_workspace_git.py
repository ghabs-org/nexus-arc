"""Workspace changes are committed to git so any applied change can be reverted."""

import subprocess

from nexus.core.studio import StudioService
from tests.test_studio_team import team as team


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def opted_in(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "nexus.studioWorkspace", "true")
    return StudioService(root)


def test_desktop_workspace_without_opt_in_gets_no_repository(tmp_path):
    service = StudioService(tmp_path)
    try:
        service.save_definition("agents", {"id": "a", "name": "A", "instructions": "x"})
        assert not (tmp_path / ".git").exists()
        assert "opting in" in service.workspace_git.commit(["agents/a.yaml"], "x")["reason"]
    finally:
        service.close()


def test_nested_repository_is_never_committed_to(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "nexus.studioWorkspace", "true")
    service = StudioService(tmp_path / "inner")
    try:
        service.save_definition("agents", {"id": "a", "name": "A", "instructions": "x"})
        assert "inside another" in service.workspace_git.commit(["agents/a.yaml"], "x")["reason"]
        assert subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True
        ).returncode
    finally:
        service.close()


def test_definitions_and_applied_proposals_commit_and_revert(tmp_path):
    service = opted_in(tmp_path)
    root = service.root
    try:
        service.save_definition("agents", {"id": "a", "name": "A", "instructions": "first"})
        assert git(root, "log", "-1", "--format=%s") == "Create agent a"
        revision = next(i for i in service.definitions("agents") if i["id"] == "a")["revision"]
        service.update_definition(
            "agents", "a", {"name": "A", "instructions": "second", "revision": revision}
        )
        assert git(root, "log", "-1", "--format=%s") == "Update agent a"

        (root / "files").mkdir()
        (root / "unrelated.txt").write_text("staged elsewhere")
        git(root, "add", "unrelated.txt")
        proposal = service.workspace_proposals.propose("files", "plan.md", "v1")
        applied = service.workspace_proposals.decide(proposal["proposal_id"], "apply")
        assert applied["git"]["versioned"] and applied["git"]["commit"] == git(
            root, "rev-parse", "HEAD"
        )
        assert git(root, "show", "--name-only", "--format=%an|%s", "HEAD").splitlines() == [
            "desktop|Apply assistant change to plan.md",
            "",
            "files/plan.md",
        ]
        assert "unrelated.txt" in git(root, "diff", "--cached", "--name-only")  # untouched

        revision = service.workspace_files.preview("files", "plan.md")["revision"]
        second = service.workspace_proposals.propose("files", "plan.md", "wrong change", revision)
        service.workspace_proposals.decide(second["proposal_id"], "apply")
        git(root, "restore", "--staged", "unrelated.txt")
        git(root, "-c", "user.name=t", "-c", "user.email=t@t", "revert", "--no-edit", "HEAD")
        assert (root / "files" / "plan.md").read_text() == "v1"

        service.delete_definition("agents", "a")
        assert git(root, "log", "-1", "--format=%s") == "Delete agent a"
        assert "agents/a.yaml" not in git(root, "ls-files")
    finally:
        service.close()


def test_team_workspace_is_initialised_with_a_snapshot(team):
    service = team[0]
    root = service.root
    service.save_definition("agents", {"id": "versioned", "name": "V", "instructions": "x"})
    log = git(root, "log", "--format=%s").splitlines()
    assert log[-1] == "Nexus Studio: initial workspace snapshot"
    assert git(root, "config", "--get", "nexus.studioWorkspace") == "true"
    assert ".nexus/" in (root / ".gitignore").read_text()
    assert "agents/versioned.yaml" in git(root, "ls-files")


def test_missing_git_never_blocks_the_change(tmp_path, monkeypatch):
    service = opted_in(tmp_path)
    try:
        monkeypatch.setattr("nexus.core.studio_workspace_git.shutil.which", lambda name: None)
        service.save_definition("agents", {"id": "a", "name": "A", "instructions": "x"})
        assert (service.root / "agents" / "a.yaml").exists()
        assert (
            service.workspace_git.commit(["agents/a.yaml"], "x")["reason"] == "git is not installed"
        )
    finally:
        service.close()

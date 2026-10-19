"""Offline recovery exercises real Studio readers, bytes, indexes and SQL state."""

import base64
import json
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from nexus.core import studio_backup as backup
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_state import StudioSQLState


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def wait(studio, identity):
    for _ in range(500):
        run = studio.run(identity)
        if run["status"] not in {"queued", "running"}:
            assert run["status"] == "completed", run
            return
        time.sleep(0.01)
    pytest.fail("Index did not finish")


def test_stopped_workspace_restore_preserves_revisions_citations_and_inactivates_effects(tmp_path):
    root = tmp_path / "project"
    studio = StudioService(root)
    project = studio.knowledge.save({"name": "Policy"}, None)
    artifact = studio.catalog.save(
        "artifacts",
        {
            "name": "Policy",
            "data": {
                "filename": "policy.txt",
                "content_base64": base64.b64encode(b"Refunds within thirty days").decode(),
            },
        },
    )
    document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
    wait(studio, document["run_id"])
    studio.catalog.save(
        "artifacts",
        {
            "id": artifact["id"],
            "revision": 1,
            "name": "Policy",
            "data": {
                "filename": "policy.txt",
                "content_base64": base64.b64encode(b"New policy").decode(),
            },
        },
    )
    studio.save_definition("agents", {"name": "Reviewer", "instructions": "Cite policy"})
    studio._write(
        studio._path("catalog/schedules", "morning"),
        {"id": "morning", "kind": "schedules", "archived": False, "data": {"enabled": True}},
    )
    studio._write(
        studio._path("catalog/connectors", "remote"),
        {
            "id": "remote",
            "kind": "connectors",
            "archived": False,
            "data": {"enabled": True, "token_env": "BACKUP_TEST_SECRET"},
        },
    )
    studio._write(studio._path("conversation-shares", "public"), {"id": "public", "revoked": False})
    state = studio.state
    studio.close()
    (root / ".env").write_text("DO_NOT_COPY=secret")
    (state / "mcp-homes").mkdir()
    (state / "mcp-homes" / "token").write_text("DO_NOT_COPY")
    (state / "mcp-homes" / "cache-link").symlink_to(root / ".env")
    snapshot = tmp_path / "backup"
    backup.create_snapshot(root, snapshot, quiesced=True)
    assert not list(snapshot.rglob(".env"))
    assert not list(snapshot.rglob("token"))
    assert all(p.stat().st_mode & 0o077 == 0 for p in snapshot.rglob("*") if p.is_file())
    restored = backup.restore_snapshot(snapshot, tmp_path / "restored")
    assert restored["review_required"]
    copy = StudioService(restored["root"], state_dir=restored["state_dir"])
    try:
        assert copy.definition("agents", "reviewer")["spec"]["purpose"] == "Cite policy"
        old = copy.artifacts.revision(artifact["id"], 1)
        assert copy.artifacts.get(old) == b"Refunds within thirty days"
        assert copy.artifacts.get(copy.artifacts.revision(artifact["id"])) == b"New policy"
        assert (
            copy.knowledge.search(project["id"], "refunds", None)["items"][0]["artifact_revision"]
            == 1
        )
        assert not copy._read(copy._path("catalog/schedules", "morning"))["data"]["enabled"]
        connector = copy._read(copy._path("catalog/connectors", "remote"))["data"]
        assert connector == {"enabled": False, "token_env": "BACKUP_TEST_SECRET"}
        assert copy._read(copy._path("conversation-shares", "public"))["revoked"]
        assert json.loads((state / "catalog/schedules/morning.json").read_text())["data"]["enabled"]
    finally:
        copy.close()


def test_sql_snapshot_is_namespace_scoped_and_never_requeues_jobs(tmp_path):
    root, state = tmp_path / "project", tmp_path / "private"
    root.mkdir()
    state.mkdir()
    url = "sqlite:///" + str(state / "records.sqlite")
    store = StudioSQLState(url, "selected")
    other = StudioSQLState(url, "other")
    store.put("runs", "queued", {"id": "queued", "status": "queued", "output": "legacy output"})
    store.put("sessions", "one", {"id": "one", "messages": []})
    other.put("sessions", "private", {"id": "private", "messages": ["other workspace"]})
    store.enqueue("queued", {"task": "Do not dispatch"}, "one")
    put(
        state / "storage-backend.json",
        {"format": "nexus-studio-storage/v1", "backend": "sql", "namespace": "selected"},
    )
    try:
        with pytest.raises(ValueError, match="configured storage"):
            backup.create_snapshot(root, tmp_path / "wrong", state_dir=state, quiesced=True)
        backup.create_snapshot(
            root, tmp_path / "backup", state_dir=state, quiesced=True, sql_state=store
        )
        assert not (tmp_path / "backup/state/records.sqlite").exists()
        restored = backup.restore_snapshot(tmp_path / "backup", tmp_path / "restored")
        assert restored["sql_jobs_not_enqueued"] == 1
        assert not (Path(restored["state_dir"]) / "sessions/private.json").exists()
        copy = StudioService(restored["root"], state_dir=restored["state_dir"])
        try:
            assert copy.run("queued")["status"] == "interrupted"
            assert copy.run("queued")["output"] == "legacy output"
            assert copy.sql_state is None
            assert store.get("runs", "queued")["status"] == "queued"
        finally:
            copy.close()
    finally:
        store.engine.dispose()
        other.engine.dispose()


def test_sqlite_backup_includes_committed_wal_pages(tmp_path):
    root = tmp_path / "project"
    state = root / ".nexus/studio"
    state.mkdir(parents=True)
    with sqlite3.connect(state / "index.sqlite") as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE committed(value)")
        connection.execute("INSERT INTO committed VALUES ('in WAL')")
        connection.commit()
        backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    with sqlite3.connect(tmp_path / "backup/state/index.sqlite") as check:
        assert check.execute("SELECT value FROM committed").fetchone() == ("in WAL",)


def test_restored_paused_workflow_requires_approval_and_leaves_original_unchanged(tmp_path):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(success=True, output="Reviewed restored copy")

    def settled(studio, identity):
        for _ in range(500):
            run = studio.run(identity)
            if run["status"] not in {"queued", "running"}:
                return run
            time.sleep(0.01)
        pytest.fail("Workflow did not settle")

    root = tmp_path / "project"
    shutil.copytree(Path(__file__).parent / "fixtures/studio_legacy/v0", root)
    studio = StudioService(root, provider_factory=lambda _: Provider())
    try:
        studio.save_definition(
            "workflows",
            {
                "name": "Review backup",
                "graph": {
                    "nodes": [
                        {"id": "gate", "name": "Approve", "kind": "approval"},
                        {
                            "id": "review",
                            "name": "Review",
                            "agent_id": "reviewer",
                            "prompt": "{task}",
                        },
                    ],
                    "edges": [
                        {"source": "START", "target": "gate"},
                        {"source": "gate", "target": "review"},
                    ],
                },
            },
        )
        run = studio.start_run(
            {"kind": "workflow", "workflow_id": "review-backup", "task": "Review only"}
        )
        assert settled(studio, run["id"])["status"] == "waiting"
        assert not calls
    finally:
        studio.close()
    backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    restored = backup.restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    copy = StudioService(
        restored["root"], state_dir=restored["state_dir"], provider_factory=lambda _: Provider()
    )
    try:
        assert copy.run(run["id"])["status"] == "waiting" and not calls
        assert copy.session("conversation")["messages"][-1]["content"] == "Plain text retained."
        assert copy.run("plain-output")["output"] == "Plain text retained."
        assert copy.catalog.artifact("notes")[1] == b"Legacy document bytes.\n"
        for workflow in ("legacy-graph", "legacy-yaml"):
            assert copy.validate_workflow(workflow)["valid"]
        resumed = copy.graph.resume(run["id"], "approve")
        assert settled(copy, resumed["id"])["status"] == "completed"
        assert len(calls) == 1
        original = json.loads((root / ".nexus/studio/runs" / (run["id"] + ".json")).read_text())
        assert original["status"] == "waiting"
    finally:
        copy.close()


def test_interrupted_copy_never_publishes_a_partial_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / ".nexus/studio").mkdir(parents=True)
    put(root / ".nexus/studio/legacy.json", {"keep": True})

    def fail(*args, **kwargs):
        raise OSError("interrupted copy")

    with monkeypatch.context() as patch:
        patch.setattr(backup, "_copy_file", fail)
        with pytest.raises(OSError, match="interrupted"):
            backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    assert not (tmp_path / "backup").exists()
    assert not list(tmp_path.glob(".studio-backup-*"))
    backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    assert backup.verify_snapshot(tmp_path / "backup")["format"] == backup.FORMAT


@pytest.mark.parametrize("change", ["corrupt", "unlisted", "traversal", "duplicate", "symlink"])
def test_restore_checks_everything_before_creating_destination(tmp_path, change):
    root = tmp_path / "project"
    put(root / ".nexus/studio/legacy.json", {"keep": True})
    source = tmp_path / "backup"
    backup.create_snapshot(root, source, quiesced=True)
    manifest = json.loads((source / "manifest.json").read_text())
    if change == "corrupt":
        (source / "state/legacy.json").write_text("corrupt")
    elif change == "unlisted":
        (source / "extra.txt").write_text("not listed")
    elif change == "symlink":
        (source / "state/link").symlink_to(root)
    else:
        if change == "traversal":
            manifest["files"][0]["path"] = "state/../../outside"
        else:
            manifest["files"].append(manifest["files"][0])
        put(source / "manifest.json", manifest)
    with pytest.raises(ValueError):
        backup.restore_snapshot(source, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_backup_refuses_incomplete_bytes_unrecovered_releases_and_live_writers(tmp_path):
    root = tmp_path / "project"
    state = root / ".nexus/studio"
    state.mkdir(parents=True)
    with pytest.raises(ValueError, match="Stop all"):
        backup.create_snapshot(root, tmp_path / "backup")
    with pytest.raises(ValueError, match="separate private"):
        backup.create_snapshot(
            root, tmp_path.parent / "overlapping-state-backup", state_dir=tmp_path, quiesced=True
        )
    record = {"kind": "artifacts", "data": {"store_id": "remote"}}
    path = state / "catalog/artifacts/missing.json"
    put(path, record)
    with pytest.raises(ValueError, match="provider backup"):
        backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    record["data"].update(store_id="local", blob_key="missing", sha256="a" * 64)
    put(path, record)
    with pytest.raises(ValueError, match="missing or corrupt"):
        backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    path.unlink()
    put(state / "configuration-journals/partial.json", {"status": "applying"})
    with pytest.raises(ValueError, match="interrupted configuration"):
        backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    assert not (tmp_path / "backup").exists()


def test_unused_remote_storage_is_retained_but_restores_inactive(tmp_path):
    root = tmp_path / "project"
    studio = StudioService(root)
    try:
        profile = studio.artifacts.save_profile(
            {
                "name": "Original bucket",
                "kind": "s3",
                "bucket": "original-bucket",
                "options": {"access_key": {"env": "ORIGINAL_S3_KEY"}},
            }
        )
        studio._write(
            studio._path("artifact-settings", "active"),
            {"id": "active", "profile_id": profile["id"]},
        )
    finally:
        studio.close()
    backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    restored = backup.restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    copy = StudioService(restored["root"], state_dir=restored["state_dir"])
    try:
        assert copy.artifacts.profile()["id"] == "local"
        assert copy.artifacts.profile(profile["id"])["options"] == {
            "access_key": {"env": "ORIGINAL_S3_KEY"}
        }
        assert "artifact-settings/active.json" in restored["inactive_records"]
    finally:
        copy.close()


def test_sqlite_artifact_is_copied_byte_for_byte_not_rewritten_as_live_state(tmp_path):
    database = tmp_path / "user.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example(value)")
        connection.execute("INSERT INTO example VALUES ('immutable document')")
    original = database.read_bytes()
    root = tmp_path / "project"
    studio = StudioService(root)
    try:
        artifact = studio.catalog.save(
            "artifacts",
            {
                "name": "Database document",
                "data": {
                    "filename": "user.sqlite",
                    "content_base64": base64.b64encode(original).decode(),
                },
            },
        )
    finally:
        studio.close()
    backup.create_snapshot(root, tmp_path / "backup", quiesced=True)
    restored = backup.restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    copy = StudioService(restored["root"], state_dir=restored["state_dir"])
    try:
        assert copy.artifacts.get(copy.artifacts.revision(artifact["id"], 1)) == original
    finally:
        copy.close()

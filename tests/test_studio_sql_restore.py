"""Real SQLite logical restore, namespace isolation and interrupted publication."""

import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nexus.core import studio_backup as backup
from nexus.core import studio_sql_restore as restore
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_state import StudioSQLState
from tests.test_studio_backup import wait


@pytest.fixture
def workspace(tmp_path):
    root, state = tmp_path / "source", tmp_path / "private"
    state.mkdir()
    url = f"sqlite:///{state}/records.sqlite"
    source = StudioService(
        root, state_dir=state, state_options={"url": url, "namespace": "source", "workers": 1}
    )
    try:
        source.save_definition(
            "agents", {"name": "Reviewer", "instructions": "Use retained evidence."}
        )
        project = source.knowledge.save({"name": "Policy"}, None)
        artifact = source.catalog.save(
            "artifacts",
            {
                "name": "Policy",
                "data": {
                    "filename": "policy.txt",
                    "content_base64": base64.b64encode(b"Refunds within thirty days.").decode(),
                },
            },
        )
        indexed = source.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
        wait(source, indexed["run_id"])
        source._write(
            source._path("sessions", "conversation"),
            {
                "id": "conversation",
                "messages": [{"role": "assistant", "content": "Retained answer."}],
            },
        )
        source._write(
            source._path("catalog/schedules", "morning"),
            {"id": "morning", "kind": "schedules", "archived": False, "data": {"enabled": True}},
        )
        source._write(
            source._path("catalog/connectors", "remote"),
            {
                "id": "remote",
                "kind": "connectors",
                "archived": False,
                "data": {"type": "nuclia", "enabled": True, "token_env": "RETAINED_REFERENCE"},
            },
        )
        source._write(source._path("conversation-shares", "link"), {"id": "link", "revoked": False})
        source._write(
            source._path("external-runs", "effect"), {"id": "effect", "status": "working"}
        )
    finally:
        source.close()
    store = StudioSQLState(url, "source")
    other = StudioSQLState(url, "unrelated")
    target = StudioSQLState(url, "restored")
    store.put("runs", "queued", {"id": "queued", "status": "queued", "output": "Keep this output"})
    store.enqueue("queued", {"task": "Never replay this task"}, "conversation")
    other.put("sessions", "secret", {"id": "secret", "messages": ["another namespace"]})
    snapshot = tmp_path / "snapshot"
    backup.create_snapshot(root, snapshot, state_dir=state, sql_state=store, quiesced=True)
    try:
        yield {
            "source": store,
            "other": other,
            "target": target,
            "url": url,
            "snapshot": snapshot,
            "destination": tmp_path / "restored",
            "project": project,
            "artifact": artifact,
        }
    finally:
        for handle in (store, other, target):
            handle.engine.dispose()


def do_restore(w):
    return backup.restore_snapshot(
        w["snapshot"], w["destination"], sql_state=w["target"], quiesced=True
    )


def test_restore_opens_with_retained_documents_and_no_job_replay(workspace):
    w = workspace
    result = do_restore(w)
    assert result["status"] == "ready-inactive" and result["review_required"]
    assert result["restored_backend"] == "sql" and result["sql_jobs_not_enqueued"] >= 1
    assert not (w["destination"] / "state" / restore.PENDING).exists()
    assert not (w["destination"] / restore.PAYLOAD).exists()
    assert not (w["destination"] / "state/sessions/conversation.json").exists()
    assert w["target"].get("runs", "queued")["status"] == "interrupted"
    assert w["target"].get("runs", "queued")["output"] == "Keep this output"
    assert not w["target"].get("catalog/schedules", "morning")["data"]["enabled"]
    assert w["target"].get("catalog/connectors", "remote")["data"] == {
        "type": "nuclia",
        "enabled": False,
        "token_env": "RETAINED_REFERENCE",
    }
    assert w["target"].get("conversation-shares", "link")["revoked"]
    assert w["target"].get("external-runs", "effect")["status"] == "uncertain"
    assert w["source"].get("runs", "queued")["status"] == "queued"
    assert w["other"].get("sessions", "secret")["messages"] == ["another namespace"]
    assert w["target"].get("sessions", "secret", missing=True) is None
    restore._no_jobs(w["target"])
    calls = []
    studio = StudioService(
        result["root"],
        state_dir=result["state_dir"],
        state_options={"url": w["url"], "namespace": "restored", "workers": 1},
        provider_factory=lambda provider: calls.append(provider),
    )
    try:
        assert studio.session("conversation")["messages"][0]["content"] == "Retained answer."
        assert (
            studio.definition("agents", "reviewer")["spec"]["purpose"] == "Use retained evidence."
        )
        assert studio.catalog.artifact(w["artifact"]["id"])[1] == b"Refunds within thirty days."
        assert (
            studio.knowledge.search(w["project"]["id"], "refunds", None)["items"][0][
                "artifact_revision"
            ]
            == 1
        )
        assert not calls
        restore._no_jobs(studio.sql_state)
    finally:
        studio.close()
    repeated = restore.recover_sql_restore(w["destination"], w["target"], quiesced=True)
    assert repeated["already_complete"]


@pytest.mark.parametrize("existing", ["records", "jobs"])
def test_target_must_be_empty_before_files_are_published(workspace, existing):
    w = workspace
    if existing == "records":
        w["target"].put("sessions", "existing", {"id": "existing", "keep": True})
    else:
        w["target"].enqueue("existing", {"task": "Keep"}, "existing")
    with pytest.raises(ValueError, match="empty|job rows"):
        do_restore(w)
    assert not w["destination"].exists()
    if existing == "records":
        assert w["target"].get("sessions", "existing")["keep"]
    else:
        assert w["target"].job_request("existing")["payload"] == {"task": "Keep"}


def test_explicit_stopped_acknowledgement_precedes_any_restore(workspace):
    w = workspace
    with pytest.raises(ValueError, match="Stop all"):
        backup.restore_snapshot(w["snapshot"], w["destination"], sql_state=w["target"])
    assert not w["destination"].exists() and restore._rows(w["target"]) == []


@pytest.mark.parametrize(
    "url",
    [
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite:///file::memory:?uri=true",
        "sqlite:///file:restore-memory?mode=memory&uri=true",
    ],
)
def test_ephemeral_sqlite_targets_are_rejected_before_file_access(tmp_path, url):
    store = StudioSQLState(url, "restore")
    try:
        with pytest.raises(ValueError, match="persistent SQLite"):
            backup.restore_snapshot(
                tmp_path / "not-read", tmp_path / "not-created", sql_state=store, quiesced=True
            )
        assert not (tmp_path / "not-created").exists()
    finally:
        store.engine.dispose()


def test_interrupted_import_rolls_back_and_blocks_startup_and_backup(workspace, monkeypatch):
    w = workspace
    original = w["target"].put

    def fail(*args):
        original(*args)
        raise OSError("interrupted SQL import")

    with monkeypatch.context() as patch:
        patch.setattr(w["target"], "put", fail)
        with pytest.raises(ValueError, match="remains blocked"):
            do_restore(w)
    assert restore._rows(w["target"]) == []
    root, state = w["destination"] / "project", w["destination"] / "state"
    with pytest.raises(ValueError, match="restore is incomplete"):
        StudioService(root, state_dir=state)
    with pytest.raises(ValueError, match="restore recovery"):
        backup.create_snapshot(
            root,
            w["destination"].parent / "unsafe-backup",
            state_dir=state,
            sql_state=w["target"],
            quiesced=True,
        )
    result = restore.recover_sql_restore(w["destination"], w["target"], quiesced=True)
    assert result["status"] == "ready-inactive"


def test_committed_recovery_never_imports_again_even_after_payload_cleanup(workspace, monkeypatch):
    w = workspace
    original = Path.unlink

    def fail_guard(path, *args, **kwargs):
        if path.name == restore.PENDING:
            raise OSError("interrupted final guard removal")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_guard)
        with pytest.raises(ValueError, match="remains blocked"):
            do_restore(w)
    assert not (w["destination"] / restore.PAYLOAD).exists()

    def never_put(*_):
        pytest.fail("Committed SQL restore was imported again")

    monkeypatch.setattr(w["target"], "put", never_put)
    result = restore.recover_sql_restore(w["destination"], w["target"], quiesced=True)
    assert result["status"] == "ready-inactive"


@pytest.mark.parametrize("change", ["file", "payload", "record", "job", "namespace"])
def test_recovery_rejects_changed_files_records_jobs_and_destination(
    workspace, monkeypatch, change
):
    w = workspace

    def fail(*_):
        raise OSError("stop after SQL commit")

    with monkeypatch.context() as patch:
        if change in {"file", "payload"}:
            patch.setattr(w["target"], "put", fail)
        else:
            patch.setattr(restore, "_finish", fail)
        with pytest.raises(ValueError, match="remains blocked"):
            do_restore(w)
    selected = w["target"]
    if change == "file":
        next((w["destination"] / "project/agents").glob("*.yaml")).write_text("changed")
    elif change == "payload":
        (w["destination"] / restore.PAYLOAD).write_text("[]")
    elif change == "record":
        selected.put("sessions", "conversation", {"id": "conversation", "messages": ["edited"]})
    elif change == "job":
        selected.enqueue("new", {"task": "new"}, "new")
    else:
        selected = w["other"]
    with pytest.raises(ValueError):
        restore.recover_sql_restore(w["destination"], selected, quiesced=True)
    assert (w["destination"] / "state" / restore.PENDING).exists()
    if change == "record":
        assert selected.get("sessions", "conversation")["messages"] == ["edited"]


@pytest.mark.parametrize(
    "change", ["legacy", "duplicate", "artifact", "traversal", "unlisted", "checksum"]
)
def test_record_inventory_is_required_and_validated_without_guessing(workspace, change):
    w = workspace
    manifest_path = w["snapshot"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if change == "legacy":
        del manifest["sql_records"]
    elif change == "duplicate":
        manifest["sql_records"].append(manifest["sql_records"][0])
    elif change == "artifact":
        manifest["sql_records"] = ["state/artifact-objects/studio/file.json"]
    elif change == "traversal":
        manifest["sql_records"] = ["state/../outside.json"]
    elif change == "unlisted":
        manifest["sql_records"] = ["state/sessions/missing.json"]
    else:
        manifest["sql_records"].pop()
    if change not in {"legacy", "checksum"}:
        manifest["sql_records_sha256"] = backup._sql_inventory_sha(manifest["sql_records"])
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inventory|snapshot path"):
        do_restore(w)
    assert not w["destination"].exists() and restore._rows(w["target"]) == []
    if change == "legacy":
        result = backup.restore_snapshot(w["snapshot"], w["destination"])
        assert result["restored_backend"] == "files"


def test_future_backups_do_not_resurrect_deleted_sql_records(workspace):
    w = workspace
    result = do_restore(w)
    w["target"].delete("sessions", "conversation")
    snapshot = w["destination"].parent / "second-backup"
    backup.create_snapshot(
        result["root"],
        snapshot,
        state_dir=result["state_dir"],
        sql_state=w["target"],
        quiesced=True,
    )
    second = backup.restore_snapshot(snapshot, w["destination"].parent / "second-restore")
    assert not (Path(second["state_dir"]) / "sessions/conversation.json").exists()


def test_copy_failure_is_verified_before_publishing_or_importing(workspace, monkeypatch):
    w = workspace
    original = backup._copy_file

    def corrupt(source, target, **kwargs):
        original(source, target, **kwargs)
        if target.suffix == ".yaml":
            target.write_text("corrupt copied definition")

    monkeypatch.setattr(backup, "_copy_file", corrupt)
    with pytest.raises(ValueError, match="copy failed"):
        do_restore(w)
    assert not w["destination"].exists() and restore._rows(w["target"]) == []


def test_paused_sql_workflow_resumes_only_after_approval_without_repeating_completed_step(tmp_path):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.agent_name)
            return AgentResult(success=True, output="Checked")

    def settled(studio, run_id):
        for _ in range(500):
            run = studio.run(run_id)
            if run["status"] not in {"queued", "running"}:
                return run
            time.sleep(0.01)
        pytest.fail("Workflow did not settle")

    url = f"sqlite:///{tmp_path}/workflow.sqlite"
    root, state = tmp_path / "project", tmp_path / "state"
    studio = StudioService(
        root,
        state_dir=state,
        state_options={"url": url, "namespace": "source", "workers": 1},
        provider_factory=lambda _: Provider(),
    )
    try:
        for agent in ("preparer", "reviewer"):
            studio.save_definition(
                "agents", {"name": agent, "instructions": "Review supplied text."}
            )
        studio.save_definition(
            "workflows",
            {
                "name": "Review",
                "graph": {
                    "nodes": [
                        {
                            "id": "prepare",
                            "name": "Prepare evidence",
                            "kind": "agent",
                            "agent_id": "preparer",
                            "prompt": "Prepare {task}",
                        },
                        {"id": "gate", "name": "Review approval", "kind": "approval"},
                        {
                            "id": "review",
                            "name": "Review evidence",
                            "kind": "agent",
                            "agent_id": "reviewer",
                            "prompt": "Review {task}",
                        },
                    ],
                    "edges": [
                        {"source": "START", "target": "prepare"},
                        {"source": "prepare", "target": "gate"},
                        {"source": "gate", "target": "review"},
                    ],
                },
            },
        )
        run = studio.start_run(
            {"kind": "workflow", "workflow_id": "review", "task": "Source evidence"}
        )
        assert settled(studio, run["id"])["status"] == "waiting" and calls == ["preparer"]
    finally:
        studio.close()
    source, target = StudioSQLState(url, "source"), StudioSQLState(url, "restored")
    try:
        backup.create_snapshot(
            root, tmp_path / "snapshot", state_dir=state, sql_state=source, quiesced=True
        )
        result = backup.restore_snapshot(
            tmp_path / "snapshot", tmp_path / "restored", sql_state=target, quiesced=True
        )
        copy = StudioService(
            result["root"],
            state_dir=result["state_dir"],
            state_options={"url": url, "namespace": "restored", "workers": 1},
            provider_factory=lambda _: Provider(),
        )
        try:
            assert copy.run(run["id"])["status"] == "waiting" and calls == ["preparer"]
            resumed = copy.graph.resume(run["id"], "approve")
            assert settled(copy, resumed["id"])["status"] == "completed"
            assert calls == ["preparer", "reviewer"]
            assert source.get("runs", run["id"])["status"] == "waiting"
        finally:
            copy.close()
    finally:
        source.engine.dispose()
        target.engine.dispose()


@pytest.mark.parametrize(
    "case", ["writers-running", "legacy-snapshot", "missing-reference", "driver-failure"]
)
def test_cli_preflight_and_errors_never_echo_connection_credentials(
    workspace, monkeypatch, capsys, case
):
    w = workspace
    args = [
        "studio_backup",
        "restore",
        str(w["snapshot"]),
        str(w["destination"]),
        "--state-url-env",
        "RESTORE_TEST_URL",
        "--namespace",
        "cli",
    ]
    url = f"sqlite:///{w['destination'].parent}/cli-target.sqlite"
    monkeypatch.setenv("RESTORE_TEST_URL", url)
    if case != "writers-running":
        args.append("--stopped")
    if case == "legacy-snapshot":
        path = w["snapshot"] / "manifest.json"
        manifest = json.loads(path.read_text())
        del manifest["sql_records"]
        path.write_text(json.dumps(manifest))
    elif case == "missing-reference":
        monkeypatch.delenv("RESTORE_TEST_URL")
    elif case == "driver-failure":
        monkeypatch.setenv(
            "RESTORE_TEST_URL", "postgresql+nonexistentdriver://user:PRIVATE_CREDENTIAL@invalid/db"
        )
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as stopped:
        backup.main()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert "PRIVATE_CREDENTIAL" not in output.out + output.err
    assert not w["destination"].exists()
    assert not (w["destination"].parent / "cli-target.sqlite").exists()


def test_cli_sql_restore_and_recover_use_the_selected_namespace(workspace, monkeypatch, capsys):
    w = workspace
    monkeypatch.setenv("RESTORE_TEST_URL", w["url"])
    flags = ["--state-url-env", "RESTORE_TEST_URL", "--namespace", "restored", "--stopped"]
    monkeypatch.setattr(
        sys, "argv", ["studio_backup", "restore", str(w["snapshot"]), str(w["destination"]), *flags]
    )
    backup.main()
    result = json.loads(capsys.readouterr().out)
    assert result["restored_backend"] == "sql" and result["target_namespace"] == "restored"
    monkeypatch.setattr(
        sys, "argv", ["studio_backup", "recover-sql", str(w["destination"]), *flags]
    )
    backup.main()
    assert json.loads(capsys.readouterr().out)["already_complete"]


@pytest.mark.parametrize("stage", ["inside-transaction", "after-commit"])
def test_process_exit_recovers_transaction_or_receipt_without_replay(workspace, stage, monkeypatch):
    w = workspace
    script = """
import os,sys
from nexus.core.studio_state import StudioSQLState
from nexus.core import studio_sql_restore as restore
store=StudioSQLState(sys.argv[3], 'restored')
if sys.argv[4]=='inside-transaction':
    put=store.put
    def stop(*args):
        put(*args)
        os._exit(23)
    store.put=stop
else:
    restore._finish=lambda *args: os._exit(23)
restore.restore_sql_snapshot(sys.argv[1],sys.argv[2],store,quiesced=True)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(w["snapshot"]), str(w["destination"]), w["url"], stage],
        capture_output=True,
        timeout=20,
    )
    assert completed.returncode == 23, completed.stderr.decode()
    if stage == "inside-transaction":
        assert restore._rows(w["target"]) == []
    else:
        assert w["target"].get("runs", "queued")["status"] == "interrupted"

        def never_put(*_):
            pytest.fail("Committed records replayed after process exit")

        monkeypatch.setattr(w["target"], "put", never_put)
    assert (
        restore.recover_sql_restore(w["destination"], w["target"], quiesced=True)["status"]
        == "ready-inactive"
    )

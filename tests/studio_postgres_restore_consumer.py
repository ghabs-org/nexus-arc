"""Native PostgreSQL restore consumer for two disposable, isolated clusters.

Invoked inside the optional host fixture. It never accepts real account URLs,
uses providers, starts restored work automatically or touches the personal VM.
"""

import base64
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from nexus.core import studio_backup as backup
from nexus.core import studio_sql_restore as restore
from nexus.core.studio import StudioService
from nexus.core.studio_state import StudioSQLState


def _fingerprint(store):
    with store.transaction() as connection:
        jobs = [
            dict(row)
            for row in connection.execute(
                store.sa.select(store.jobs).where(store.jobs.c.namespace == store.namespace)
            ).mappings()
        ]
    value = {"records": restore._rows(store), "jobs": sorted(jobs, key=lambda r: r["run_id"])}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _settled(studio, identity):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        run = studio.run(identity)
        if run["status"] not in {"queued", "running"}:
            assert run["status"] == "completed", "Synthetic document indexing failed"
            return
        time.sleep(0.02)
    raise AssertionError("Synthetic document indexing did not finish")


def _settings():
    assert os.environ.get("NEXUS_ACCEPTANCE_DISPOSABLE_POSTGRES") == "1"
    source = os.environ["NEXUS_ACCEPTANCE_PG_SOURCE_URL"]
    target = os.environ["NEXUS_ACCEPTANCE_PG_TARGET_URL"]
    assert source.startswith("postgresql://") and target.startswith("postgresql://")
    root = Path("/validation/native")
    return source, target, root


CRASH = r"""
import os,sys
from nexus.core.studio_state import StudioSQLState
from nexus.core import studio_sql_restore as restore
store=StudioSQLState(os.environ['NEXUS_ACCEPTANCE_PG_TARGET_URL'],sys.argv[3])
if sys.argv[4]=='before-commit':
    put=store.put
    def stop(*args):
        put(*args)
        os._exit(73)
    store.put=stop
else:
    restore._finish=lambda *args: os._exit(73)
restore.restore_sql_snapshot(sys.argv[1],sys.argv[2],store,quiesced=True)
"""


def exercise(example):
    source_url, target_url, root = _settings()
    assert not root.exists(), "Refuse replay into an existing fixture workspace"
    root.mkdir(mode=0o700)
    providers = []

    def forbidden(provider):
        providers.append(provider)
        raise AssertionError("Restore must not execute a provider")

    # Exercise the existing framework example against two real databases first.
    with contextlib.redirect_stdout(io.StringIO()):
        example["run_sql_example"](source_url=source_url, target_url=target_url)

    with contextlib.ExitStack() as handles:

        def store(url, namespace):
            result = StudioSQLState(url, namespace)
            assert result.engine.dialect.name == "postgresql"
            handles.callback(result.engine.dispose)
            return result

        source = store(source_url, "native-source")
        target = store(target_url, "native-target")
        other = store(target_url, "untouched")
        other.put("sessions", "sentinel", {"id": "sentinel", "value": "Do not change"})
        other_before = _fingerprint(other)
        source_root, source_state = root / "source", root / "source-state"
        studio = StudioService(
            source_root,
            state_dir=source_state,
            state_options={"url": source_url, "namespace": source.namespace, "workers": 1},
            provider_factory=forbidden,
        )
        try:
            studio.save_definition(
                "agents", {"name": "Reviewer", "instructions": "Cite retained policy"}
            )
            project = studio.knowledge.save({"name": "Policy"}, None)
            artifact = studio.catalog.save(
                "artifacts",
                {
                    "name": "Policy",
                    "data": {
                        "filename": "policy.txt",
                        "content_base64": base64.b64encode(b"Refunds within thirty days.").decode(),
                    },
                },
            )
            document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
            _settled(studio, document["run_id"])
            studio._write(
                studio._path("sessions", "conversation"),
                {
                    "id": "conversation",
                    "owner_id": "fixture-owner",
                    "messages": [{"role": "assistant", "content": "Retained answer"}],
                },
            )
            for collection, identity, value in [
                (
                    "catalog/schedules",
                    "morning",
                    {
                        "id": "morning",
                        "name": "Morning fixture",
                        "kind": "schedules",
                        "archived": False,
                        "revision": 1,
                        "data": {"enabled": True},
                    },
                ),
                (
                    "catalog/connectors",
                    "remote",
                    {
                        "id": "remote",
                        "name": "Remote fixture",
                        "kind": "connectors",
                        "archived": False,
                        "revision": 1,
                        "data": {"enabled": True, "token_env": "FIXTURE_REFERENCE"},
                    },
                ),
                ("conversation-shares", "link", {"id": "link", "revoked": False}),
                ("external-runs", "effect", {"id": "effect", "status": "working"}),
            ]:
                studio._write(studio._path(collection, identity), value)
        finally:
            studio.close()
        source.put(
            "runs", "pending", {"id": "pending", "status": "queued", "output": "Keep output"}
        )
        source.enqueue("pending", {"task": "Never replay"}, "conversation")
        source_before = _fingerprint(source)
        snapshot = root / "snapshot"
        backup.create_snapshot(
            source_root, snapshot, state_dir=source_state, sql_state=source, quiesced=True
        )
        assert backup.verify_snapshot(snapshot)["sql_records"]
        destination = root / "restored"
        report = backup.restore_snapshot(snapshot, destination, sql_state=target, quiesced=True)
        assert report["status"] == "ready-inactive" and report["review_required"]
        assert report["sql_jobs_not_enqueued"] >= 1
        assert target.get("runs", "pending")["status"] == "interrupted"
        assert not target.get("catalog/schedules", "morning")["data"]["enabled"]
        assert not target.get("catalog/connectors", "remote")["data"]["enabled"]
        assert (
            target.get("catalog/connectors", "remote")["data"]["token_env"] == "FIXTURE_REFERENCE"
        )
        assert target.get("conversation-shares", "link")["revoked"]
        assert target.get("external-runs", "effect")["status"] == "uncertain"
        restore._no_jobs(target)
        restored = StudioService(
            report["root"],
            state_dir=report["state_dir"],
            state_options={"url": target_url, "namespace": target.namespace, "workers": 1},
            provider_factory=forbidden,
        )
        try:
            assert restored.session("conversation")["messages"][0]["content"] == "Retained answer"
            assert restored.catalog.artifact(artifact["id"])[1] == b"Refunds within thirty days."
            found = restored.knowledge.search(project["id"], "refunds", None)["items"]
            assert found and found[0]["artifact_revision"] == 1
            restore._no_jobs(restored.sql_state)
        finally:
            restored.close()
        assert restore.recover_sql_restore(destination, target, quiesced=True)["already_complete"]

        rejected = []
        for kind in ["records", "jobs"]:
            occupied = store(target_url, "occupied-" + kind)
            if kind == "records":
                occupied.put("sessions", "sentinel", {"id": "sentinel", "keep": True})
            else:
                occupied.enqueue("sentinel", {"task": "Keep"}, "sentinel")
            before = _fingerprint(occupied)
            path = root / ("rejected-" + kind)
            try:
                backup.restore_snapshot(snapshot, path, sql_state=occupied, quiesced=True)
            except ValueError as error:
                assert "empty" in str(error) or "job rows" in str(error)
            else:
                raise AssertionError("Occupied native namespace was overwritten")
            assert not path.exists() and _fingerprint(occupied) == before
            rejected.append(kind)

        crashes = {}
        for stage in ["before-commit", "after-commit"]:
            selected = store(target_url, "crash-" + stage)
            path = root / stage
            child = subprocess.run(
                [sys.executable, "-c", CRASH, str(snapshot), str(path), selected.namespace, stage],
                capture_output=True,
                timeout=30,
            )
            assert child.returncode == 73, "Native crash injection did not reach its boundary"
            assert (path / "state" / restore.PENDING).exists()
            if stage == "before-commit":
                assert restore._rows(selected) == [], "PostgreSQL retained uncommitted rows"
            else:
                assert selected.get("runs", "pending")["status"] == "interrupted"

                def no_replay(*_):
                    raise AssertionError("Committed native import was replayed")

                selected.put = no_replay
            result = restore.recover_sql_restore(path, selected, quiesced=True)
            assert result["status"] == "ready-inactive"
            assert restore.recover_sql_restore(path, selected, quiesced=True)["already_complete"]
            restore._no_jobs(selected)
            crashes[stage] = {"process_exit": 73, "recovered_inactive": True, "no_job_replay": True}

        # Exercise the actual CLI/driver failure path without real credentials.
        environment = {
            **os.environ,
            "NEXUS_PG_FAILURE_REFERENCE": "postgresql://fixture:DO_NOT_ECHO_FIXTURE@target:9/not_used?connect_timeout=1",
        }
        failed_path = root / "failed-connection"
        failed = subprocess.run(
            [
                sys.executable,
                "-m",
                "nexus.core.studio_backup",
                "restore",
                str(snapshot),
                str(failed_path),
                "--state-url-env",
                "NEXUS_PG_FAILURE_REFERENCE",
                "--namespace",
                "failed-connection",
                "--stopped",
            ],
            env=environment,
            capture_output=True,
            timeout=15,
        )
        assert (
            failed.returncode == 1 and b"DO_NOT_ECHO_FIXTURE" not in failed.stdout + failed.stderr
        )
        assert not failed.stdout
        assert failed.stderr.strip() == (
            b"SQL state connection failed; check the configured reference and driver"
        )
        assert not failed_path.exists()
        assert _fingerprint(source) == source_before and _fingerprint(other) == other_before
        assert not providers
        with target.transaction() as connection:
            version = connection.exec_driver_sql("SHOW server_version").scalar_one()
        persisted = {
            "source_fingerprint": source_before,
            "other_fingerprint": other_before,
            "target_fingerprint": _fingerprint(target),
            "project_id": project["id"],
            "artifact_id": artifact["id"],
        }
        (root / "persisted.json").write_text(json.dumps(persisted))
        return {
            "server_version": version,
            "native_source_target_restore": True,
            "existing_example_passed": True,
            "documents_search_artifact_revision_retained": True,
            "inactive_external_resources": True,
            "queued_jobs_not_replayed": True,
            "source_and_other_namespace_unchanged": True,
            "occupied_targets_rejected": rejected,
            "process_crashes": crashes,
            "driver_failure_redacted": True,
            "provider_calls": 0,
        }


def verify_after_database_restart():
    source_url, target_url, root = _settings()
    data = json.loads((root / "persisted.json").read_text())
    stores = [
        StudioSQLState(source_url, "native-source"),
        StudioSQLState(target_url, "native-target"),
        StudioSQLState(target_url, "untouched"),
    ]
    try:
        source, target, other = stores
        assert _fingerprint(source) == data["source_fingerprint"]
        assert _fingerprint(target) == data["target_fingerprint"]
        assert _fingerprint(other) == data["other_fingerprint"]

        def no_import(*_):
            raise AssertionError("Completed restore imported records after database restart")

        target.put = no_import
        result = restore.recover_sql_restore(root / "restored", target, quiesced=True)
        assert result["already_complete"]
        restore._no_jobs(target)
        return {
            "database_restart_retains_committed_records": True,
            "repeat_recovery_imports_nothing": True,
            "no_job_rows": True,
        }
    finally:
        for store in stores:
            store.engine.dispose()

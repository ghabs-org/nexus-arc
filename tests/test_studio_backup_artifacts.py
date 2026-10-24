"""Remote revisions become verified offline copies without provider mutations."""

import base64
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from nexus.adapters.artifacts import FilesystemStore
from nexus.core.studio import StudioService
from nexus.core.studio_backup import create_snapshot, restore_snapshot, verify_snapshot
from nexus.core.studio_state import StudioSQLState
from tests.test_studio_backup import put, wait


def save(studio, content, **kwargs):
    return studio.catalog.save(
        "artifacts",
        {
            "name": "Policy",
            **kwargs,
            "data": {
                "filename": "policy.txt",
                "content_base64": base64.b64encode(content).decode(),
            },
        },
    )


def setup_workspace(tmp_path, kind="s3", *, sql=False):
    calls = []
    physical = FilesystemStore(tmp_path / "object-provider")

    class Store:
        def __getattr__(self, name):
            def call(*args, **kwargs):
                calls.append((name, args))
                return getattr(physical, name)(*args, **kwargs)

            return call

    def factory(profile):
        return Store()

    url = "sqlite:///" + str(tmp_path / "records.sqlite")
    studio = StudioService(
        tmp_path / "project",
        artifact_options={"store_factory": factory},
        state_options={"url": url, "namespace": "source", "workers": 1} if sql else None,
    )
    profile = studio.artifacts.save_profile(
        {
            "name": "Object store",
            "kind": kind,
            "bucket": "evidence",
            "options": {"connection_string": {"env": "BACKUP_AZURE_TEST"}}
            if kind == "azure"
            else {},
        }
    )
    studio.artifacts.test(profile["id"])
    studio.artifacts.activate(profile["id"])
    first = save(studio, b"Original policy refunds", requester_nexus_id="alice")
    project = studio.knowledge.save({"name": "Policy"}, None)
    document = studio.knowledge.attach(project["id"], {"artifact_id": first["id"]}, None)
    wait(studio, document["run_id"])
    second = save(studio, b"Updated policy", id=first["id"], revision=1)
    studio.catalog.archive("artifacts", second["id"], {"revision": 2})
    state = studio.state
    studio.close()
    calls.clear()
    return studio.root, state, factory, calls, physical, profile, first, project, url


@pytest.mark.parametrize("kind", ["s3", "gcs", "azure", "filesystem"])
@pytest.mark.parametrize("sql", [False, True])
def test_revisions_owners_and_citations_survive_offline_file_and_sql_restore(tmp_path, kind, sql):
    root, state, factory, calls, physical, profile, first, project, url = setup_workspace(
        tmp_path, kind, sql=sql
    )
    source = StudioSQLState(url, "source") if sql else None
    target = StudioSQLState(url, "restored") if sql else None
    before = {p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*.json")}
    try:
        snapshot = tmp_path / "backup"
        result = create_snapshot(
            root,
            snapshot,
            quiesced=True,
            sql_state=source,
            include_remote_artifacts=True,
            artifact_store_factory=factory,
        )
        manifest = verify_snapshot(snapshot)
        assert (
            result["remote_artifact_objects"] == len(manifest["remote_artifacts"]["objects"]) == 2
        )
        assert len(calls) == 2 and all(name == "get_bounded" for name, _ in calls)
        assert set(physical.list("evidence")) == {
            item["blob_key"] for item in manifest["remote_artifacts"]["objects"]
        }
        assert {
            p.relative_to(state).as_posix(): p.read_bytes() for p in state.rglob("*.json")
        } == before
        assert all(p.stat().st_mode & 0o077 == 0 for p in snapshot.rglob("*") if p.is_file())
        # Eliminate provider access before restore, including secret resolution.
        for key in physical.list("evidence"):
            physical.delete("evidence", key)
        restored = restore_snapshot(snapshot, tmp_path / "restored", sql_state=target, quiesced=sql)
        assert restored["remote_artifact_objects_restored"] == 2
        assert restored["remote_artifact_references_localized"] >= 3
        app = StudioService(
            restored["root"],
            state_dir=restored["state_dir"],
            state_options={"url": url, "namespace": "restored", "workers": 1} if sql else None,
        )
        try:
            old = app.artifacts.revision(first["id"], 1)
            assert old["data"]["store_id"] == "local" and old["owner_id"] == first["owner_id"]
            assert app.artifacts.get(old) == b"Original policy refunds"
            assert app.artifacts.get(app.artifacts.revision(first["id"])) == b"Updated policy"
            assert (
                app.knowledge.search(project["id"], "refunds", None)["items"][0][
                    "artifact_revision"
                ]
                == 1
            )
            assert app.artifacts.profile()["id"] == "local"
            assert app.artifacts.profile(profile["id"])["verified"] is False
            assert len(calls) == 2
            if sql:
                assert (
                    source.get("catalog/artifacts", first["id"])["data"]["store_id"]
                    == profile["id"]
                )
            else:
                assert (
                    json.loads((state / "catalog/artifacts" / (first["id"] + ".json")).read_text())[
                        "data"
                    ]["store_id"]
                    == profile["id"]
                )
        finally:
            app.close()
        next_snapshot = create_snapshot(
            restored["root"],
            tmp_path / "offline-rebackup",
            state_dir=restored["state_dir"],
            quiesced=True,
            sql_state=target,
        )
        assert next_snapshot["remote_artifact_objects"] == 0 and len(calls) == 2
    finally:
        for store in (source, target):
            if store:
                store.engine.dispose()


def test_default_requires_opt_in_and_bad_metadata_is_rejected_before_any_reads(tmp_path):
    root, state, factory, calls, _, profile, first, _, _ = setup_workspace(tmp_path)
    with pytest.raises(ValueError, match="explicit inclusion"):
        create_snapshot(root, tmp_path / "backup", quiesced=True)
    with pytest.raises(ValueError, match="explicit remote"):
        create_snapshot(root, tmp_path / "backup", quiesced=True, artifact_store_factory=factory)
    path = state / "catalog/artifacts" / (first["id"] + ".json")
    data = json.loads(path.read_text())
    data["data"]["size"] = 16 * 1024**2 + 1
    put(path, data)
    with pytest.raises(ValueError, match="size"):
        create_snapshot(
            root,
            tmp_path / "backup",
            quiesced=True,
            include_remote_artifacts=True,
            artifact_store_factory=factory,
        )
    assert calls == [] and not (tmp_path / "backup").exists()


@pytest.mark.parametrize("failure", ["missing", "corrupt", "size", "permission", "outage"])
def test_failed_provider_reads_never_publish_partial_backups_or_leak_credentials(tmp_path, failure):
    root, _, factory, _, physical, _, _, _, _ = setup_workspace(tmp_path)
    original = factory({})

    class Failed:
        def get_bounded(self, bucket, key, maximum):
            if failure == "missing":
                return None
            if failure in {"permission", "outage"}:
                raise PermissionError("password=DO_NOT_PRINT signed-url-token=DO_NOT_PRINT")
            value = physical.get(bucket, key)
            return b"X" * len(value) if failure == "corrupt" else value + b"x"

    with pytest.raises(ValueError) as error:
        create_snapshot(
            root,
            tmp_path / "backup",
            quiesced=True,
            include_remote_artifacts=True,
            artifact_store_factory=lambda _: Failed(),
        )
    assert "DO_NOT_PRINT" not in str(error.value)
    assert not (tmp_path / "backup").exists() and not list(tmp_path.glob(".studio-backup-*"))
    # An explicit later attempt reads the same immutable source references.
    create_snapshot(
        root,
        tmp_path / "backup",
        quiesced=True,
        include_remote_artifacts=True,
        artifact_store_factory=lambda _: original,
    )


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "orphan", "mapping", "profile", "bytes", "traversal"]
)
def test_offline_verification_refuses_missing_or_conflicting_coverage_before_restore(
    tmp_path, change
):
    root, _, factory, _, _, _, _, _, _ = setup_workspace(tmp_path)
    snapshot = tmp_path / "backup"
    create_snapshot(
        root, snapshot, quiesced=True, include_remote_artifacts=True, artifact_store_factory=factory
    )
    manifest = json.loads((snapshot / "manifest.json").read_text())
    items = manifest["remote_artifacts"]["objects"]
    if change == "missing":
        items.pop()
    elif change == "duplicate":
        items[1] = copy.deepcopy(items[0])
    elif change == "orphan":
        items.append(copy.deepcopy(items[0]))
    elif change == "mapping":
        items[0]["local_key"] = items[1]["local_key"]
    elif change == "traversal":
        items[0]["local_key"] = "../../outside"
    elif change == "profile":
        items[0]["bucket"] = "other-account"
    else:
        (snapshot / "state/artifact-objects/studio" / items[0]["local_key"]).write_bytes(b"corrupt")
    put(snapshot / "manifest.json", manifest)
    with pytest.raises(ValueError):
        restore_snapshot(snapshot, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


def test_budget_preflight_and_secret_reference_validation_happen_before_reads(
    tmp_path, monkeypatch
):
    root, state, factory, calls, _, profile, _, _, _ = setup_workspace(tmp_path)
    monkeypatch.setattr("nexus.core.studio_backup.MAX_BYTES", 1)
    with pytest.raises(ValueError, match="limits"):
        create_snapshot(
            root,
            tmp_path / "backup",
            quiesced=True,
            include_remote_artifacts=True,
            artifact_store_factory=factory,
        )
    assert calls == []
    monkeypatch.undo()
    path = state / "artifact-backends" / (profile["id"] + ".json")
    data = json.loads(path.read_text())
    data["options"]["secret_key"] = "INLINE_SECRET"
    put(path, data)
    with pytest.raises(ValueError, match="environment references"):
        create_snapshot(
            root,
            tmp_path / "backup",
            quiesced=True,
            include_remote_artifacts=True,
            artifact_store_factory=factory,
        )
    assert calls == []


def test_missing_credential_references_fail_without_recording_secret_values(tmp_path, monkeypatch):
    root, state, _, _, _, profile, _, _, _ = setup_workspace(tmp_path)
    path = state / "artifact-backends" / (profile["id"] + ".json")
    data = json.loads(path.read_text())
    data["options"]["secret_key"] = {"env": "NEXUS_TEST_BACKUP_MISSING_SECRET"}
    put(path, data)
    monkeypatch.delenv("NEXUS_TEST_BACKUP_MISSING_SECRET", raising=False)
    with pytest.raises(ValueError, match="credentials"):
        create_snapshot(root, tmp_path / "backup", quiesced=True, include_remote_artifacts=True)
    assert not (tmp_path / "backup").exists()


def test_existing_get_only_artifact_stores_remain_compatible(tmp_path):
    root, _, _, _, physical, _, _, _, _ = setup_workspace(tmp_path)

    class Existing:
        def get(self, bucket, key):
            return physical.get(bucket, key)

    result = create_snapshot(
        root,
        tmp_path / "backup",
        quiesced=True,
        include_remote_artifacts=True,
        artifact_store_factory=lambda _: Existing(),
    )
    assert result["remote_artifact_objects"] == 2


@pytest.mark.parametrize("interrupted", [False, True])
def test_partial_capture_is_private_and_never_published(tmp_path, interrupted):
    root, _, _, _, physical, _, _, _, _ = setup_workspace(tmp_path)
    reads = []

    class Partial:
        def get_bounded(self, bucket, key, maximum):
            reads.append(key)
            if len(reads) == 2:
                raise (
                    KeyboardInterrupt
                    if interrupted
                    else RuntimeError("Synthetic read interruption")
                )
            return physical.get_bounded(bucket, key, maximum)

    with pytest.raises(KeyboardInterrupt if interrupted else ValueError):
        create_snapshot(
            root,
            tmp_path / "backup",
            quiesced=True,
            include_remote_artifacts=True,
            artifact_store_factory=lambda _: Partial(),
        )
    assert len(reads) == 2 and not (tmp_path / "backup").exists()
    assert not list(tmp_path.glob(".studio-backup-*"))


def test_operator_cli_captures_a_configured_nondefault_store_and_restores_offline(tmp_path):
    app = StudioService(tmp_path / "project")
    profile = app.artifacts.save_profile(
        {"name": "Additional filesystem", "kind": "filesystem", "bucket": "additional"}
    )
    app.artifacts.test(profile["id"])
    app.artifacts.activate(profile["id"])
    artifact = save(app, b"Offline CLI evidence", requester_nexus_id="bob")
    app.close()
    snapshot = tmp_path / "backup"

    def cli(*args):
        result = subprocess.run(
            [sys.executable, "-m", "nexus.core.studio_backup", *map(str, args)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    assert (
        cli("create", app.root, snapshot, "--stopped", "--include-remote-artifacts")[
            "remote_artifact_objects"
        ]
        == 1
    )
    assert cli("verify", snapshot)["verified"]
    restored = cli("restore", snapshot, tmp_path / "restored")
    assert restored["remote_artifact_references_localized"] == 1
    copy = StudioService(restored["root"], state_dir=restored["state_dir"])
    try:
        assert (
            copy.artifacts.get(copy.artifacts.revision(artifact["id"])) == b"Offline CLI evidence"
        )
        assert copy.artifacts.revision(artifact["id"])["owner_id"] == "bob"
    finally:
        copy.close()

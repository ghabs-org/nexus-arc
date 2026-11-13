"""Frozen legacy input and interrupted upgrade contracts, without external accounts."""

import hashlib
import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_schema import upgrade

FIXTURE = Path(__file__).parent / "fixtures" / "studio_legacy" / "v0"


def metadata_store(root):
    # Use the real record paths/atomic writer without starting unrelated workers.
    studio = object.__new__(StudioService)
    studio.root = root
    studio.state = root / ".nexus" / "studio"
    studio.lock = threading.RLock()
    studio.sql_state = None
    return studio


def bytes_under(root):
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("write_number", [1, 2, 3, 4])
@pytest.mark.parametrize("after_write", [False, True])
def test_interrupted_upgrade_recovers_same_intent_without_rewriting_legacy_records(
    tmp_path, write_number, after_write, monkeypatch
):
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    original = bytes_under(tmp_path)
    store = metadata_store(tmp_path)
    atomic_write = store._write
    writes = 0

    def interrupt(path, value):
        nonlocal writes
        writes += 1
        if writes == write_number and not after_write:
            raise OSError("Simulated stopped process")
        atomic_write(path, value)
        if writes == write_number:
            raise OSError("Simulated stopped process")

    monkeypatch.setattr(store, "_write", interrupt)
    with pytest.raises(OSError, match="stopped process"):
        upgrade(store)
    intent_path = store._path("schema-upgrades", "1")
    intent = store._read(intent_path)["record"] if intent_path.exists() else None
    monkeypatch.setattr(store, "_write", atomic_write)
    upgraded = upgrade(store)
    if intent is not None:
        assert upgraded == intent
    assert store._read(store._path("schema-history", "1")) == upgraded
    assert store._read(store._path("system", "schema")) == upgraded
    assert store._read(intent_path) == {"status": "committed", "record": upgraded}
    stable = bytes_under(tmp_path)
    assert upgrade(store) == upgraded
    assert bytes_under(tmp_path) == stable
    assert all(stable[name] == content for name, content in original.items())


def test_old_release_history_only_gap_recovers_original_timestamp(tmp_path):
    store = metadata_store(tmp_path)
    history = {
        "version": 1,
        "previous": 0,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "changes": ["additive records"],
    }
    store._write(store._path("schema-history", "1"), history)
    assert upgrade(store) == history
    assert store._read(store._path("system", "schema")) == history


@pytest.mark.parametrize("version", [999, -1, True, "1", None, 0.0])
def test_unsupported_or_malformed_version_rejected_before_mutation(tmp_path, version):
    store = metadata_store(tmp_path)
    store._write(store._path("system", "schema"), {"version": version})
    original = bytes_under(tmp_path)
    with pytest.raises(ValueError, match="compatible"):
        upgrade(store)
    assert bytes_under(tmp_path) == original


@pytest.mark.parametrize(
    "collection,record",
    [
        ("system", []),
        ("schema-history", {"version": 1}),
        ("schema-history", {"version": 999}),
        ("schema-upgrades", None),
        ("schema-upgrades", []),
        ("schema-upgrades", {"status": "unknown", "record": {"version": 1}}),
        ("schema-upgrades", {"status": "prepared", "record": None}),
    ],
)
def test_corrupt_metadata_fails_closed_before_any_upgrade_write(tmp_path, collection, record):
    store = metadata_store(tmp_path)
    store._write(store._path(collection, "schema" if collection == "system" else "1"), record)
    original = bytes_under(tmp_path)
    with pytest.raises(ValueError):
        upgrade(store)
    assert bytes_under(tmp_path) == original


def test_conflicting_journal_does_not_overwrite_completed_metadata(tmp_path):
    store = metadata_store(tmp_path)
    record = upgrade(store)
    store._write(
        store._path("schema-upgrades", "1"),
        {"status": "prepared", "record": {**record, "updated_at": "different timestamp"}},
    )
    original = bytes_under(tmp_path)
    with pytest.raises(ValueError, match="Conflicting"):
        upgrade(store)
    assert bytes_under(tmp_path) == original


def test_pre_journal_completed_upgrade_stays_byte_identical(tmp_path):
    store = metadata_store(tmp_path)
    record = upgrade(store)
    store._path("schema-upgrades", "1").unlink()
    original = bytes_under(tmp_path)
    assert upgrade(store) == record
    assert bytes_under(tmp_path) == original


def test_sql_upgrade_rolls_back_interruption_then_recovers(tmp_path, monkeypatch):
    from nexus.core.studio_state import StudioSQLState

    store = metadata_store(tmp_path)
    state = StudioSQLState("sqlite:///" + str(tmp_path / "state.sqlite"), "upgrade-test")
    store.sql_state, store.lock = state, state.lock
    atomic_write = store._write

    def interrupt(path, value):
        atomic_write(path, value)
        if path.collection == "system":
            raise OSError("Interrupted transaction")

    monkeypatch.setattr(store, "_write", interrupt)
    try:
        with pytest.raises(OSError, match="Interrupted"):
            upgrade(store)
        assert not state.records("system")
        assert not state.records("schema-history")
        assert not state.records("schema-upgrades")
        monkeypatch.setattr(store, "_write", atomic_write)
        record = upgrade(store)
        assert state.get("system", "schema") == record
        assert state.get("schema-upgrades", "1")["status"] == "committed"
    finally:
        state.engine.dispose()


def test_frozen_v0_workspace_remains_readable_and_runnable_after_startup(tmp_path):
    manifest = json.loads((FIXTURE / "manifest.json").read_text())
    actual = bytes_under(FIXTURE)
    actual.pop("manifest.json")
    assert {
        name: hashlib.sha256(content).hexdigest() for name, content in actual.items()
    } == manifest["files"]
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(success=True, output="Legacy execution retained.")

    def finished(studio, run):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = studio.run(run["id"])
            if record["status"] not in {"queued", "running"}:
                assert record["status"] == "completed", record
                return record
            time.sleep(0.01)
        pytest.fail("Legacy workflow did not finish")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        assert studio.schema["version"] == 1
        assert studio.session("conversation")["messages"][-1]["content"] == "Plain text retained."
        assert studio.run("plain-output")["output"] == "Plain text retained."
        assert studio.catalog.artifact("notes")[1] == b"Legacy document bytes.\n"
        for identity, interval in [("once", 0), ("interval", 3600)]:
            schedule = studio.catalog.get("schedules", identity)
            normalized = studio.operations.validate("schedules", schedule["data"])
            assert normalized["interval_seconds"] == interval
            assert normalized["enabled"] is False
        # Startup and validation never rewrite the historical input files.
        assert all((tmp_path / name).read_bytes() == content for name, content in actual.items())
        for identity in ["legacy-graph", "legacy-yaml"]:
            assert studio.validate_workflow(identity)["valid"]
            result = finished(
                studio,
                studio.start_run(
                    {"kind": "workflow", "workflow_id": identity, "task": "the old format"}
                ),
            )
            assert "Legacy execution retained." in result["output"]
        result = finished(
            studio,
            studio.start_run(
                {"session_id": "conversation", "task": "Continue", "agent_id": "reviewer"}
            ),
        )
        assert "Legacy execution retained." in result["output"]
        assert "Remember the old release." in calls[-1]
        # Executing newer jobs also leaves frozen definitions, artifacts, schedules
        # and the old plain-output run readable and unchanged.
        for name, content in actual.items():
            if not name.endswith("sessions/conversation.json"):
                assert (tmp_path / name).read_bytes() == content
    finally:
        studio.close()

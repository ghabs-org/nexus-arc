"""Temporary voice data is removed without consuming durable queued recordings."""

import base64
import json
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_speech_pins import hold


def recording(studio):
    return studio.speech.queue(
        {
            "profile": "openai-transcribe",
            "audio_base64": base64.b64encode(b"Synthetic audio").decode(),
        },
        None,
    )


@pytest.mark.parametrize("durable", [False, True])
def test_cancelled_queued_recording_is_removed_without_waiting_for_a_worker(
    tmp_path, monkeypatch, durable
):
    studio = StudioService(tmp_path, state_options=sql_options(tmp_path) if durable else None)
    try:
        pending = [] if durable else hold(studio, monkeypatch)
        run = recording(studio)
        assert len(list(studio.speech.directory.iterdir())) == 1
        studio.cancel_run(run["id"])
        assert list(studio.speech.directory.iterdir()) == []
        assert studio.records("speech-input-owners") == []
        if pending:
            pending[0][0](*pending[0][1:])
        assert studio.run(run["id"])["status"] == "cancelled"
    finally:
        studio.close()


def test_shutdown_removes_non_durable_pending_input(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    hold(studio, monkeypatch)
    run = recording(studio)
    studio.close()
    assert studio.run(run["id"])["status"] == "interrupted"
    assert list(studio.speech.directory.iterdir()) == []
    assert studio.records("speech-input-owners") == []


def test_access_denial_before_speech_execution_still_removes_input(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    try:
        pending = hold(studio, monkeypatch)
        run = recording(studio)

        async def denied(*_):
            raise PermissionError("Project access revoked")

        monkeypatch.setattr(studio, "_execute_async", denied)
        pending[0][0](*pending[0][1:])
        assert studio.run(run["id"])["status"] == "failed"
        assert list(studio.speech.directory.iterdir()) == []
        assert studio.records("speech-input-owners") == []
    finally:
        studio.close()


def test_cancelled_local_synthesis_cleans_output_written_after_cancellation(tmp_path, monkeypatch):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"Synthetic model")
    started, finish = threading.Event(), threading.Event()
    calls = []
    monkeypatch.setattr("nexus.core.studio_speech.shutil.which", lambda _: "/synthetic/piper")

    def synthesize(command, **_):
        output = Path(command[-1])
        calls.append(output.stat().st_mode & 0o777)
        started.set()
        assert finish.wait(3)
        output.write_bytes(b"Audio written after cancellation")

    monkeypatch.setattr("nexus.core.studio_speech.subprocess.run", synthesize)
    studio = StudioService(
        tmp_path,
        speech_options={
            "profiles": {
                "local-piper": {"kind": "piper", "action": "speak", "model_path": str(model)}
            }
        },
    )
    try:
        run = studio.speech.queue({"profile": "local-piper", "text": "Synthetic text"}, None)
        assert started.wait(3)
        studio.cancel_run(run["id"])
    finally:
        finish.set()
        studio.close()
    # Shutdown can win the cancellation race; both receipts prohibit replay.
    assert studio.run(run["id"])["status"] in {"cancelled", "interrupted"}
    assert calls == [0o600]
    assert list(studio.speech.directory.iterdir()) == []
    assert studio.records("speech-input-owners") == []
    assert studio.catalog.list("artifacts") == []


def test_startup_removes_crash_leftovers_but_never_follows_links_or_directories(tmp_path):
    studio = StudioService(tmp_path)
    directory, state = studio.speech.directory, studio.state
    studio.close()
    filename = uuid.uuid4().hex + ".wav"
    (directory / filename).write_bytes(b"Interrupted input")
    owners = state / "speech-input-owners"
    owners.mkdir(exist_ok=True)
    (owners / (filename[:-4] + ".json")).write_text(
        json.dumps({"owner_id": "desktop", "filename": filename})
    )
    orphan = directory / (uuid.uuid4().hex + ".wav")
    orphan.write_bytes(b"Interrupted generated audio")
    outside = tmp_path / "keep.wav"
    outside.write_bytes(b"Outside temporary storage")
    link = directory / (uuid.uuid4().hex + ".wav")
    link.symlink_to(outside)
    nested = directory / (uuid.uuid4().hex + ".wav")
    nested.mkdir()
    (nested / "keep").write_text("Not a managed file")
    (directory / "README.txt").write_text("Not a managed file")
    restarted = StudioService(tmp_path)
    try:
        assert not (directory / filename).exists()
        assert not orphan.exists()
        assert not link.is_symlink()
        assert outside.read_bytes() == b"Outside temporary storage"
        assert (nested / "keep").is_file()
        assert (directory / "README.txt").is_file()
        assert restarted.records("speech-input-owners") == []
    finally:
        restarted.close()


def sql_options(tmp_path):
    return {
        "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
        "namespace": "audio",
        "workers": 0,
    }


def test_sql_restart_preserves_queued_legacy_recording_and_executes_it_once(tmp_path):
    options, calls = sql_options(tmp_path), []
    producer = StudioService(tmp_path / "workspace", state_options=options)
    run = recording(producer)
    owner = producer.records("speech-input-owners")[0]
    # Old owner records have no run ID; the durable job remains authoritative.
    owner.pop("run_id")
    producer._write(producer._path("speech-input-owners", owner["filename"].split(".")[0]), owner)
    source = producer.speech.directory / owner["filename"]
    assert source.stat().st_mode & 0o777 == 0o600
    producer.close()
    assert source.exists()

    def transcribe(**kwargs):
        calls.append(kwargs["file"].read())
        return SimpleNamespace(text="Recovered transcript")

    consumer = StudioService(
        tmp_path / "workspace",
        state_options=options,
        speech_options={
            "client_factory": lambda *_: SimpleNamespace(
                audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe)),
                close=lambda: None,
            )
        },
    )
    try:
        assert source.exists()
        job = consumer.sql_state.claim(60)
        consumer._execute(job["run_id"], job["payload"], job["session_id"])
        consumer.sql_state.finish(job["run_id"], job["token"])
        consumer.speech.cleanup()
        assert consumer.run(run["id"])["output"] == "Recovered transcript"
        assert calls == [b"Synthetic audio"]
        assert not source.exists()
        assert consumer.records("speech-input-owners") == []
    finally:
        consumer.close()


def test_second_sql_process_preserves_leased_output_until_interruption(tmp_path):
    options = sql_options(tmp_path)
    producer = StudioService(tmp_path / "workspace", state_options=options)
    try:
        run = producer.speech.queue({"profile": "openai-speech", "text": "Queued synthesis"}, None)
        producer.sql_state.claim(60)
        output = producer.speech.directory / (uuid.uuid4().hex + ".wav")
        output.write_bytes(b"Output still being generated")
        producer._write(
            producer._path("speech-input-owners", output.stem),
            {"filename": output.name, "owner_id": "desktop", "run_id": run["id"]},
        )
        consumer = StudioService(tmp_path / "workspace", state_options=options)
        try:
            assert output.exists()
            # A terminal run receipt alone does not release an active worker's file.
            producer._update_run(run["id"], status="cancelled")
            consumer.speech.cleanup()
            assert output.exists()
            with producer.sql_state.transaction() as connection:
                connection.execute(producer.sql_state.jobs.update().values(expires=0))
            assert producer.sql_state.reap() == 1
            consumer.speech.cleanup()
            assert not output.exists()
            assert consumer.records("speech-input-owners") == []
        finally:
            consumer.close()
    finally:
        producer.close()


def test_failed_owner_registration_removes_partially_written_audio(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    original = studio._write
    try:

        def fail_owner(path, value):
            if "speech-input-owners" in str(path):
                raise OSError("Synthetic storage failure")
            return original(path, value)

        monkeypatch.setattr(studio, "_write", fail_owner)
        with pytest.raises(OSError, match="Synthetic storage failure"):
            recording(studio)
        assert list(studio.speech.directory.iterdir()) == []
        assert studio.records("runs") == []
    finally:
        studio.close()


def test_collector_waits_for_input_registration_and_preserves_accepted_job(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    registered, collected = threading.Event(), threading.Event()
    original = studio.start_run
    errors = []
    try:
        hold(studio, monkeypatch)

        def delayed(payload):
            registered.set()
            assert not collected.wait(0.05)
            return original(payload)

        monkeypatch.setattr(studio, "start_run", delayed)

        def collect():
            registered.wait(2)
            try:
                studio.speech.cleanup()
            except Exception as exc:
                errors.append(exc)
            finally:
                collected.set()

        collector = threading.Thread(target=collect)
        collector.start()
        run = recording(studio)
        collector.join(timeout=2)
        assert collected.is_set() and not errors
        assert len(list(studio.speech.directory.iterdir())) == 1
        studio.cancel_run(run["id"])
        assert list(studio.speech.directory.iterdir()) == []
    finally:
        studio.close()


@pytest.mark.parametrize("durable", [False, True])
def test_real_process_exit_collects_orphans_and_preserves_only_durable_work(tmp_path, durable):
    options = sql_options(tmp_path) if durable else None
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import base64, json, os, sys, uuid
from pathlib import Path
from nexus.core.studio import StudioService
studio = StudioService(Path(sys.argv[1]), state_options=json.loads(sys.argv[2]))
if not studio.sql_state:
    studio.executor.submit = lambda *args: None
studio.speech.queue({"profile": "openai-transcribe", "audio_base64": base64.b64encode(b"Crash fixture").decode()}, None)
(studio.speech.directory / (uuid.uuid4().hex + ".wav")).write_bytes(b"Interrupted local output")
os._exit(23)
""",
            str(tmp_path / "workspace"),
            json.dumps(options),
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert child.returncode == 23, child.stderr.decode()
    studio = StudioService(tmp_path / "workspace", state_options=options)
    try:
        run = studio.records("runs")[0]
        assert run["status"] == ("queued" if durable else "interrupted")
        assert len(list(studio.speech.directory.iterdir())) == (1 if durable else 0)
        if durable:
            studio.cancel_run(run["id"])
        assert studio.records("speech-input-owners") == []
        assert list(studio.speech.directory.iterdir()) == []
    finally:
        studio.close()


def test_filename_collision_never_removes_an_existing_recording(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    try:
        hold(studio, monkeypatch)
        recording(studio)
        original = next(studio.speech.directory.iterdir())
        monkeypatch.setattr(
            "nexus.core.studio_speech.uuid.uuid4", lambda: SimpleNamespace(hex=original.stem)
        )
        with pytest.raises(FileExistsError):
            recording(studio)
        assert original.read_bytes() == b"Synthetic audio"
        assert len(studio.records("speech-input-owners")) == 1
    finally:
        studio.close()


def test_worker_reaps_expired_recording_without_calling_provider(tmp_path):
    options = sql_options(tmp_path)
    producer = StudioService(tmp_path / "workspace", state_options=options)
    run = recording(producer)
    producer.sql_state.claim(60)
    with producer.sql_state.transaction() as connection:
        connection.execute(producer.sql_state.jobs.update().values(expires=0))
    producer.close()
    consumer = StudioService(
        tmp_path / "workspace",
        state_options={**options, "workers": 1},
        speech_options={
            "client_factory": lambda *_: pytest.fail("Expired audio must not be replayed")
        },
    )
    try:
        deadline = time.monotonic() + 3
        while consumer.records("speech-input-owners") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert consumer.run(run["id"])["status"] == "interrupted"
        assert consumer.records("speech-input-owners") == []
        assert list(consumer.speech.directory.iterdir()) == []
    finally:
        consumer.close()


def test_recording_cannot_be_rebound_through_generic_runs(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    try:
        pending = hold(studio, monkeypatch)
        accepted = recording(studio)
        with pytest.raises(ValueError, match="already belongs to an accepted job"):
            studio.start_run(dict(pending[0][2]))
        assert len(studio.records("runs")) == 1
        assert len(pending) == 1
        assert len(list(studio.speech.directory.iterdir())) == 1
        assert studio.records("speech-input-owners")[0]["run_id"] == accepted["id"]
    finally:
        studio.close()

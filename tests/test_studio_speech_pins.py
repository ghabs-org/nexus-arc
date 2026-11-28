"""Queued voice settings stay fixed; credentials and execution permission stay live."""

import base64
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_speech import DEFAULT_PROFILES
from tests.test_studio_speech import wait
from tests.test_studio_team import team as team


def hold(service, monkeypatch):
    pending = []
    monkeypatch.setattr(service.executor, "submit", lambda *args: pending.append(args))
    return pending


def client(calls):
    return SimpleNamespace(
        audio=SimpleNamespace(
            speech=SimpleNamespace(
                create=lambda **kwargs: (
                    calls.append(kwargs) or SimpleNamespace(content=b"ID3pinned-speech")
                )
            )
        ),
        close=lambda: None,
    )


def test_changed_and_removed_profile_never_changes_accepted_job(tmp_path, monkeypatch):
    calls, settings = [], []

    def factory(profile, payload):
        settings.append(deepcopy(profile))
        return client(calls)

    studio = StudioService(tmp_path, speech_options={"client_factory": factory})
    try:
        pending = hold(studio, monkeypatch)
        run = studio.speech.queue(
            {"profile": "openai-speech", "text": "Queued", "voice": "nova"}, None
        )
        callback, identity, payload, session = pending.pop()
        original = deepcopy(payload["_speech_profile_snapshot"])
        studio.speech.profiles["openai-speech"].update(
            model="changed-model", token_env="CHANGED_KEY"
        )
        studio.speech.profiles["openai-speech"]["voices"].clear()
        del studio.speech.profiles["openai-speech"]
        callback(identity, payload, session)
        completed = studio.run(run["id"])
        assert completed["status"] == "completed", completed
        assert calls[0]["model"] == "tts-1" and calls[0]["voice"] == "nova"
        assert settings[0]["token_env"] == "OPENAI_API_KEY"
        assert payload["_speech_profile_snapshot"] == original
        assert completed["speech_profile_revision"] == original["digest"]
        assert len(completed["speech_profile_revision"]) == 64
        assert "settings" not in completed and "token_env" not in json.dumps(completed)
        assert DEFAULT_PROFILES["openai-speech"]["voices"]
    finally:
        studio.close()


def test_client_snapshot_is_overwritten_at_shared_start_run_boundary(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    try:
        pending = hold(studio, monkeypatch)
        run = studio.start_run(
            {
                "kind": "speech",
                "task": "Speak",
                "speech_profile": "openai-speech",
                "_speech_profile_snapshot": {
                    "settings": {"kind": "whisper", "token": "PRIVATE_VALUE"}
                },
            }
        )
        payload = pending[0][2]
        assert payload["_speech_profile_snapshot"]["settings"]["kind"] == "openai"
        assert "PRIVATE_VALUE" not in json.dumps(payload)
        assert run["speech_action"] == "speak"
        studio.start_run(
            {
                "kind": "chat",
                "task": "Hello",
                "_speech_profile_snapshot": {"token": "PRIVATE_VALUE"},
            }
        )
        assert "_speech_profile_snapshot" not in pending[1][2]
    finally:
        studio.close()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop("_speech_profile_snapshot"),
        lambda payload: payload["_speech_profile_snapshot"].update(digest="invalid"),
        lambda payload: payload["_speech_profile_snapshot"]["settings"].update(model="changed"),
        lambda payload: payload["_speech_profile_snapshot"].update(format="unknown"),
    ],
)
def test_legacy_or_corrupt_jobs_fail_before_provider_and_clean_owned_input(
    tmp_path, monkeypatch, mutation
):
    studio = StudioService(
        tmp_path, speech_options={"client_factory": lambda *_: pytest.fail("provider called")}
    )
    try:
        pending = hold(studio, monkeypatch)
        run = studio.speech.queue(
            {"profile": "openai-transcribe", "audio_base64": base64.b64encode(b"input").decode()},
            None,
        )
        callback, identity, payload, session = pending[0]
        mutation(payload)
        callback(identity, payload, session)
        completed = studio.run(run["id"])
        assert completed["status"] == "failed"
        assert "review and submit a new request" in completed["error"]
        assert list(studio.speech.directory.iterdir()) == []
        assert studio.records("speech-input-owners") == []
    finally:
        studio.close()


def test_relative_local_model_path_is_pinned_before_working_directory_changes(
    tmp_path, monkeypatch
):
    folder = tmp_path / "models"
    folder.mkdir()
    (folder / "fixture.pt").write_bytes(b"synthetic weights")
    monkeypatch.chdir(folder)
    studio = StudioService(
        tmp_path / "workspace",
        speech_options={
            "profiles": {
                "selected": {"kind": "whisper", "action": "transcribe", "model_path": "fixture.pt"}
            },
            "client_factory": lambda *_: pytest.fail("local profile switched to API"),
        },
    )
    try:
        pending = hold(studio, monkeypatch)
        run = studio.speech.queue(
            {"profile": "selected", "audio_base64": base64.b64encode(b"input").decode()}, None
        )
        monkeypatch.chdir(tmp_path)
        studio.speech.profiles["selected"] = {
            "kind": "openai",
            "action": "transcribe",
            "model": "changed",
        }
        commands = []

        def process(command, **kwargs):
            commands.append(command)
            return SimpleNamespace(stdout=b'{"text":"Local transcript"}')

        monkeypatch.setattr("nexus.core.studio_speech.subprocess.run", process)
        pending[0][0](*pending[0][1:])
        assert studio.run(run["id"])["output"] == "Local transcript"
        assert commands[0][3] == str((folder / "fixture.pt").resolve())
        assert list(studio.speech.directory.iterdir()) == []
    finally:
        studio.close()


def test_credential_rotation_uses_current_value_of_pinned_reference(tmp_path, monkeypatch):
    credentials, calls = [], []

    def openai(**kwargs):
        credentials.append(kwargs["api_key"])
        return client(calls)

    monkeypatch.setattr("openai.OpenAI", openai)
    monkeypatch.setenv("SPEECH_PIN_KEY", "old-value")
    studio = StudioService(
        tmp_path,
        speech_options={
            "profiles": {
                "selected": {
                    "kind": "openai",
                    "action": "speak",
                    "model": "tts-1",
                    "token_env": "SPEECH_PIN_KEY",
                    "voices": ["nova"],
                }
            }
        },
    )
    try:
        pending = hold(studio, monkeypatch)
        run = studio.speech.queue({"profile": "selected", "text": "Hello"}, None)
        monkeypatch.setenv("SPEECH_PIN_KEY", "rotated-value")
        studio.speech.profiles["selected"]["token_env"] = "OTHER_KEY"
        pending[0][0](*pending[0][1:])
        assert studio.run(run["id"])["status"] == "completed"
        assert credentials == ["rotated-value"]
        assert "rotated-value" not in json.dumps(pending[0][2])
        assert "old-value" not in json.dumps(pending[0][2])
    finally:
        studio.close()


def test_removed_credential_fails_without_selecting_another_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("SPEECH_REVOKED_KEY", raising=False)
    monkeypatch.setenv("OTHER_KEY", "unrelated-valid-credential")
    monkeypatch.setattr("openai.OpenAI", lambda **_: pytest.fail("credential fallback"))
    studio = StudioService(
        tmp_path,
        speech_options={
            "profiles": {
                "selected": {
                    "kind": "openai",
                    "action": "speak",
                    "model": "tts-1",
                    "token_env": "SPEECH_REVOKED_KEY",
                    "voices": ["nova"],
                }
            }
        },
    )
    try:
        pending = hold(studio, monkeypatch)
        run = studio.speech.queue({"profile": "selected", "text": "Hello"}, None)
        studio.speech.profiles["selected"]["token_env"] = "OTHER_KEY"
        pending[0][0](*pending[0][1:])
        assert studio.run(run["id"])["status"] == "failed"
        assert "speech API credentials" in studio.run(run["id"])["error"]
    finally:
        studio.close()


@pytest.mark.parametrize(
    "settings",
    [
        {"kind": "openai", "action": "speak", "model": "tts-1", "api_key": "SECRET_VALUE"},
        {"kind": "openai", "action": "speak", "model": "tts-1", "token_env": "not a reference"},
        {"kind": "openai", "action": "speak", "model": "tts-1", "voices": "nova"},
        {"kind": "whisper", "action": "speak", "model_path": ""},
    ],
)
def test_invalid_or_secret_bearing_profiles_reject_before_queue(tmp_path, settings):
    studio = StudioService(tmp_path, speech_options={"profiles": {"selected": settings}})
    try:
        with pytest.raises(ValueError):
            studio.speech.queue({"profile": "selected", "text": "Hello"}, None)
        assert studio.records("runs") == []
    finally:
        studio.close()


def test_sql_queued_profile_survives_restart_and_profile_removal(tmp_path):
    options = {
        "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
        "namespace": "speech-pins",
        "workers": 0,
    }
    profiles = {
        "selected": {
            "kind": "openai",
            "action": "speak",
            "model": "pinned-model",
            "token_env": "PINNED_KEY",
            "voices": ["nova"],
        }
    }
    producer = StudioService(
        tmp_path / "workspace", state_options=options, speech_options={"profiles": profiles}
    )
    run = producer.speech.queue({"profile": "selected", "text": "Queued before restart"}, None)
    producer.close()
    calls, settings = [], []

    def factory(profile, payload):
        settings.append(profile)
        return client(calls)

    consumer = StudioService(
        tmp_path / "workspace",
        state_options={**options, "workers": 1},
        speech_options={"client_factory": factory},
    )
    try:
        assert "selected" not in consumer.speech.profiles
        completed = wait(consumer, run)
        assert completed["status"] == "completed", completed
        assert calls[0]["model"] == "pinned-model"
        assert settings[0]["token_env"] == "PINNED_KEY"
        assert consumer.catalog.get("artifacts", completed["speech_artifact_id"])
    finally:
        consumer.close()


def test_speech_http_keeps_existing_team_permissions_and_private_settings(team, monkeypatch):
    studio, _, _, request, _ = team
    pending = hold(studio, monkeypatch)
    payload = {
        "profile": "openai-speech",
        "text": "Hello",
        "voice": "nova",
        "_speech_profile_snapshot": {"settings": {"token_env": "CLIENT_CREDENTIAL"}},
    }
    assert request("/api/studio/speech", payload)[0] in {401, 403}
    assert request("/api/studio/speech", payload, user="viewer")[0] == 403
    status, run = request("/api/studio/speech", payload, user="alice")
    assert status == 202
    assert run["speech_action"] == "speak" and run["speech_profile"] == "openai-speech"
    assert len(run["speech_profile_revision"]) == 64
    assert "OPENAI_API_KEY" not in json.dumps(run)
    assert pending[0][2]["_speech_profile_snapshot"]["settings"]["token_env"] == "OPENAI_API_KEY"
    assert request("/api/runs/" + run["id"], user="bob")[0] == 403
    assert request("/api/studio/speech", {"profile": []}, user="alice")[0] == 400

import base64
import time
from types import SimpleNamespace

import pytest

from nexus.core.studio import StudioService


def wait(studio, run):
    for _ in range(500):
        record = studio.run(run["id"])
        if record["status"] not in {"running", "queued"}:
            return record
        time.sleep(0.01)
    pytest.fail("Speech job did not finish")


def test_api_speech_uses_explicit_profile_preserves_private_artifact_and_cleans_input(tmp_path):
    calls = []

    class Client:
        def __init__(self):
            self.audio = SimpleNamespace(
                transcriptions=SimpleNamespace(create=self.transcribe),
                speech=SimpleNamespace(create=self.speak),
            )

        def transcribe(self, **kwargs):
            calls.append(("transcribe", kwargs["file"].read()))
            return SimpleNamespace(text="Review this transcript")

        def speak(self, **kwargs):
            calls.append(("speak", kwargs["input"]))
            return SimpleNamespace(content=b"ID3fixture-audio")

        def close(self):
            calls.append(("close", None))

    service = StudioService(
        tmp_path, speech_options={"client_factory": lambda profile, payload: Client()}
    )
    try:
        speech = wait(
            service, service.speech.queue({"profile": "openai-speech", "text": "Hello"}, None)
        )
        assert speech["status"] == "completed", speech
        artifact = service.catalog.get("artifacts", speech["speech_artifact_id"])
        assert service.artifacts.preview(artifact)["kind"] == "audio"
        transcript = wait(
            service,
            service.speech.queue(
                {
                    "profile": "openai-transcribe",
                    "audio_base64": base64.b64encode(b"fixture audio").decode(),
                    "format": "wav",
                },
                None,
            ),
        )
        assert transcript["output"] == "Review this transcript"
        assert list(service.speech.directory.iterdir()) == []
        assert service.records("speech-input-owners") == []
        assert [name for name, _ in calls] == ["speak", "close", "transcribe", "close"]
    finally:
        service.close()


def test_missing_local_model_never_downloads_or_falls_back_to_paid_api(tmp_path):
    service = StudioService(
        tmp_path, speech_options={"client_factory": lambda *_: pytest.fail("must not call API")}
    )
    try:
        run = wait(
            service,
            service.speech.queue(
                {
                    "profile": "local-whisper",
                    "audio_base64": base64.b64encode(b"fixture audio").decode(),
                },
                None,
            ),
        )
        assert run["status"] == "failed" and "automatic model download is disabled" in run["error"]
        assert list(service.speech.directory.iterdir()) == []
        with pytest.raises(ValueError, match="voice"):
            service.speech.queue(
                {"profile": "openai-speech", "text": "Hello", "voice": "unknown"}, None
            )
    finally:
        service.close()

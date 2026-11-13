"""Exercise real installed local speech models through ordinary Studio jobs.

Supply model paths and make the installed Piper executable available on PATH.
This example never downloads models, invokes an API or opens a microphone.
"""

import argparse
import base64
import io
import json
import tempfile
import time
import wave
from pathlib import Path

from nexus.core.studio import StudioService

TEXT = "Studio can run local speech without an external account."


def wait(studio, run, seconds=150):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = studio.run(run["id"])
        if current["status"] not in {"queued", "running"}:
            if current["status"] != "completed":
                raise RuntimeError("Local voice job failed: " + str(current.get("error")))
            return current
        time.sleep(0.05)
    raise TimeoutError("Local voice acceptance exceeded its wait budget")


def exercise(whisper_model, piper_model):
    def no_api(*_):
        raise AssertionError("Local speech must not construct a remote API client")

    with tempfile.TemporaryDirectory(prefix="nexus-local-voice-") as temporary:
        studio = StudioService(
            Path(temporary),
            speech_options={
                "profiles": {
                    "fixture-whisper": {
                        "kind": "whisper",
                        "action": "transcribe",
                        "model_path": str(Path(whisper_model).resolve()),
                    },
                    "fixture-piper": {
                        "kind": "piper",
                        "action": "speak",
                        "model_path": str(Path(piper_model).resolve()),
                    },
                },
                "client_factory": no_api,
            },
        )
        try:
            ready = {row["id"]: row for row in studio.speech.available()["items"]}
            for name in ("fixture-whisper", "fixture-piper"):
                if not ready[name]["model_ready"]:
                    raise RuntimeError(
                        "Missing local prerequisites: "
                        + ", ".join(ready[name]["missing_prerequisites"])
                    )
            started = time.monotonic()
            spoken = wait(
                studio, studio.speech.queue({"profile": "fixture-piper", "text": TEXT}, None)
            )
            synthesis_seconds = round(time.monotonic() - started, 3)
            artifact = studio.catalog.get("artifacts", spoken["speech_artifact_id"])
            content = studio.artifacts.get(artifact)
            assert studio.artifacts.preview(artifact)["kind"] == "audio"
            assert artifact.get("owner_id") is None and artifact["data"]["filename"].endswith(
                ".wav"
            )
            with wave.open(io.BytesIO(content)) as audio:
                assert audio.getnchannels() == 1 and audio.getsampwidth() == 2
                duration = audio.getnframes() / audio.getframerate()
                assert 1 < duration < 30
                assert any(audio.readframes(audio.getnframes()))
            assert not list(studio.speech.directory.iterdir())
            assert studio.records("speech-input-owners") == []
            started = time.monotonic()
            transcript = wait(
                studio,
                studio.speech.queue(
                    {
                        "profile": "fixture-whisper",
                        "language": "en",
                        "format": "wav",
                        "audio_base64": base64.b64encode(content).decode(),
                    },
                    None,
                ),
            )
            transcription_seconds = round(time.monotonic() - started, 3)
            text = transcript["output"].strip()
            assert all(word in text.lower() for word in ("local", "speech", "account")), text
            assert (
                spoken["speech_profile"] == "fixture-piper"
                and transcript["speech_profile"] == "fixture-whisper"
            )
            assert not list(studio.speech.directory.iterdir())
            assert studio.records("speech-input-owners") == []
            result = {
                "synthesis_completed": True,
                "transcription_completed": True,
                "generated_wav_bytes": len(content),
                "audio_seconds": round(duration, 3),
                "transcript": text,
                "synthesis_seconds": synthesis_seconds,
                "transcription_seconds": transcription_seconds,
                "temporary_audio_removed": True,
                "private_audio_artifact": True,
                "api_client_used": False,
                "microphone_used": False,
                "model_downloaded_by_worker": False,
            }
        finally:
            studio.close()
        assert not list(studio.speech.directory.iterdir())
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whisper-model", required=True)
    parser.add_argument("--piper-model", required=True)
    arguments = parser.parse_args()
    print(json.dumps(exercise(arguments.whisper_model, arguments.piper_model), indent=2))

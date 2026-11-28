"""Windows real-model cancellation/shutdown after observed native inference.

Supply already prepared models. No downloads, microphone or API clients are used.
Test-only model hooks mark completed inference output, without blocking the model.
"""

import argparse
import base64
import io
import json
import sys
import tempfile
import time
import wave
from pathlib import Path
from unittest.mock import patch

from studio_local_speech import TEXT, wait
from studio_windows_speech_lifecycle import child_handle, removed

from nexus.core import studio_speech
from nexus.core.studio import StudioService

FIXTURE = Path(__file__).resolve().parents[3] / "tests/fixtures/studio_voice_interrupt_worker.py"


def service(root, whisper, piper):
    def no_api(*_):
        raise AssertionError("Interruption acceptance must not use a speech API")

    return StudioService(
        root,
        speech_options={
            "client_factory": no_api,
            "profiles": {
                "whisper": {
                    "kind": "whisper",
                    "action": "transcribe",
                    "model_path": str(Path(whisper).resolve()),
                },
                "piper": {
                    "kind": "piper",
                    "action": "speak",
                    "model_path": str(Path(piper).resolve()),
                },
            },
        },
    )


def long_recording(content):
    with wave.open(io.BytesIO(content)) as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        frames = source.readframes(source.getnframes())
    size = 60 * rate * channels * width
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setframerate(rate)
        target.setnchannels(channels)
        target.setsampwidth(width)
        target.writeframes((frames * (size // len(frames) + 1))[:size])
    result = output.getvalue()
    assert len(result) <= 4 * 1024 * 1024
    return base64.b64encode(result).decode()


def exercise(whisper, piper):
    if sys.platform != "win32":
        raise RuntimeError("Actual Windows required; another host is not native acceptance")
    results = {}
    original_process = studio_speech.local_process
    with tempfile.TemporaryDirectory(prefix="nexus-real-voice-stop-") as temporary:
        root = Path(temporary)
        seed = service(root / "seed", whisper, piper)
        try:
            spoken = wait(seed, seed.speech.queue({"profile": "piper", "text": TEXT}, None))
            artifact = seed.catalog.get("artifacts", spoken["speech_artifact_id"])
            audio = long_recording(seed.artifacts.get(artifact))
        finally:
            seed.close()
        for kind in ("piper", "whisper"):
            for action in ("cancel", "shutdown"):
                case = f"{kind}_{action}"
                ready = root / (case + ".ready")
                studio = service(root / case, whisper, piper)
                observed = None
                closed = False

                async def model_process(command, _kind=kind, _ready=ready, **options):
                    arguments = command[-3:] if _kind == "whisper" else command[1:]
                    return await original_process(
                        [sys.executable, str(FIXTURE), _kind, str(_ready), *arguments], **options
                    )

                try:
                    with patch.object(studio_speech, "local_process", model_process):
                        request = (
                            {"profile": "piper", "text": (TEXT + " ") * 40}
                            if kind == "piper"
                            else {
                                "profile": "whisper",
                                "audio_base64": audio,
                                "format": "wav",
                                "language": "en",
                            }
                        )
                        run = studio.speech.queue(request, None)
                        deadline = time.monotonic() + 30
                        while not ready.exists():
                            record = studio.run(run["id"])
                            if record["status"] not in {"queued", "running"}:
                                raise AssertionError(
                                    f"Model ended before observed inference: {record.get('error')}"
                                )
                            if time.monotonic() >= deadline:
                                raise AssertionError("Real model did not reach inference")
                            time.sleep(0.01)
                        observed = child_handle(ready)
                        api, handle = observed
                        assert api.WaitForSingleObject(handle, 0) == 258, (
                            "Model already exited before interruption"
                        )
                        assert studio.run(run["id"])["status"] == "running"
                        started = time.monotonic()
                        if action == "shutdown":
                            studio.close()
                            closed = True
                        else:
                            studio.cancel_run(run["id"])
                        deadline = time.monotonic() + 8
                        while studio.run(run["id"])["status"] in {"running", "queued"}:
                            if time.monotonic() >= deadline:
                                raise AssertionError("Real-model interruption did not settle")
                            time.sleep(0.01)
                        identity, observed = observed, None
                        removed(*identity)
                        status = studio.run(run["id"])["status"]
                        assert status == ("interrupted" if action == "shutdown" else "cancelled")
                        assert not list(studio.speech.directory.iterdir())
                        assert studio.records("speech-input-owners") == []
                        assert studio.catalog.list("artifacts") == []
                        results[case] = {
                            "real_inference_observed": True,
                            "model_process_exited": True,
                            "status": status,
                            "temporary_audio_removed": True,
                            "owner_records_removed": True,
                            "artifacts_created": 0,
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                        }
                finally:
                    if not closed:
                        studio.close()
                    if observed is not None:
                        removed(*observed)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--whisper-model", required=True)
    parser.add_argument("--piper-model", required=True)
    arguments = parser.parse_args()
    print(json.dumps(exercise(arguments.whisper_model, arguments.piper_model), sort_keys=True))

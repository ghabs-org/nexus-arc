"""Exercise pinned voice jobs through the real SDK against a local simulation.

Start postman/simulations/studio-speech.sim.yaml on port 49832, then run:
PYTHONPATH=. python examples/nexus-bot/src/studio_speech_pins.py
Only synthetic audio, temporary state and a synthetic API credential are used.
"""

import argparse
import base64
import json
import tempfile
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from nexus.core.studio import StudioService
from nexus.core.studio_speech import DEFAULT_PROFILES


def exercise(base="http://127.0.0.1:49832/speech"):
    import httpx
    from openai import OpenAI

    destination = urlparse(base)
    if destination.scheme != "http" or destination.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("This synthetic example requires a loopback HTTP simulation")
    requests = []

    def observed(request):
        requests.append((request.url.path, request.read()))

    def factory(profile, payload):
        return OpenAI(
            api_key="synthetic-speech-key",
            base_url=base.rstrip("/") + "/v1",
            max_retries=0,
            timeout=3,
            http_client=httpx.Client(
                trust_env=False, follow_redirects=False, event_hooks={"request": [observed]}
            ),
        )

    with tempfile.TemporaryDirectory(prefix="nexus-speech-pins-") as temporary:
        studio = StudioService(
            Path(temporary).resolve(), speech_options={"client_factory": factory}
        )
        original_submit = studio.executor.submit
        pending = []
        studio.executor.submit = lambda *args: pending.append(args)
        try:
            spoken = studio.speech.queue(
                {"profile": "openai-speech", "voice": "nova", "text": "Queued voice"}, None
            )
            studio.speech.profiles["openai-speech"]["model"] = "changed-model"
            del studio.speech.profiles["openai-speech"]
            callback, identity, payload, session = pending.pop()
            callback(identity, payload, session)
            completed = studio.run(spoken["id"])
            assert completed["status"] == "completed", completed
            artifact = studio.catalog.get("artifacts", completed["speech_artifact_id"])
            assert studio.artifacts.get(artifact) == b"ID3synthetic-speech"
            sent = json.loads(requests[-1][1])
            assert sent["model"] == "tts-1" and sent["voice"] == "nova"

            transcript = studio.speech.queue(
                {
                    "profile": "openai-transcribe",
                    "format": "wav",
                    "audio_base64": base64.b64encode(b"synthetic audio").decode(),
                },
                None,
            )
            del studio.speech.profiles["openai-transcribe"]
            callback, identity, payload, session = pending.pop()
            callback(identity, payload, session)
            assert (
                studio.run(transcript["id"])["output"]
                == "Pinned transcription from synthetic audio"
            )
            assert b"whisper-1" in requests[-1][1]
            assert list(studio.speech.directory.iterdir()) == []

            studio.speech.profiles["openai-speech"] = deepcopy(DEFAULT_PROFILES["openai-speech"])
            failed = studio.speech.queue(
                {"profile": "openai-speech", "voice": "nova", "text": "fixture:failure"}, None
            )
            callback, identity, payload, session = pending.pop()
            callback(identity, payload, session)
            record = studio.run(failed["id"])
            assert record["status"] == "failed"
            assert "PRIVATE_PROVIDER_DETAIL" not in record["error"]
            assert len(requests) == 3, "Speech failure must not retry automatically"
            return {
                "sdk_http_requests": len(requests),
                "pinned_model_and_voice": True,
                "transcription_cleaned": True,
                "automatic_retries": 0,
                "live_verified": False,
            }
        finally:
            studio.executor.submit = original_submit
            studio.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49832/speech")
    args = parser.parse_args()
    print(json.dumps(exercise(args.base)))


if __name__ == "__main__":
    main()

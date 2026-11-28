"""Keep queued recordings across restart and remove abandoned temporary audio.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_speech_cleanup.py
Only a temporary SQLite workspace and an injected synthetic provider are used.
"""

import base64
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from nexus.core.studio import StudioService


def exercise():
    with tempfile.TemporaryDirectory(prefix="nexus-voice-cleanup-") as temporary:
        root = Path(temporary)
        options = {
            "url": "sqlite:///" + str(root / "state.sqlite"),
            "namespace": "voice-example",
            "workers": 0,
        }
        producer = StudioService(root / "workspace", state_options=options)
        run = producer.speech.queue(
            {
                "profile": "openai-transcribe",
                "audio_base64": base64.b64encode(b"Synthetic recording").decode(),
            },
            None,
        )
        directory = producer.speech.directory
        orphan = directory / (uuid.uuid4().hex + ".wav")
        orphan.write_bytes(b"Abandoned generated audio")
        assert producer.speech.cleanup()["files"] == 1
        assert not orphan.exists() and len(list(directory.iterdir())) == 1
        producer.close()
        assert len(list(directory.iterdir())) == 1
        calls = []

        def transcribe(**kwargs):
            calls.append(kwargs["file"].read())
            return SimpleNamespace(text="Recovered recording")

        consumer = StudioService(
            root / "workspace",
            state_options={**options, "workers": 1},
            speech_options={
                "client_factory": lambda *_: SimpleNamespace(
                    audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe)),
                    close=lambda: None,
                )
            },
        )
        try:
            deadline = time.monotonic() + 5
            while consumer.run(run["id"])["status"] in {"queued", "running"}:
                if time.monotonic() > deadline:
                    raise TimeoutError("Synthetic transcription did not finish")
                time.sleep(0.01)
            assert consumer.run(run["id"])["output"] == "Recovered recording"
        finally:
            consumer.close()
        assert calls == [b"Synthetic recording"]
        assert list(directory.iterdir()) == []
        print("Queued audio survived restart, ran once, and left no temporary files.")


if __name__ == "__main__":
    exercise()

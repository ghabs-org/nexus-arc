"""Opt-in real local speech roundtrip; missing prerequisites fail, never skip."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_real_piper_and_whisper_jobs_keep_audio_private_and_remove_temporary_files():
    script = Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_local_speech.py"
    completed = subprocess.run(
        [
            os.environ.get("NEXUS_ACCEPTANCE_SPEECH_PYTHON", sys.executable),
            str(script),
            "--whisper-model",
            os.environ["NEXUS_ACCEPTANCE_WHISPER_MODEL"],
            "--piper-model",
            os.environ["NEXUS_ACCEPTANCE_PIPER_MODEL"],
        ],
        capture_output=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-2000:]
    result = json.loads(completed.stdout)
    assert result["synthesis_completed"] and result["transcription_completed"]
    assert result["temporary_audio_removed"] and result["private_audio_artifact"]
    assert not result["api_client_used"] and not result["microphone_used"]
    assert not result["model_downloaded_by_worker"]

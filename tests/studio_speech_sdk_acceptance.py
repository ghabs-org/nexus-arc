"""Opt-in real speech SDK acceptance against the local Postman simulation."""

import os
import runpy
from pathlib import Path


def test_speech_sdk_uses_pinned_settings_and_never_retries_failed_calls():
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_speech_pins.py")
    )
    result = example["exercise"](
        os.environ.get("NEXUS_ACCEPTANCE_SPEECH_URL", "http://127.0.0.1:49832/speech")
    )
    assert result == {
        "sdk_http_requests": 3,
        "pinned_model_and_voice": True,
        "transcription_cleaned": True,
        "automatic_retries": 0,
        "live_verified": False,
    }

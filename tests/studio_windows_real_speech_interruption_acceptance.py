"""Opt-in actual Windows model interruption; no missing prerequisite skips."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def test_actual_piper_whisper_cancel_and_shutdown_after_inference(request):
    assert sys.platform == "win32", "Actual Windows required"
    root = Path(__file__).resolve().parents[1]
    script = root / "examples/nexus-bot/src/studio_real_speech_interruption.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--whisper-model",
            os.environ["NEXUS_ACCEPTANCE_WHISPER_MODEL"],
            "--piper-model",
            os.environ["NEXUS_ACCEPTANCE_PIPER_MODEL"],
        ],
        capture_output=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-4000:]
    outcomes = json.loads(result.stdout)
    assert set(outcomes) == {"piper_cancel", "piper_shutdown", "whisper_cancel", "whisper_shutdown"}
    assert all(
        case["real_inference_observed"] and case["model_process_exited"]
        for case in outcomes.values()
    )
    assert all(
        case["temporary_audio_removed"]
        and case["owner_records_removed"]
        and case["artifacts_created"] == 0
        for case in outcomes.values()
    )
    request.node.user_properties.append(
        (
            "real_interruption_result",
            json.dumps(
                {
                    "platform": sys.platform,
                    "outcomes": outcomes,
                    "source_sha256": {
                        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                        for name in (
                            "nexus/core/studio.py",
                            "nexus/core/studio_speech.py",
                            "nexus/core/studio_speech_process.py",
                            "nexus/core/studio_speech_windows.py",
                            "nexus/core/studio_speech_worker.py",
                            "examples/nexus-bot/src/studio_real_speech_interruption.py",
                            "tests/fixtures/studio_voice_interrupt_worker.py",
                            "tests/studio_windows_real_speech_interruption_acceptance.py",
                        )
                    },
                },
                sort_keys=True,
            ),
        )
    )

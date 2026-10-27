"""Opt-in actual Linux Piper/Whisper jobs; no network, microphone or cloud API."""

import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from nexus.core.studio_container_recovery import launch_environment

MODELS = {
    "tiny.en.pt": "d3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03",
    "en_US-lessac-medium.onnx": "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
    "en_US-lessac-medium.onnx.json": "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
}


def test_real_linux_speech_jobs_without_network_or_api(request):
    image = os.environ["NEXUS_ACCEPTANCE_LINUX_SPEECH_IMAGE"]
    context = os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"]
    models = Path(os.environ["NEXUS_ACCEPTANCE_LOCAL_SPEECH_MODELS"]).resolve(strict=True)
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image)
    environment = launch_environment(context)
    name = "nexus-linux-speech-" + uuid.uuid4().hex
    mounts = []
    for filename, digest in MODELS.items():
        path = models / filename
        assert path.is_file() and not path.is_symlink()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        mounts += ["--mount", f"type=bind,source={path},target=/models/{filename},readonly"]
    command = ["docker", "--context", context]
    program = (
        "import hashlib,importlib.metadata as m,json,platform,runpy;from pathlib import Path;"
        "module=runpy.run_path('/checks/studio_local_speech.py');"
        "result=module['exercise']('/models/tiny.en.pt','/models/en_US-lessac-medium.onnx');"
        "result.update(platform=platform.platform(),packages={n:m.version(n) for n in "
        "['openai-whisper','torch','piper-tts','onnxruntime']},"
        "worker_sha256=hashlib.sha256(Path('/checks/nexus/core/studio_speech_worker.py').read_bytes()).hexdigest());"
        "print(json.dumps(result,sort_keys=True))"
    )
    try:
        result = subprocess.run(
            [
                *command,
                "run",
                "--rm",
                "--pull",
                "never",
                "--name",
                name,
                "--label",
                "nexus.studio.validation=" + name,
                "--network",
                "none",
                "--read-only",
                "--user",
                "65534:65534",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                "128",
                "--memory",
                "2g",
                "--cpus",
                "2",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
                *mounts,
                "--entrypoint",
                "python",
                image,
                "-c",
                program,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, result.stderr[-3000:]
        report = json.loads(result.stdout)
        assert report["platform"].startswith("Linux")
        assert report["synthesis_completed"] and report["transcription_completed"]
        assert report["temporary_audio_removed"] and report["private_audio_artifact"]
        assert not report["api_client_used"] and not report["microphone_used"]
        assert not report["model_downloaded_by_worker"]
        current = Path(__file__).resolve().parents[1] / "nexus/core/studio_speech_worker.py"
        assert report["worker_sha256"] == hashlib.sha256(current.read_bytes()).hexdigest()
        request.node.user_properties.append(("result", json.dumps(report, sort_keys=True)))
    finally:
        subprocess.run(
            [*command, "rm", "--force", name], env=environment, capture_output=True, timeout=20
        )
        absent = subprocess.run(
            [*command, "container", "inspect", name],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert absent.returncode != 0 and "no such" in absent.stderr.lower()
        request.node.user_properties.append(("scope_resources_removed", "true"))

"""Explicit real CPU/model acceptance; no automatic download or skipped checks."""

import base64
import importlib.util
import json
import os
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import IMAGE_PROGRAM
from nexus.core.studio_sandbox import StudioSandbox


@pytest.fixture
def profile():
    return {
        "image": os.environ["NEXUS_ACCEPTANCE_VISION_IMAGE"],
        "model_path": os.environ["NEXUS_ACCEPTANCE_VISION_MODEL"],
        "docker_context": os.environ["NEXUS_ACCEPTANCE_DOCKER_CONTEXT"],
        "timeout": 120,
        "memory_mb": 2048,
        "pids_limit": 256,
        "open_files": 256,
    }


def test_object_detection_uses_real_model_and_publishes_private_artifacts(tmp_path, profile):
    path = Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_data_workers.py"
    spec = importlib.util.spec_from_file_location("vision_worker_example", path)
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    studio = StudioService(tmp_path, data_tool_options={"profiles": {"vision": profile}})
    try:
        result = example.exercise_vision(studio, os.environ["NEXUS_ACCEPTANCE_VISION_SAMPLE"])
        labels = {r["label"] for r in result["detections"] if r["confidence"] > 0.5}
        assert {"bus", "person"} <= labels
        for row in result["detections"]:
            assert 0 <= row["confidence"] <= 1
            x1, y1, x2, y2 = row["xyxy"]
            assert 0 <= x1 <= x2 <= 810 and 0 <= y1 <= y2 <= 1080
    finally:
        studio.close()


def test_vision_worker_mounts_only_selected_read_only_model(profile, monkeypatch):
    monkeypatch.setenv("NEXUS_WORKER_HOST_SECRET", "must-not-inherit")
    worker = StudioSandbox({"vision": profile})
    args = worker.arguments("vision", "owned-vision")
    assert args.count("--mount") == 1
    assert (
        args[args.index("--mount") + 1]
        == f"type=bind,source={Path(profile['model_path']).resolve()},destination=/models/model.pt,readonly"
    )
    result = worker.execute(
        """import os,pathlib
assert 'NEXUS_WORKER_HOST_SECRET' not in os.environ
assert sorted(p.name for p in pathlib.Path('/models').iterdir())==['model.pt']
mount=next(line for line in pathlib.Path('/proc/self/mountinfo').read_text().splitlines() if line.split()[4]=='/models/model.pt')
assert 'ro' in mount.split()[5].split(',')
assert not pathlib.Path('/sys/fs/cgroup/memory.max').read_text().strip()=='max'
print('selected read-only model')
""",
        profile="vision",
    )
    assert result["stdout"] == "selected read-only model\n"


def test_invalid_installed_model_fails_without_download_or_host_fallback(tmp_path, profile):
    model = tmp_path / "invalid.pt"
    model.write_bytes(b"Invalid model fixture")
    worker = StudioSandbox({"vision": {**profile, "model_path": str(model)}})
    code = IMAGE_PROGRAM.replace(
        "PARAMETERS", repr(json.dumps({"action": "detect_objects", "filename": "bus.jpg"}))
    )
    files = [
        {
            "filename": "bus.jpg",
            "content_base64": base64.b64encode(
                Path(os.environ["NEXUS_ACCEPTANCE_VISION_SAMPLE"]).read_bytes()
            ).decode(),
        }
    ]
    with pytest.raises(ValueError, match="Analysis worker failed"):
        worker.execute(code, files, profile="vision")

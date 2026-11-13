"""Run all four configured media tools in a private workflow using synthetic data.

PYTHONPATH=. python examples/nexus-bot/src/studio_media.py
No external account is contacted. Install studio-images for image validation.
"""

import base64
import io
import json
import tempfile
import time
import wave
from contextlib import closing
from pathlib import Path

from PIL import Image

from nexus.core.studio import StudioService


def example_image():
    output = io.BytesIO()
    Image.new("RGBA", (2, 2), (0, 128, 255, 128)).save(output, format="PNG")
    return output.getvalue()


def example_audio():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 160)
    return output.getvalue()


def example_profiles(url="http://127.0.0.1:49830/media/v1", token_env=None):
    return {
        action: {
            "action": action,
            "model": "fixture-" + action,
            "enabled": True,
            "url": url,
            "allow_private": True,
            "auth": {"type": "bearer", "token_env": token_env} if token_env else {"type": "none"},
        }
        for action in ("image_generate", "image_edit", "image_describe", "audio_describe")
    }


def exercise(studio):
    source = studio.catalog.save(
        "artifacts",
        {
            "name": "Short synthetic audio",
            "data": {
                "filename": "synthetic.wav",
                "content_base64": base64.b64encode(example_audio()).decode(),
            },
        },
    )
    tools = {n.rsplit(":", 1)[1]: n for n in studio.media.names}
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Private media workflow",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "generate",
                        "kind": "tool",
                        "tool_id": tools["image_generate"],
                        "kwargs": {
                            "profile": "image_generate",
                            "prompt": "Create synthetic colored blocks",
                            "background": "transparent",
                        },
                    },
                    {
                        "id": "edit",
                        "kind": "tool",
                        "tool_id": tools["image_edit"],
                        "depends_on": ["generate"],
                        "kwargs": {
                            "profile": "image_edit",
                            "prompt": "Change the block color",
                            "artifact_id": {"ref": "nodes.generate.artifacts.0.id"},
                            "revision": 1,
                        },
                    },
                    {
                        "id": "describe",
                        "kind": "tool",
                        "tool_id": tools["image_describe"],
                        "depends_on": ["edit"],
                        "kwargs": {
                            "profile": "image_describe",
                            "prompt": "Describe the synthetic image",
                            "artifact_id": {"ref": "nodes.edit.artifacts.0.id"},
                            "revision": 1,
                        },
                    },
                    {
                        "id": "sound",
                        "kind": "tool",
                        "tool_id": tools["audio_describe"],
                        "kwargs": {
                            "profile": "audio_describe",
                            "prompt": "Describe the short recording",
                            "artifact_id": source["id"],
                            "revision": 1,
                        },
                    },
                ],
                "output_mapping": {
                    "generated": {"ref": "nodes.generate"},
                    "edited": {"ref": "nodes.edit"},
                    "image_description": {"ref": "nodes.describe"},
                    "audio_description": {"ref": "nodes.sound"},
                },
            },
        },
    )
    run = studio.start_run(
        {
            "kind": "workflow",
            "workflow_id": workflow["id"],
            "task": "Exercise configured media tools",
        }
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        run = studio.run(run["id"])
        if run["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    if run["status"] != "completed":
        raise RuntimeError("Media workflow did not complete: " + str(run.get("error")))
    result = json.loads(run["output"])
    assert result["generated"]["artifacts"] and result["edited"]["artifacts"]
    assert result["image_description"]["output"] and result["audio_description"]["output"]
    attachments = [part for part in run["structured_content"] if part["type"] == "artifact"]
    assert len(attachments) == 2
    for part in attachments:
        record = studio.catalog.get("artifacts", part["artifact_id"])
        assert record["generated_by_run"] == run["id"] and record["revision"] == 1
    return result


def run_example(root):
    def request(method, url, headers, body, text):
        if "/images/" in url:
            return {"data": [{"b64_json": base64.b64encode(example_image()).decode()}]}
        return {
            "choices": [
                {"finish_reason": "stop", "message": {"content": "Synthetic media description."}}
            ]
        }

    with closing(
        StudioService(
            root,
            media_options={"profiles": example_profiles()},
            research_options={"request_client": request},
        )
    ) as studio:
        result = exercise(studio)
        print(json.dumps(result, indent=2))
        print("Generation, editing, image/audio descriptions and private artifacts verified.")
        return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="nexus-media-example-") as directory:
        run_example(Path(directory))

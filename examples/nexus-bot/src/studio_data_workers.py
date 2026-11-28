"""Exercise actual private data/media workers with an explicitly selected image.

PYTHONPATH=. python examples/nexus-bot/src/studio_data_workers.py --image sha256:... --docker-context desktop-linux
No image is pulled, credentials loaded or host directory mounted. State is temporary.
"""

import argparse
import base64
import json
import struct
import tempfile
import time
import zlib
from contextlib import closing
from pathlib import Path

from nexus.core.studio import StudioService


def profiles(image, context):
    return {
        name: {
            "image": image,
            "docker_context": context,
            "timeout": 60,
            "pids_limit": 256,
            "open_files": 256,
        }
        for name in ("data", "python", "media")
    }


def image_bytes():
    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value))
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\x00" + b"\xff\x00\x00" * 4) * 4))
        + chunk(b"IEND", b"")
    )


def upload(studio, filename, content):
    return studio.catalog.save(
        "artifacts",
        {
            "name": filename,
            "data": {
                "filename": filename,
                "content_base64": base64.b64encode(content).decode(),
            },
        },
    )


def exercise(studio):
    source = upload(studio, "rows.csv", b"name,amount\nAda,3\nLin,4\n")
    chart = upload(
        studio,
        "chart.json",
        json.dumps({"data": [{"type": "bar", "x": ["Ada", "Lin"], "y": [3, 4]}]}).encode(),
    )
    image = upload(studio, "image.png", image_bytes())
    tools = {name.rsplit(":", 1)[1]: name for name in studio.data_tools.names}
    recipe = studio.recipes.configure("imagemagick", {}, None)
    assert recipe["configured"]
    tools["imagemagick"] = recipe["tool_id"]
    operations = [
        ("database", "create_database", {"artifact_id": source["id"]}, []),
        (
            "sql",
            "query_sql",
            {
                "artifact_id": {"ref": "nodes.database.artifacts.0.id"},
                "query": "SELECT sum(amount) AS total FROM data",
            },
            ["database"],
        ),
        (
            "jq",
            "transform_jq",
            {"artifact_id": source["id"], "expression": "map(.amount|tonumber)|add"},
            [],
        ),
        ("chart", "chart", {"artifact_id": chart["id"]}, []),
        (
            "image",
            "imagemagick",
            {"artifact_id": image["id"], "width": 2, "height": 2, "grayscale": True},
            [],
        ),
        (
            "python",
            "python",
            {
                "code": "import os,pathlib\nassert os.getuid()==65534\npathlib.Path(os.environ['OUTPUT_DIR'],'analysis.txt').write_text('Private analysis')\nprint('Analysis complete')"
            },
            [],
        ),
    ]
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Private data analysis",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": name,
                        "kind": "tool",
                        "tool_id": tools[action],
                        "kwargs": kwargs,
                        "depends_on": dependencies,
                    }
                    for name, action, kwargs, dependencies in operations
                ],
                "output_mapping": {name: {"ref": "nodes." + name} for name, *_ in operations},
            },
        },
    )
    run = studio.start_run(
        {
            "kind": "workflow",
            "workflow_id": workflow["id"],
            "task": "Exercise isolated local workers",
        }
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        run = studio.run(run["id"])
        if run["status"] not in {"running", "queued"}:
            break
        time.sleep(0.05)
    if run["status"] != "completed":
        raise RuntimeError("Data workflow failed: " + str(run.get("error")))
    results = json.loads(run["output"])

    def content(name):
        ref = results[name]["artifacts"][0]
        record = studio.catalog.get("artifacts", ref["id"])
        assert record["generated_by_run"] == run["id"] and record["revision"] == ref["revision"]
        return studio.artifacts.get(record)

    assert content("database").startswith(b"SQLite format 3\x00")
    assert json.loads(content("sql"))["rows"] == [[7]]
    assert json.loads(content("jq")) == 7
    assert struct.unpack(">II", content("chart")[16:24]) == (1200, 800)
    assert struct.unpack(">II", content("image")[16:24]) == (2, 2)
    assert content("python") == b"Private analysis"
    assert len([part for part in run["structured_content"] if part["type"] == "artifact"]) == 6
    return {"run_id": run["id"], "operations": list(results), "artifacts": 6}


def exercise_vision(studio, image):
    source = upload(studio, "selected-image.jpg", Path(image).read_bytes())
    recipe = studio.recipes.configure("object-detection", {}, None)
    assert recipe["configured"]
    tool = recipe["tool_id"]
    run = studio.start_run(
        {
            "kind": "tool",
            "tool": tool,
            "task": "Detect objects in the selected image",
            "kwargs": {"artifact_id": source["id"]},
        }
    )
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        run = studio.run(run["id"])
        if run["status"] not in {"running", "queued"}:
            break
        time.sleep(0.05)
    if run["status"] != "completed":
        raise RuntimeError("Vision task failed: " + str(run.get("error")))
    results = json.loads(run["output"])
    outputs = {}
    for ref in results["artifacts"]:
        record = studio.catalog.get("artifacts", ref["id"])
        assert record["generated_by_run"] == run["id"] and record["revision"] == ref["revision"]
        outputs[ref["filename"]] = studio.artifacts.get(record)
    detections = json.loads(outputs["detections.json"])
    assert len(detections) <= 100 and outputs["annotated.jpg"].startswith(b"\xff\xd8")
    assert len(run["structured_content"]) == 2
    return {"run_id": run["id"], "detections": detections, "artifacts": 2}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--docker-context", required=True)
    parser.add_argument("--vision-image")
    parser.add_argument("--model-path")
    parser.add_argument("--sample-image")
    args = parser.parse_args()
    selected = (args.vision_image, args.model_path, args.sample_image)
    if any(selected) and not all(selected):
        parser.error("Choose vision image, installed model path and sample image together")
    workers = profiles(args.image, args.docker_context)
    if args.vision_image:
        workers["vision"] = {
            "image": args.vision_image,
            "docker_context": args.docker_context,
            "model_path": args.model_path,
            "memory_mb": 2048,
            "timeout": 120,
            "pids_limit": 256,
            "open_files": 256,
        }
    with tempfile.TemporaryDirectory(prefix="nexus-data-example-") as root:
        with closing(StudioService(root, data_tool_options={"profiles": workers})) as service:
            print(json.dumps(exercise(service), indent=2))
            if args.vision_image:
                print(json.dumps(exercise_vision(service, args.sample_image), indent=2))

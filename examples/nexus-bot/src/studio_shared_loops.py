"""Reuse an external loop template with ordered or joined concurrent controllers."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from nexus.core.studio import StudioService


def exercise(studio, *, concurrent=False):
    source = {
        "agent_name": "shared-loop-demo",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "words"}],
        "nodes": [
            {"id": "first", "type": "loop", "node": "body", "condition": "false"},
            {
                "id": "second",
                "type": "loop",
                "node": "body",
                "condition": "false",
                "depends_on": ["first"],
            },
            {
                "id": "body",
                "type": "tool",
                "tool_name": "words",
                "input": {"text": {"concat": [{"coalesce": ["{{body.output}}", ""]}, " word"]}},
            },
        ],
        "output_mapping": {"final_count": "{{body.output}}"},
    }
    if concurrent:
        source["nodes"][1].pop("depends_on")
        source["nodes"].append(
            {
                "id": "joined",
                "type": "loop",
                "node": "body",
                "condition": "false",
                "depends_on": ["first", "second"],
            }
        )
    report = studio.configuration.convert_interop(
        {"documents": [source], "bindings": {"tools": {"words": "text:wordcount"}}}, None
    )
    assert report["valid"], report
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    run = studio.start_run(
        {
            "kind": "workflow",
            "workflow_id": "shared-loop-demo",
            "task": "Reuse an ordered template locally",
        }
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = studio.run(run["id"])
        if result["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    else:
        raise RuntimeError("Local shared loop fixture did not settle")
    assert result["status"] == "completed", result
    assert json.loads(result["output"]) == {"final_count": 2}
    saved = studio._read(studio._path("graph-runs", run["id"]))
    assert saved["outputs"]["root/first/interop_initial"] is None
    second_seed = None if concurrent else 1
    assert saved["outputs"]["root/second/interop_initial"] == second_seed
    if concurrent:
        assert saved["outputs"]["root/joined/interop_initial"] == 1
    return {
        "output": json.loads(result["output"]),
        "second_seed": second_seed,
        "concurrent": concurrent,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", action="store_true", help="Use local SQLite durable workers")
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help="Run independent controllers and a joined dependent controller",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nexus-shared-loops-") as directory:
        root = Path(directory)
        options = (
            {
                "url": "sqlite:///" + str(root / "state.sqlite"),
                "namespace": "shared-loop-demo",
                "workers": 1,
            }
            if args.sql
            else None
        )
        studio = StudioService(root / "workspace", state_options=options)
        try:
            print(json.dumps(exercise(studio, concurrent=args.concurrent)))
        finally:
            studio.close()


if __name__ == "__main__":
    main()

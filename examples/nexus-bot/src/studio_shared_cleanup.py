"""Run one cleanup template for two phases, each with its own approval receipt."""

import argparse
import json
import tempfile
import time
from pathlib import Path

from nexus.core.studio import StudioService


def settled(studio, run):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = studio.run(run["id"])
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(0.01)
    raise RuntimeError("Local fixture did not settle")


def exercise(studio):
    studio.save_definition(
        "workflows",
        {
            "name": "Cleanup approval",
            "graph": {
                "version": 2,
                "nodes": [
                    {"id": "gate", "kind": "approval"},
                    {
                        "id": "words",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": "cleanup"},
                        "depends_on": ["gate"],
                    },
                ],
                "output_mapping": {"ref": "nodes.words"},
            },
        },
    )
    source = {
        "agent_name": "shared-exit-demo",
        "agent_type": "workflow",
        "tools": [{"tool_type": "builtin", "tool_name": "count"}],
        "on_exit": {"always": "cleanup", "on_success": "cleanup"},
        "nodes": [
            {
                "id": "work",
                "type": "tool",
                "tool_name": "count",
                "input": {"text": "one two three"},
            },
            {"id": "cleanup", "type": "workflow", "workflow_name": "Cleanup approval"},
        ],
        "output_mapping": {"words": "{{work.output}}", "cleanup": "{{cleanup.output}}"},
    }
    report = studio.configuration.convert_interop(
        {
            "documents": [source],
            "bindings": {"tools": {"count": "text:wordcount"}},
        },
        None,
    )
    assert report["valid"], report
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    run = studio.start_run(
        {"kind": "workflow", "workflow_id": "shared-exit-demo", "task": "Count locally"}
    )
    phases = []
    for expected in ("always", "on_success"):
        paused = settled(studio, run)
        assert paused["status"] == "waiting", paused
        gate = paused["awaiting_approval"][0]["id"]
        assert "/cleanup/" + expected + "/" in gate
        phases.append(expected)
        studio.graph.resume(run["id"], "approve")
    result = settled(studio, run)
    assert result["status"] == "completed", result
    assert json.loads(result["output"]) == {"words": 3, "cleanup": 1}
    saved = studio._read(studio._path("graph-runs", run["id"]))
    effects = [
        key
        for key, status in saved["nodes"].items()
        if key.endswith("/words") and status == "completed"
    ]
    assert len(effects) == 2
    return {
        "run_id": result["id"],
        "phases": phases,
        "output": json.loads(result["output"]),
        "cleanup_receipts": len(effects),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", action="store_true", help="Use local SQLite durable workers")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nexus-shared-cleanup-") as directory:
        root = Path(directory)
        options = (
            {
                "url": "sqlite:///" + str(root / "state.sqlite"),
                "namespace": "shared-cleanup-demo",
                "workers": 1,
            }
            if args.sql
            else None
        )
        studio = StudioService(root / "workspace", state_options=options)
        try:
            print(json.dumps(exercise(studio)))
        finally:
            studio.close()


if __name__ == "__main__":
    main()

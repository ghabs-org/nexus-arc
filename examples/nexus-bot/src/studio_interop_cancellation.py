"""Local external cancellation: stop a paused body, then review cleanup separately."""

import argparse
import json
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler

SOURCE = """agent_name: cancel-review
agent_type: workflow
tools:
  - {tool_type: builtin, tool_name: count_words}
on_exit:
  always: cleanup
  on_cancel: stopped
nodes:
  - {id: body, type: workflow, workflow_name: Body approval}
  - {id: cleanup, type: workflow, workflow_name: Cleanup approval}
  - id: stopped
    type: tool
    tool_name: count_words
    input: {text: '{{workflow.status}}'}
output_mapping: {body: '{{body.output}}'}
"""
BINDINGS = {"tools": {"count_words": "text:wordcount"}}


def prepare(studio):
    for name in ("Body approval", "Cleanup approval"):
        studio.save_definition(
            "workflows",
            {
                "name": name,
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "review", "name": name, "kind": "approval"},
                        {
                            "id": "count",
                            "kind": "tool",
                            "tool_id": "text:wordcount",
                            "kwargs": {"text": "local cleanup"},
                            "depends_on": ["review"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.count"},
                },
            },
        )
    converted = studio.configuration.convert_interop({"text": SOURCE, "bindings": BINDINGS}, None)
    assert converted["valid"], converted
    plan = studio.configuration.plan(converted["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)


def wait(studio, identity, status):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        run = studio.run(identity)
        if run["status"] not in {"running", "queued"}:
            assert run["status"] == status, run
            return run
        time.sleep(0.02)
    raise TimeoutError("Local cancellation example did not settle")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="Serve a disposable local Studio")
    parser.add_argument("--reject", action="store_true", help="Reject cleanup and retain a warning")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nexus-interop-cancel-") as directory:
        studio = StudioService(Path(directory))
        try:
            prepare(studio)
            if args.serve:
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), make_studio_handler(studio, "interop-cancel-fixture")
                )
                print(
                    f"Local cancellation Studio: http://127.0.0.1:{server.server_port}/", flush=True
                )
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                return
            task = studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "cancel-review",
                    "task": "Cancellation fixture",
                    "workflow_input": {"topic": "local"},
                }
            )
            wait(studio, task["id"], "waiting")
            studio.cancel_run(task["id"])
            wait(studio, task["id"], "waiting")
            studio.graph.resume(task["id"], "reject" if args.reject else "approve")
            result = wait(studio, task["id"], "cancelled")
            checkpoint = studio._read(studio._path("graph-runs", task["id"]))
            assert "root/body/count" not in checkpoint["started"]
            assert checkpoint["outputs"]["root/stopped"] == 1
            assert result["cleanup_status"] == "completed"
            assert bool(result.get("workflow_warnings")) == args.reject
            print(
                json.dumps(
                    {
                        key: result.get(key)
                        for key in ("status", "cleanup_status", "workflow_warnings")
                    },
                    indent=2,
                )
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

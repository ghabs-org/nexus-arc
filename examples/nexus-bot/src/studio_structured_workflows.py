"""Consume bounded structured graphs, nested workflows and approval checkpoints."""

import argparse
import json
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


class SyntheticProvider:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        return AgentResult(success=True, output='{"reviewed":true}')


def identity_graph():
    return {
        "version": 2,
        "nodes": [
            {
                "id": "result",
                "name": "Return input",
                "kind": "switch",
                "cases": [
                    {
                        "condition": {"left": True, "op": "eq", "right": True},
                        "value": {"ref": "input"},
                    }
                ],
            }
        ],
        "output_mapping": {"ref": "nodes.result"},
    }


def service(root, **kwargs):
    return StudioService(root, provider_factory=lambda _: SyntheticProvider(), **kwargs)


def create_demo(root):
    studio = service(root)
    studio.save_definition(
        "agents",
        {
            "name": "Reviewer",
            "provider": "openai",
            "instructions": "Review supplied counts; return a JSON review flag",
        },
    )
    studio.save_definition(
        "workflows",
        {"name": "Return counts", "graph": {**identity_graph(), "on_success": identity_graph()}},
    )
    body = {
        "version": 2,
        "nodes": [
            {
                "id": "count",
                "name": "Count words",
                "kind": "tool",
                "tool_id": "text:wordcount",
                "kwargs": {"text": {"ref": "input"}},
            }
        ],
        "output_mapping": {"ref": "nodes.count"},
    }
    repeated = {
        "version": 2,
        "nodes": [
            {
                "id": "child",
                "name": "Return counts",
                "kind": "workflow",
                "workflow_id": "return-counts",
            }
        ],
        "output_mapping": {"ref": "nodes.child"},
    }
    graph = {
        "version": 2,
        "input_schema": {"type": "array", "items": {"type": "string"}},
        "nodes": [
            {
                "id": "each",
                "name": "Each entry",
                "kind": "map",
                "items": {"ref": "input"},
                "body": body,
            },
            {
                "id": "repeat",
                "name": "Two passes",
                "kind": "loop",
                "input": {"ref": "nodes.each"},
                "depends_on": ["each"],
                "max_iterations": 2,
                "condition": {"left": {"ref": "iteration"}, "op": "lt", "right": 1},
                "body": repeated,
            },
            {"id": "gate", "name": "Review counts", "kind": "approval", "depends_on": ["repeat"]},
            {
                "id": "review",
                "name": "Review result",
                "kind": "agent",
                "agent_id": "reviewer",
                "input": {"ref": "nodes.repeat"},
                "depends_on": ["gate"],
                "output_schema": {"type": "object", "required": ["reviewed"]},
            },
        ],
        "output_mapping": {"counts": {"ref": "nodes.each"}, "review": {"ref": "nodes.review"}},
        "on_success": identity_graph(),
        "on_error": identity_graph(),
    }
    workflow = studio.save_definition("workflows", {"name": "Structured review", "graph": graph})
    return studio, workflow


def wait(studio, run_id, status):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        run = studio.run(run_id)
        if run["status"] not in {"queued", "running"}:
            assert run["status"] == status, run
            return run
        time.sleep(0.01)
    raise TimeoutError("Synthetic workflow did not reach its checkpoint")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start a disposable loopback Studio with synthetic generation",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nexus-structured-") as directory:
        root = Path(directory)
        studio, workflow = create_demo(root)
        try:
            if args.serve:
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), make_studio_handler(studio, "synthetic-structured-token")
                )
                print(f"Synthetic Studio: http://127.0.0.1:{server.server_port}/", flush=True)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                return
            queued = studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": workflow["id"],
                    "task": "Review entries",
                    "workflow_input": ["one two", "three"],
                }
            )
            paused = wait(studio, queued["id"], "waiting")
            assert paused["awaiting_approval"][0]["id"] == "root/gate"
            studio.close()
            # New configuration applies to future runs; the paused run retains its policy.
            studio = service(root, workflow_limits={"map_items": 2, "map_concurrency": 1})
            resumed = studio.graph.resume(queued["id"], "approve")
            result = wait(studio, resumed["id"], "completed")
            assert json.loads(result["output"]) == {"counts": [2, 1], "review": {"reviewed": True}}
            print(
                "Mapped ordering, bounded loop, pinned nested workflow and approval restart verified."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

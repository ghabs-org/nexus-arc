"""Run repeated workflow evaluations with explicit approval and typed input.

Run from the repository root with PYTHONPATH=.
Uses temporary local state and a synthetic provider; no account is contacted.
"""

import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def wait(studio, identity):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = studio.run(identity)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.01)
    raise TimeoutError("Local experiment did not pause or finish")


def main():
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(success=True, output="Evidence checked")

    with tempfile.TemporaryDirectory(prefix="nexus-experiment-review-") as root:
        studio = StudioService(root, provider_factory=lambda _: Provider())
        try:
            studio.save_definition(
                "agents", {"name": "Reviewer", "instructions": "Review evidence"}
            )
            workflow = studio.save_definition(
                "workflows",
                {
                    "name": "Reviewed answer",
                    "graph": {
                        "version": 2,
                        "nodes": [
                            {"id": "prepare", "kind": "agent", "agent_id": "reviewer"},
                            {"id": "gate", "kind": "approval", "depends_on": ["prepare"]},
                            {
                                "id": "answer",
                                "kind": "input",
                                "depends_on": ["gate"],
                                "output_schema": {"type": "string", "minLength": 1},
                            },
                        ],
                        "output_mapping": {"ref": "nodes.answer"},
                    },
                },
            )
            suite = studio.catalog.save(
                "evaluations",
                {
                    "name": "Reviewed evidence",
                    "data": {
                        "target_kind": "workflow",
                        "target_id": workflow["id"],
                        "cases": [{"input": "Review policy", "contains": ["Evidence"]}],
                    },
                },
            )
            experiment = studio.catalog.save(
                "experiments",
                {
                    "name": "Two reviewed repetitions",
                    "data": {
                        "evaluation_id": suite["id"],
                        "evaluation_revision": suite["revision"],
                        "runsPerExample": 2,
                        "passThreshold": 1,
                        "variants": [{"name": "Baseline"}],
                    },
                },
            )
            run = studio.start_run({"kind": "evaluation", "experiment_id": experiment["id"]})
            for _ in range(2):
                run = wait(studio, run["id"])
                assert run["status"] == "waiting" and run["awaiting_approval"]
                studio.graph.resume(run["id"], "approve", checkpoint=run["evaluation_checkpoint"])
                run = wait(studio, run["id"])
                assert run["status"] == "waiting" and run["awaiting_input"]
                studio.graph.structured.resume_input(
                    run["id"],
                    {run["awaiting_input"][0]["id"]: "Evidence reviewed"},
                    checkpoint=run["evaluation_checkpoint"],
                )
            run = wait(studio, run["id"])
            report = studio._read(studio._path("evaluation-reports", run["id"]))
            assert run["status"] == "completed" and report["acceptance"]
            assert report["passed"] == len(calls) == 2
            print(
                "Two explicit approvals and typed replies passed; completed preparation steps were not replayed."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

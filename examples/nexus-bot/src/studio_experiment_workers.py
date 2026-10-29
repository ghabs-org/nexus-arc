"""Run a bounded parallel experiment using a synthetic in-process provider.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_experiment_workers.py
No provider account, external service or model billing is used.
"""

import asyncio
import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def main():
    active, maximum = 0, 0
    ready = asyncio.Event()

    class SyntheticProvider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                if active == 2:
                    ready.set()
                await ready.wait()
                return AgentResult(success=True, output="Approved")
            finally:
                active -= 1

    with tempfile.TemporaryDirectory(prefix="nexus-experiment-workers-") as root:
        studio = StudioService(root, provider_factory=lambda _: SyntheticProvider())
        try:
            studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review status"})
            suite = studio.catalog.save(
                "evaluations",
                {
                    "name": "Status cases",
                    "data": {
                        "target_kind": "agent",
                        "target_id": "reviewer",
                        "cases": [
                            {"input": f"Status {i}", "contains": ["Approved"]} for i in range(3)
                        ],
                    },
                },
            )
            experiment = studio.catalog.save(
                "experiments",
                {
                    "name": "Parallel status regression",
                    "data": {
                        "evaluation_id": suite["id"],
                        "evaluation_revision": suite["revision"],
                        "runsPerExample": 2,
                        "maxWorkers": 2,
                        "passThreshold": 1,
                        "variants": [{"name": "Baseline"}],
                    },
                },
            )
            run = studio.start_run({"kind": "evaluation", "experiment_id": experiment["id"]})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = studio.run(run["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert run["status"] == "completed", run
            report = studio._read(studio._path("evaluation-reports", run["id"]))
            assert maximum == 2 and active == 0
            assert report["max_workers"] == 2 and report["passed"] == report["total"] == 6
            assert report["dispatched_slots"] == list(range(6)) and report["acceptance"]
            print("Six synthetic cases passed with two workers; no external model calls.")
        finally:
            studio.close()


if __name__ == "__main__":
    main()

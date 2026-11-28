"""Grade each answer with two copied evaluators using a synthetic provider.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_experiment_evaluators.py
No external service or model billing is used.
"""

import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_evaluation_gate import check_report
from nexus.core.studio_experiment_judges import copy_evaluator


def main():
    calls = []

    class SyntheticProvider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(
                success=True,
                output='{"score":1,"reason":"Matches"}'
                if "Evaluate the answer using only the rubric" in context.prompt
                else "Approved",
            )

    with tempfile.TemporaryDirectory(prefix="nexus-multiple-evaluators-") as root:
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
                        "cases": [{"input": "Review status", "contains": ["Approved"]}],
                    },
                },
            )
            copies = [
                copy_evaluator(
                    studio.catalog.save(
                        "evaluators",
                        {
                            "name": name,
                            "data": {
                                "provider": "codex",
                                "rubric": f"Check {name}",
                                "threshold": 0.8,
                            },
                        },
                    )
                )
                for name in ("Accuracy", "Clarity")
            ]
            saved = studio.catalog.save(
                "experiments",
                {
                    "name": "Two-judge regression",
                    "data": {
                        "evaluation_id": suite["id"],
                        "evaluation_revision": suite["revision"],
                        "runsPerExample": 2,
                        "maxWorkers": 2,
                        "variants": [{"name": "Baseline"}],
                        "evaluators": copies,
                        "primaryEvaluatorId": copies[0]["id"],
                        "evaluatorPolicy": "all",
                    },
                },
            )
            assert not calls
            run = studio.start_run({"kind": "evaluation", "experiment_id": saved["id"]})
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = studio.run(run["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert run["status"] == "completed", run
            report = studio._read(studio._path("evaluation-reports", run["id"]))
            assert len(calls) == 6 and check_report(report)["passed"]
            assert [item["passed"] for item in report["evaluator_summaries"]] == [2, 2]
            print(
                "Two target answers passed both evaluators: six synthetic invocations, no external model calls."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

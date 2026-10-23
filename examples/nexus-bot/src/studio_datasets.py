"""Import reusable datasets and run independent copied evaluation cases locally.

Run from the checkout: PYTHONPATH=. python examples/nexus-bot/src/studio_datasets.py
The injected provider returns synthetic text; no account or service is contacted.
"""

import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_datasets import parse_dataset


def main():
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            if "Configured choice scores (JSON)" in context.prompt:
                return AgentResult(
                    success=True, output='{"choice":"Supported","reason":"Synthetic evidence"}'
                )
            return AgentResult(success=True, output="Approved")

    with tempfile.TemporaryDirectory(prefix="nexus-dataset-example-") as root:
        studio = StudioService(root, provider_factory=lambda _: Provider())
        try:
            studio.save_definition(
                "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review policy"}
            )
            dataset = studio.catalog.save(
                "datasets",
                {
                    "name": "Policy references",
                    "data": parse_dataset(
                        {"format": "csv", "content": "input,expected\nReview policy,Approved\n"}
                    ),
                },
            )
            evaluator = studio.catalog.save(
                "evaluators",
                {
                    "name": "Evidence judge",
                    "data": {
                        "provider": "codex",
                        "rubric": "Check supplied evidence",
                        "choices": {"Supported": 1, "Unsupported": 0},
                    },
                },
            )
            judge = {
                key: value
                for key, value in evaluator["data"].items()
                if key not in {"kind", "type", "version"}
            }
            cases = [{**case, "judge": judge} for case in dataset["data"]["cases"]]
            suite = studio.catalog.save(
                "evaluations",
                {
                    "name": "Policy regression",
                    "data": {
                        "target_kind": "agent",
                        "target_id": "reviewer",
                        "cases": cases,
                    },
                },
            )
            # Dataset edits do not change the copied suite or its queued snapshot.
            studio.catalog.save(
                "datasets",
                {
                    **dataset,
                    "data": parse_dataset(
                        {
                            "format": "yaml",
                            "content": "cases:\n- input: Changed policy\n  contains: [Changed]\n",
                        }
                    ),
                },
            )
            run = studio.start_run({"kind": "evaluation", "evaluation_id": suite["id"]})
            deadline = time.monotonic() + 5
            while studio.run(run["id"])["status"] in {"queued", "running"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Local evaluation did not finish")
                time.sleep(0.01)
            report = studio._read(studio._path("evaluation-reports", run["id"]))
            assert studio.run(run["id"])["status"] == "completed"
            assert report["passed"] == report["total"] == 1
            assert report["results"][0]["input"] == "Review policy"
            assert report["results"][0]["checks"][-1]["choice"] == "Supported"
            assert report["results"][0]["checks"][-1]["score"] == 1
            assert studio.catalog.history("datasets", dataset["id"])[0]["revision"] == 1
            experiment = studio.catalog.save(
                "experiments",
                {
                    "name": "Repeated policy regression",
                    "data": {
                        "evaluation_id": suite["id"],
                        "evaluation_revision": suite["revision"],
                        "runsPerExample": 2,
                        "passThreshold": 1,
                        "variants": [{"name": "Baseline"}],
                    },
                },
            )
            repeated = studio.start_run({"kind": "evaluation", "experiment_id": experiment["id"]})
            deadline = time.monotonic() + 5
            while studio.run(repeated["id"])["status"] in {"queued", "running"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Repeated local evaluation did not finish")
                time.sleep(0.01)
            report = studio._read(studio._path("evaluation-reports", repeated["id"]))
            assert report["complete"] and report["acceptance"]
            assert report["expected_total"] == report["passed"] == 2
            print(
                "Datasets, evaluator choice scores, saved experiments and repeated threshold grading verified."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

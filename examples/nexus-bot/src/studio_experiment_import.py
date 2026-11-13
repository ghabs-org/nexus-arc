"""Import an experiment with explicit saved resource revisions, without running it.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_experiment_import.py
Uses bundled synthetic inputs, disposable state and existing native plan/apply.
"""

import tempfile
from pathlib import Path

from nexus.core.studio import StudioService


def main():
    source = Path(__file__).parents[1] / "config/evaluation-import"
    with tempfile.TemporaryDirectory(prefix="nexus-experiment-import-") as root:
        studio = StudioService(root)
        try:
            package = studio.configuration.convert_evaluations(
                {
                    "dataset": (source / "dataset.yaml").read_text(),
                    "evaluator": (source / "evaluator.yaml").read_text(),
                    "files": {"status.csv": (source / "status.csv").read_text()},
                    "binding": {"alias": "general", "provider": "codex"},
                },
                None,
            )
            plan = studio.configuration.plan(package["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            agent = studio.save_definition(
                "agents",
                {
                    "id": "status-agent",
                    "name": "Status agent",
                    "instructions": "Answer status questions",
                    "provider": "codex",
                },
            )
            dataset = studio.catalog.get("datasets", "status-cases")
            evaluator = studio.catalog.get("evaluators", "status-judge")
            converted = studio.configuration.convert_experiment(
                {
                    "experiment": (source / "experiment.yaml").read_text(),
                    "pass_threshold": 1,
                    "bindings": {
                        "dataset": {"id": dataset["id"], "revision": dataset["revision"]},
                        "evaluator": {"id": evaluator["id"], "revision": evaluator["revision"]},
                        "target": {"id": agent["id"], "revision": agent["revision"]},
                    },
                },
                None,
            )
            assert converted["planned_executions"] == 4
            assert not studio.catalog.list("experiments") and not studio.records("runs")
            # Interactive consumers display adaptations and the plan before apply.
            plan = studio.configuration.plan(converted["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            saved = studio.catalog.get("experiments", "status-regression")
            assert saved["data"]["runsPerExample"] == 2
            assert saved["data"]["evaluation_revision"] == 1
            assert not studio.records("runs")
            exported = studio.configuration.export_experiment(
                {
                    "experiment_id": saved["id"],
                    "revision": saved["revision"],
                    "dataset": {"id": dataset["id"], "revision": dataset["revision"]},
                },
                None,
            )
            assert exported["valid"], exported["diagnostics"]
            handoff = next(
                item["content"]
                for item in exported["files"]
                if item["filename"] == "experiment-handoff.json"
            )
            restored = studio.configuration.convert_experiment_handoff({"handoff": handoff}, None)
            restored_settings = restored["document"]["resources"][1]["data"]
            assert {
                key: value for key, value in restored_settings.items() if key != "evaluation_id"
            } == {key: value for key, value in saved["data"].items() if key != "evaluation_id"}
            assert not studio.records("runs")
            print("Experiment saved; declaration/handoff round-trip verified; no runs started.")
        finally:
            studio.close()


if __name__ == "__main__":
    main()

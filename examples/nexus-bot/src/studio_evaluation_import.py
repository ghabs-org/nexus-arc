"""Convert and explicitly save the bundled evaluation example without AI calls.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_evaluation_import.py
Only the three named local example files are read; no sibling search occurs.
"""

import tempfile
from pathlib import Path

from nexus.core.studio import StudioService


def main():
    source = Path(__file__).parents[1] / "config/evaluation-import"
    with tempfile.TemporaryDirectory(prefix="nexus-evaluation-import-") as root:
        studio = StudioService(root)
        try:
            report = studio.configuration.convert_evaluations(
                {
                    "dataset": (source / "dataset.yaml").read_text(),
                    "evaluator": (source / "evaluator.yaml").read_text(),
                    "files": {"status.csv": (source / "status.csv").read_text()},
                    "binding": {"alias": "general", "provider": "codex"},
                },
                None,
            )
            assert not studio.catalog.list("datasets") and not studio.records("runs")
            # An interactive consumer shows document/adaptations before this step.
            plan = studio.configuration.plan(report["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            assert len(studio.catalog.get("datasets", "status-cases")["data"]["cases"]) == 2
            assert not studio.records("runs")
            dataset = studio.catalog.get("datasets", "status-cases")
            evaluator = studio.catalog.get("evaluators", "status-judge")
            exported = studio.configuration.export_evaluations(
                {
                    "dataset": {"id": dataset["id"], "revision": dataset["revision"]},
                    "evaluator": {"id": evaluator["id"], "revision": evaluator["revision"]},
                    "model_alias": "general",
                },
                None,
            )
            assert exported["valid"], exported["diagnostics"]
            handoff = next(
                file["content"]
                for file in exported["files"]
                if file["filename"] == "evaluation-handoff.json"
            )
            destination = StudioService(Path(root) / "fresh-destination")
            try:
                restored = destination.configuration.convert_evaluation_handoff(
                    {"handoff": handoff}, None
                )
                assert (
                    restored["dataset"] == dataset["data"]
                    and restored["evaluator"] == evaluator["data"]
                )
                assert not destination.records("configuration-plans") and not destination.records(
                    "runs"
                )
                # An interactive consumer reviews the imported resources and this plan.
                plan = destination.configuration.plan(restored["document"], None)
                destination.configuration.apply(plan["id"], plan["digest"], None)
                assert destination.catalog.get("datasets", dataset["id"])["data"] == dataset["data"]
                assert not destination.records("runs")
            finally:
                destination.close()
            print(
                "Dataset/CSV/judge package round-trip verified in a fresh workspace; no AI invoked."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

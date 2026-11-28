"""Demonstrate reviewed dataset drafting without a real provider or account.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_dataset_drafting.py
"""

import json
import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


class SyntheticDesigner:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        return AgentResult(success=True, output=json.dumps({
            "name": "Refund policy",
            "data": {"cases": [
                {"input": "Refund after 31 days", "contains": ["not eligible"]},
                {"input": "Refund after 29 days", "contains": ["eligible"]},
            ]},
        }))


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-dataset-drafting-") as root:
        studio = StudioService(root, builder_factory=lambda _: SyntheticDesigner())
        try:
            run = studio.start_run({
                "kind": "design", "design_kind": "dataset", "provider": "codex",
                "case_count": 2, "task": "Test the 30-day refund policy",
            })
            deadline = time.monotonic() + 5
            while (run := studio.run(run["id"]))["status"] in {"queued", "running"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Dataset drafting did not finish")
                time.sleep(0.01)
            assert run["status"] == "completed", run
            assert not studio.catalog.list("datasets")
            # A real consumer lets the user review/edit before this separate save.
            saved = studio.catalog.save("datasets", run["design_proposal"])
            assert len(saved["data"]["cases"]) == 2
            assert not studio.catalog.list("evaluations")
            print("Draft reviewed and saved separately; no evaluation executed.")
        finally:
            studio.close()


if __name__ == "__main__":
    main()

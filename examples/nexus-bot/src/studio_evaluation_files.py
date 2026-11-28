"""Demonstrate pinned file roles with a synthetic target and judge, without accounts.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_evaluation_files.py
"""

import base64
import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


class LocalProvider:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        if "Evaluate the answer using only the rubric" in context.prompt:
            assert "REFERENCE_ONLY" in context.prompt
            return AgentResult(success=True, output='{"score":1,"reason":"Matches fixture"}')
        assert "INPUT_ONLY" in context.prompt and "REFERENCE_ONLY" not in context.prompt
        return AgentResult(success=True, output="Approved")


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-evaluation-files-") as root:
        studio = StudioService(root, provider_factory=lambda _: LocalProvider())
        try:
            studio.save_definition("agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review inputs"})
            references = []
            for name, text in [("input", "INPUT_ONLY"), ("reference", "REFERENCE_ONLY")]:
                item = studio.catalog.save("artifacts", {"name": name, "data": {
                    "filename": name + ".txt", "content_base64": base64.b64encode(text.encode()).decode(),
                }})
                references.append({"id": item["id"], "revision": item["revision"]})
            suite = studio.catalog.save("evaluations", {"name": "Pinned files", "data": {
                "target_kind": "agent", "target_id": "reviewer", "cases": [{
                    "input": "Review the supplied input", "input_files": [references[0]],
                    "reference_files": [references[1]],
                    "judge": {"provider": "codex", "rubric": "Compare against grading reference"},
                }],
            }})
            run = studio.start_run({"kind": "evaluation", "evaluation_id": suite["id"]})
            deadline = time.monotonic() + 5
            while (run := studio.run(run["id"]))["status"] in {"queued", "running"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Local evaluation did not finish")
                time.sleep(0.01)
            assert run["status"] == "completed", run
            report = studio._read(studio._path("evaluation-reports", run["id"]))
            assert report["passed"] == 1
            print("Pinned input and judge-only reference verified; no account contacted.")
        finally:
            studio.close()


if __name__ == "__main__":
    main()

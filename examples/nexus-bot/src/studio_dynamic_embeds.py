"""Consume bounded dynamic embeds with private local artifacts and a fake model.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_dynamic_embeds.py
No account, container, network request or external model is used.
"""

import base64
import json
import tempfile
import time
from contextlib import closing
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def run_example(root):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(
                success=True,
                output=(
                    "## Weekly report\n\n"
                    "Prepared «datetime:%Y-%m-%d». Total: «math:40 * 1.2 | .2f».\n\n"
                    "«artifact_content:metrics.json >>> jsonpath:$.items[*] "
                    ">>> select_fields:name,total >>> format:csv»\n\n"
                    "Download: «artifact_return:metrics.json:1»"
                ),
            )

    with closing(StudioService(root, provider_factory=lambda _: Provider())) as studio:
        record = studio.catalog.save(
            "artifacts",
            {
                "name": "Weekly metrics",
                "data": {
                    "filename": "metrics.json",
                    "content_base64": base64.b64encode(
                        json.dumps(
                            {"items": [{"name": "Ada", "total": 48}, {"name": "Lin", "total": 24}]}
                        ).encode()
                    ).decode(),
                },
            },
        )
        run = studio.start_run({"task": "Summarize the weekly metrics"})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = studio.run(run["id"])
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        if run["status"] != "completed":
            raise RuntimeError("Example response did not complete: " + str(run.get("error")))
        assert "48.00" in run["output"] and "Ada,48" in run["output"]
        assert run["structured_content"][0]["artifact_id"] == record["id"]
        assert run["structured_content"][0]["revision"] == 1
        print(run["output"])
        print("Pinned attachment and persisted response verified.")
        return run


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="nexus-embeds-example-") as directory:
        run_example(Path(directory))

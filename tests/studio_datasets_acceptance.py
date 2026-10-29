"""Disposable full-app dataset editor fixture; no external model is configured."""

import base64
import json
import tempfile
from http.server import ThreadingHTTPServer

from nexus.core.models import AgentResult
from nexus.core.studio_datasets import parse_dataset
from tests.studio_sharing_team_acceptance import create_fixture


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-datasets-acceptance-") as root:
        studio, handler, _ = create_fixture(root)
        class DraftProvider:
            async def check_availability(self):
                return True

            async def execute_agent(self, context):
                return AgentResult(success=True, output=json.dumps({
                    "name": "Synthetic cases",
                    "data": {"cases": [
                        {"input": f"Synthetic question {index}", "contains": ["Approved"]}
                        for index in range(1, 6)
                    ]},
                }))

        studio.team.provider = lambda *args, **kwargs: DraftProvider()
        for name, text in (("case-input", "Input text for the target"), ("case-reference", "Expected answer for the judge")):
            studio.catalog.save("artifacts", {
                "id": name, "name": name, "requester_nexus_id": "alice",
                "data": {"filename": name + ".txt", "content_base64": base64.b64encode(text.encode()).decode()},
            })
        studio.catalog.save("evaluators", {
            "name": "Synthetic reference grader", "data": {"provider": "codex", "rubric": "Compare with grading-reference files"},
        })
        studio.save_definition(
            "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review policy"}
        )
        studio.catalog.save(
            "datasets",
            {
                "name": "Policy references",
                "requester_nexus_id": "alice",
                "data": parse_dataset(
                    {"format": "csv", "content": "input,expected\nReview policy,Approved"}
                ),
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/acceptance"}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            studio.close()


if __name__ == "__main__":
    main()

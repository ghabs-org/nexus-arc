"""Disposable Experiment Lab fixture; all provider execution is synthetic and local."""

import json
import tempfile
from http.server import ThreadingHTTPServer

from nexus.core.models import AgentResult
from tests.studio_sharing_team_acceptance import create_fixture


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-experiments-acceptance-") as root:
        studio, handler, _ = create_fixture(root)

        class Provider:
            async def check_availability(self):
                return True

            async def execute_agent(self, context):
                return AgentResult(
                    success=True,
                    output="Unsupported answer"
                    if context.model_override == "weak"
                    else "Evidence reviewed",
                )

        studio.team.provider = lambda name, payload, builder=False: Provider()
        studio.save_definition(
            "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review evidence"}
        )
        suite = studio.catalog.save(
            "evaluations",
            {
                "name": "Evidence suite",
                "requester_nexus_id": "alice",
                "data": {
                    "target_kind": "agent",
                    "target_id": "reviewer",
                    "cases": [{"input": "Review synthetic evidence", "contains": ["Evidence"]}],
                },
            },
        )
        studio.catalog.save(
            "experiments",
            {
                "name": "Comparison experiment",
                "requester_nexus_id": "alice",
                "data": {
                    "evaluation_id": suite["id"],
                    "evaluation_revision": suite["revision"],
                    "runsPerExample": 2,
                    "passThreshold": 0.5,
                    "variants": [{"name": "Baseline", "model": "strong"}],
                },
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

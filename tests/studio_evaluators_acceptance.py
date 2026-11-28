"""Disposable full-app evaluator acceptance with an injected synthetic judge."""

import json
import tempfile
from http.server import ThreadingHTTPServer

from nexus.core.models import AgentResult
from tests.studio_sharing_team_acceptance import create_fixture
from tests.test_studio_evaluators import CONFIG


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-evaluators-acceptance-") as root:
        studio, handler, _ = create_fixture(root)

        class Provider:
            async def check_availability(self):
                return True

            async def execute_agent(self, context):
                return AgentResult(
                    success=True,
                    output='{"choice":"Partial","reason":"Synthetic reference evidence"}'
                    if "Configured choice scores (JSON)" in context.prompt
                    else "Evidence reviewed",
                )

        studio.team.provider = lambda name, payload, builder=False: Provider()
        studio.save_definition(
            "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review evidence"}
        )
        studio.catalog.save(
            "evaluators", {"name": "Evidence judge", "requester_nexus_id": "alice", "data": CONFIG}
        )
        studio.catalog.save(
            "evaluations",
            {
                "name": "Evidence acceptance",
                "requester_nexus_id": "alice",
                "data": {
                    "target_kind": "agent",
                    "target_id": "reviewer",
                    "cases": [
                        {
                            "input": "Review the synthetic policy",
                            "contains": ["Evidence"],
                            "contexts": ["Synthetic reference policy"],
                        }
                    ],
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

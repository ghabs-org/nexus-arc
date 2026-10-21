"""Review worker activation without running the generated workflow action.

PYTHONPATH=. python examples/nexus-bot/src/studio_activation_setup.py
Add --image sha256:... --docker-context desktop-linux for actual isolated probes.
"""

import argparse
import json
import tempfile
import time
from contextlib import closing

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def run_example(image=None, context=None):
    studio = None

    class LocalProposalProvider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            tool = next(name for name in studio.data_tools.names if name.endswith(":python"))
            return AgentResult(
                success=True,
                output=json.dumps(
                    {
                        "name": "Reviewed analysis",
                        "summary": "Check local setup before activation",
                        "assumptions": [],
                        "agents": [],
                        "resources": [],
                        "workflow": {
                            "id": "analysis",
                            "name": "Analysis",
                            "graph": {
                                "version": 2,
                                "nodes": [
                                    {
                                        "id": "analyze",
                                        "kind": "tool",
                                        "tool_id": tool,
                                        "kwargs": {
                                            "code": "raise AssertionError('This workflow action must never run during activation')"
                                        },
                                    }
                                ],
                                "output_mapping": {"ref": "nodes.analyze"},
                            },
                        },
                    }
                ),
            )

    workers = {"python": {"image": image, "docker_context": context}} if image else {}
    with tempfile.TemporaryDirectory(prefix="nexus-activation-example-") as root:
        with closing(
            StudioService(
                root,
                provider_factory=lambda _: LocalProposalProvider(),
                data_tool_options={"profiles": workers},
            )
        ) as studio:
            run = studio.start_run(
                {"kind": "build", "task": "Create a reviewable local analysis workflow"}
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = studio.run(run["id"])
                if run["status"] not in {"running", "queued"}:
                    break
                time.sleep(0.01)
            if run["status"] != "completed":
                raise RuntimeError("Local proposal did not complete")
            build = studio.builder.get(run["build_id"])
            review = studio.builder.review(build["id"], {"revision": build["revision"]})
            build = studio.builder.apply(
                build["id"],
                {
                    "revision": build["revision"],
                    "review_id": review["id"],
                    "digest": review["digest"],
                },
            )
            activation = studio.resource_lifecycle.plan_build(
                build["id"], {"revision": build["revision"], "action": "activate"}
            )
            if image:
                if activation["errors"]:
                    raise RuntimeError("Worker setup failed: " + str(activation["errors"]))
                result = studio.resource_lifecycle.transition_build(
                    build["id"], {"plan_id": activation["id"], "digest": activation["digest"]}
                )
                assert result["status"] == "active"
            else:
                assert any(
                    "worker_profile_missing:python" in item["reason"]
                    for item in activation["errors"]
                )
                result = {"status": "blocked", "errors": activation["errors"]}
            assert all(item["kind"] == "build" for item in studio.records("runs"))
            print(json.dumps(result, indent=2))
            return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--docker-context")
    args = parser.parse_args()
    if args.image and not args.docker_context:
        parser.error("Select the Docker context explicitly")
    run_example(args.image, args.docker_context)

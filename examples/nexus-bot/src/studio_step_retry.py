"""Retry a failed workflow step through the same framework API used by Activity.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_step_retry.py
No accounts or external actions are used.
"""

import json
import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def wait(studio, run):
    for _ in range(500):
        saved = studio.run(run["id"])
        if saved["status"] not in {"queued", "running"} and run["id"] not in studio.active_tasks:
            return saved
        time.sleep(0.01)
    raise RuntimeError("Example did not finish")


def main():
    calls = []
    build_calls = []

    class Builder:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            build_calls.append(context.agent_name)
            return AgentResult(
                success=True,
                output=json.dumps(
                    {
                        "name": "Evidence review",
                        "summary": "Review supplied evidence.",
                        "agents": [
                            {
                                "id": "reviewer",
                                "name": "Reviewer",
                                "description": "Review evidence",
                                "instructions": "Use the evidence skill.",
                                "skill_ids": ["evidence"],
                            }
                        ],
                        "workflow": None,
                    }
                ),
            )

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.agent_name)
            fails = context.agent_name == "reviewer" and calls.count("reviewer") == 1
            return AgentResult(
                success=not fails,
                output="" if fails else "Reviewed evidence",
                error="Temporary example failure" if fails else None,
            )

    with tempfile.TemporaryDirectory(prefix="nexus-step-retry-") as workspace:
        studio = StudioService(
            workspace, provider_factory=lambda _: Provider(), builder_factory=lambda _: Builder()
        )
        try:
            for name in ["researcher", "reviewer"]:
                studio.save_definition(
                    "agents",
                    {"name": name, "provider": "openai", "instructions": "Use supplied evidence."},
                )
            definition = studio.save_definition(
                "workflows",
                {
                    "name": "Review evidence",
                    "graph": {
                        "version": 2,
                        "nodes": [
                            {
                                "id": "research",
                                "kind": "agent",
                                "agent_id": "researcher",
                                "prompt": "{task}",
                            },
                            {
                                "id": "review",
                                "kind": "agent",
                                "agent_id": "reviewer",
                                "prompt": "{task}",
                            },
                        ],
                        "edges": [
                            {"source": "START", "target": "research"},
                            {"source": "research", "target": "review"},
                        ],
                    },
                },
            )
            failed = wait(
                studio,
                studio.start_run(
                    {
                        "kind": "workflow",
                        "workflow_id": definition["id"],
                        "task": "Compare supplied notes",
                    }
                ),
            )
            assert failed["status"] == "failed"
            options = studio.retry.describe(failed["id"])
            queued = studio.retry.queue(
                failed["id"], {"digest": options["digest"], "step_id": "root/review"}
            )
            assert wait(studio, queued)["status"] == "completed"
            assert calls == ["researcher", "reviewer", "reviewer"]
            print("Retried the failed review and continued; completed research ran once.")

            # Generation succeeds, but local validation needs a missing workspace skill.
            failed_build = wait(
                studio,
                studio.start_run(
                    {
                        "kind": "build",
                        "task": "Propose a reviewer using the evidence skill",
                    }
                ),
            )
            assert failed_build["status"] == "failed"
            studio.catalog.save(
                "skills",
                {
                    "id": "evidence",
                    "name": "Evidence",
                    "data": {"content": "Check sources."},
                },
            )
            options = studio.retry.describe(failed_build["id"])
            step = options["steps"][0]
            assert step["uses_saved_output"]
            before = len(build_calls)
            recovered = wait(
                studio,
                studio.retry.queue(
                    failed_build["id"],
                    {
                        "digest": options["digest"],
                        "step_id": step["id"],
                    },
                ),
            )
            assert recovered["status"] == "completed", recovered
            assert len(build_calls) == before
            print("Retried only build validation and saving; no additional LLM calls.")
        finally:
            studio.close()


if __name__ == "__main__":
    main()

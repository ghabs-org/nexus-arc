import json
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def test_designers_refine_in_isolated_workspaces_and_never_activate_or_save_resources(tmp_path):
    contexts = []

    class Model:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            contexts.append(context)
            assert context.workspace != tmp_path and context.workspace.is_dir()
            assert not list(context.workspace.iterdir())
            if "Design a schedule template" in context.prompt:
                data = {
                    "target_kind": "agent",
                    "target_id": "assistant",
                    "task": "Review",
                    "cron": "0 9 * * 1-5",
                    "timezone": "Europe/Rome",
                    "enabled": True,
                }
            else:
                data = {
                    "content": "Summarize {{Topic}}",
                    "variables": {"Topic": "News"},
                    "tags": ["summary"],
                    "shortcut": "summarize",
                }
            return AgentResult(
                success=True, output=json.dumps({"name": "Reviewed draft", "data": data})
            )

    model = Model()
    studio = StudioService(tmp_path, builder_factory=lambda name: model)
    try:
        studio.save_definition(
            "agents", {"name": "Assistant", "provider": "codex", "instructions": "Help"}
        )
        for kind in ("prompt", "schedule"):
            queued = studio.start_run(
                {
                    "kind": "design",
                    "design_kind": kind,
                    "provider": "codex",
                    "task": "Refine it",
                    "conversation": "Keep the existing topic variable",
                }
            )
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                run = studio.run(queued["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            assert run["status"] == "completed", run
            if kind == "schedule":
                assert not run["design_proposal"]["data"]["enabled"]
        assert not studio.catalog.list("prompts") and not studio.catalog.list("schedules")
        assert all(not context.workspace.exists() for context in contexts)
        assert all("Keep the existing topic variable" in context.prompt for context in contexts)
    finally:
        studio.close()

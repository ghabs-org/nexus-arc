"""Consume prompt versions and calendar lifecycle using synthetic local providers."""

import argparse
import json
import tempfile
import time
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_prompts import StudioPromptHistory
from nexus.core.studio_schedules import StudioSchedules
from nexus.studio import make_studio_handler


class DemoProvider:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        if context.agent_name == "studio-designer":
            if "Design a schedule template" in context.prompt:
                data = {
                    "target_kind": "agent",
                    "target_id": "assistant",
                    "task": "/daily",
                    "cron": "0 9 * * 1-5",
                    "timezone": "Europe/Rome",
                    "enabled": True,
                }
                proposal = {"name": "Morning review", "data": data}
            else:
                proposal = {
                    "name": "Daily briefing",
                    "data": {
                        "content": "Summarize {{Topic}}"
                        if "shorter" in context.prompt.lower()
                        else "Review {{Topic}} and explain the main findings.",
                        "variables": {"Topic": "Invoices"},
                        "tags": ["daily"],
                        "shortcut": "briefing",
                    },
                }
            return AgentResult(success=True, output=json.dumps(proposal))
        if context.agent_name == "quick-build":
            return AgentResult(
                success=True,
                output=json.dumps(
                    {
                        "name": "Draft review",
                        "summary": "Synthetic prompt insertion check",
                        "agents": [
                            {
                                "id": "draft-review",
                                "name": "Draft review",
                                "description": "Review a selected prompt topic",
                                "provider": "codex",
                                "instructions": "Review the supplied task",
                            }
                        ],
                        "workflow": {
                            "id": "draft-flow",
                            "name": "Draft flow",
                            "description": "Run one synthetic reviewer",
                            "steps": [
                                {
                                    "id": "review",
                                    "name": "Review",
                                    "agent_type": "draft-review",
                                    "prompt": "{task}",
                                }
                            ],
                        },
                    }
                ),
            )
        return AgentResult(success=True, output="Synthetic reviewed task: " + context.prompt[-200:])


def create_demo(root):
    provider = DemoProvider()
    studio = StudioService(
        root, provider_factory=lambda _: provider, builder_factory=lambda _: provider
    )
    studio.save_definition(
        "agents",
        {"name": "Assistant", "provider": "codex", "instructions": "Review the supplied topic"},
    )
    first = studio.catalog.save(
        "prompts",
        {
            "name": "Daily review",
            "data": {
                "content": "Review {{Topic}}",
                "variables": {"Topic": "Invoices"},
                "tags": ["daily"],
                "shortcut": "daily",
            },
        },
    )
    studio.catalog.save(
        "prompts",
        {
            **first,
            "data": {
                "content": "Summarize {{Topic}}",
                "variables": {"Topic": "Payments"},
                "tags": ["brief"],
                "shortcut": "daily",
            },
        },
    )
    schedule = studio.catalog.save(
        "schedules",
        {
            "name": "Weekday review",
            "data": {
                "target_kind": "agent",
                "target_id": "assistant",
                "task": "/daily",
                "cron": "0 9 * * 1-5",
                "timezone": "Europe/Rome",
                "enabled": False,
            },
        },
    )
    return studio, first, schedule


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Open a disposable loopback browser fixture with synthetic scheduled execution",
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nexus-calendar-") as directory:
        studio, prompt, schedule = create_demo(Path(directory))
        try:
            if args.serve:
                studio.operations.start()
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), make_studio_handler(studio, "synthetic-calendar-token")
                )
                print(f"Synthetic Studio: http://127.0.0.1:{server.server_port}/", flush=True)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                return
            history, calendars = StudioPromptHistory(studio), StudioSchedules(studio)
            assert history.compare(prompt["id"], 1)["changed_fields"]
            restored = history.restore(prompt["id"], {"revision": 2, "restore_revision": 1})
            assert restored["revision"] == 3
            assert studio.catalog.expand({"task": "/daily"})["expanded_task"] == "Review Invoices"
            assert len(calendars.details(schedule["id"])["upcoming"]) == 5
            now = datetime.now(UTC)
            once = studio.catalog.save(
                "schedules",
                {
                    "name": "Overdue review",
                    "data": {
                        "target_kind": "agent",
                        "target_id": "assistant",
                        "task": "/daily",
                        "run_at": (now - timedelta(minutes=1)).isoformat(),
                        "enabled": True,
                    },
                },
            )
            studio.operations.tick(now)
            run = calendars.details(once["id"])["occurrences"][0]["run_id"]
            deadline = time.monotonic() + 5
            while (
                studio.run(run)["status"] in {"queued", "running"} and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert studio.run(run)["status"] == "completed"
            paused = calendars.enabled(once["id"], {"revision": 1, "enabled": False})
            calendars.enabled(once["id"], {"revision": paused["revision"], "enabled": True})
            studio.operations.tick(now)
            assert len(calendars.details(once["id"])["occurrences"]) == 1
            print(
                "Prompt comparison/restoration, variable expansion and once-only scheduling verified."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

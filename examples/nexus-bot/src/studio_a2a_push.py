"""Exercise rich task output and callback delivery against a local simulation.

Start: postman simulation run postman/simulations/studio-a2a-push.sim.yaml --port 49648
Run: PYTHONPATH=. python examples/nexus-bot/src/studio_a2a_push.py [--sql] [--failure]
"""

import argparse
import base64
import json
import os
import tempfile
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def exercise(
    receiver="http://127.0.0.1:49648/receiver", *, sql=False, failure=False, redirect=False
):
    # Disposable fixtures only. Production must provide its own private key.
    previous = {
        key: os.environ.get(key)
        for key in ("NEXUS_CREDENTIALS_MASTER_KEY", "NEXUS_A2A_EXAMPLE_TOKEN")
    }
    os.environ["NEXUS_CREDENTIALS_MASTER_KEY"] = base64.b64encode(b"x" * 32).decode()
    os.environ["NEXUS_A2A_EXAMPLE_TOKEN"] = "synthetic-a2a-example-local-only-token"
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-a2a-push-") as directory:
            root = Path(directory)
            options = (
                {
                    "url": "sqlite:///" + str(root / "state.sqlite"),
                    "namespace": "push-example",
                    "workers": 1,
                }
                if sql
                else None
            )
            studio = StudioService(root / "workspace", state_options=options)
            try:
                artifact = studio.catalog.save(
                    "artifacts",
                    {
                        "name": "Report",
                        "data": {
                            "filename": "report.txt",
                            "content_base64": base64.b64encode(b"Local report evidence").decode(),
                        },
                    },
                )

                class ReportProvider:
                    async def check_availability(self):
                        return True

                    async def execute_agent(self, context):
                        return AgentResult(
                            success=True,
                            output="Report ready",
                            metadata={
                                "structured_content": [
                                    {"type": "json", "data": {"count": 3}},
                                    {
                                        "type": "artifact",
                                        "artifact_id": artifact["id"],
                                        "revision": 1,
                                    },
                                ]
                            },
                        )

                studio.provider_factory = lambda _: ReportProvider()
                agent = studio.save_definition(
                    "agents",
                    {
                        "name": "Report publisher",
                        "provider": "codex",
                        "system_prompt": "Publish the local fixture",
                    },
                )
                url = receiver.rstrip("/") + (
                    "/failure" if failure else "/redirect" if redirect else "/callback"
                )
                entry = studio.catalog.save(
                    "entrypoints",
                    {
                        "name": "Report callback",
                        "data": {
                            "target_kind": "agent",
                            "target_id": agent["id"],
                            "enabled": True,
                            "a2a_enabled": True,
                            "token_env": "NEXUS_A2A_EXAMPLE_TOKEN",
                            "a2a_push": {"destinations": [url], "allow_private": True},
                        },
                    },
                )
                params = {
                    "message": {
                        "role": "ROLE_USER",
                        "messageId": "example-report",
                        "parts": [{"text": "Prepare report"}],
                    },
                    "configuration": {
                        "taskPushNotificationConfig": {
                            "id": "receiver",
                            "url": url,
                            "authentication": {
                                "scheme": "Bearer",
                                "credentials": "synthetic-callback-secret",
                            },
                        }
                    },
                }
                result, _ = studio.a2a.invoke(entry, "send", params, "1.0", "http://localhost")
                task = result["task"]
                assert task["status"]["state"] == "TASK_STATE_COMPLETED"
                assert (
                    base64.b64decode(task["artifacts"][-1]["parts"][0]["raw"])
                    == b"Local report evidence"
                )
                for now in (100, 105, 115, 200):
                    studio.a2a.push.tick(now)
                state = studio.records("a2a-push")[0]["status"]
                assert state == ("failed" if failure or redirect else "delivered"), state
                assert (
                    studio.a2a.invoke(entry, "send", params, "1.0", "http://localhost")[0]["task"][
                        "id"
                    ]
                    == task["id"]
                )
                assert len(studio.records("runs")) == 1
                return {
                    "backend": "sqlite" if sql else "files",
                    "delivery": state,
                    "tasks_created": 1,
                    "json_and_file": True,
                }
            finally:
                studio.close()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", default="http://127.0.0.1:49648/receiver")
    parser.add_argument("--sql", action="store_true")
    parser.add_argument("--failure", action="store_true")
    parser.add_argument("--redirect", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            exercise(args.receiver, sql=args.sql, failure=args.failure, redirect=args.redirect)
        )
    )

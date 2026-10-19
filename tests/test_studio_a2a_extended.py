import asyncio
import base64
import time

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_a2a_server import A2AError


def wait(service, identity, expected):
    for _ in range(500):
        run = service.run(identity)
        if run["status"] not in {"queued", "running"}:
            assert run["status"] == expected, run
            return run
        time.sleep(0.01)
    pytest.fail("Task did not reach expected status")


def test_a2a_structured_input_and_continuation_never_approve_gates(tmp_path):
    service = StudioService(tmp_path)
    try:
        workflow = service.save_definition(
            "workflows",
            {
                "name": "Ask input",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {
                            "id": "question",
                            "kind": "input",
                            "name": "Which city?",
                            "output_schema": {"type": "string"},
                        },
                        {"id": "gate", "kind": "approval", "depends_on": ["question"]},
                    ],
                    "output_mapping": {"ref": "nodes.question"},
                },
            },
        )
        entry = service.catalog.save(
            "entrypoints",
            {
                "name": "Questions",
                "data": {
                    "target_kind": "workflow",
                    "target_id": workflow["id"],
                    "token_env": "FIXTURE_A2A_TOKEN",
                    "enabled": True,
                    "a2a_enabled": True,
                },
            },
        )
        message = {
            "role": "ROLE_USER",
            "messageId": "ask-city",
            "parts": [{"data": {"request": "choose city"}}],
        }
        task_id = service.a2a.send(entry, {"message": message}, "1.0")
        paused = wait(service, task_id, "waiting")
        assert paused["awaiting_input"][0]["name"] == "Which city?"
        follow = {
            "role": "ROLE_USER",
            "messageId": "supply-city",
            "taskId": task_id,
            "parts": [{"text": "Rome"}],
        }
        assert service.a2a.send(entry, {"message": follow}, "1.0") == task_id
        gate = wait(service, task_id, "waiting")
        assert gate["awaiting_approval"] and not gate.get("awaiting_input")
        with pytest.raises(A2AError, match="approval gates"):
            service.a2a.send(entry, {"message": {**follow, "messageId": "cannot-approve"}}, "1.0")
        # Exact continuation retry returns the same task without another execution.
        assert service.a2a.send(entry, {"message": follow}, "1.0") == task_id
    finally:
        service.close()


def test_a2a_inline_file_and_deduplication(tmp_path):
    service = StudioService(tmp_path)
    try:
        workflow = service.save_definition(
            "workflows",
            {
                "name": "Process attachment",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {
                            "id": "result",
                            "kind": "switch",
                            "cases": [
                                {
                                    "condition": {"left": 1, "op": "eq", "right": 1},
                                    "value": "attached",
                                }
                            ],
                        }
                    ],
                },
            },
        )
        entry = service.catalog.save(
            "entrypoints",
            {
                "name": "Files",
                "data": {
                    "target_kind": "workflow",
                    "target_id": workflow["id"],
                    "token_env": "FIXTURE_A2A_TOKEN",
                    "enabled": True,
                    "a2a_enabled": True,
                },
            },
        )
        message = {
            "role": "user",
            "messageId": "file-1",
            "parts": [
                {"kind": "text", "text": "Read note"},
                {
                    "kind": "file",
                    "file": {
                        "name": "note.txt",
                        "bytes": base64.b64encode(b"Source text").decode(),
                    },
                },
            ],
        }
        identity = service.a2a.send(entry, {"message": message}, "0.3")
        wait(service, identity, "completed")
        assert service.a2a.send(entry, {"message": message}, "0.3") == identity
        attached = [r for r in service.catalog.list("artifacts") if r["id"].startswith("a2a-")]
        assert len(attached) == 1 and service.artifacts.get(attached[0]) == b"Source text"
    finally:
        service.close()


def test_running_task_cancellation_acknowledges_local_execution_only(tmp_path):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            while True:
                await asyncio.sleep(0.01)

    service = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        run = service.start_run({"task": "Long running task"})
        for _ in range(100):
            if service.run(run["id"])["status"] == "running":
                break
            time.sleep(0.01)
        requested = service.cancel_run(run["id"])
        assert requested["cancel_requested"]
        stopped = wait(service, run["id"], "cancelled")
        assert "external effects may still complete" in stopped["error"]
        with pytest.raises(ValueError, match="terminal"):
            service.cancel_run(run["id"])
    finally:
        service.close()

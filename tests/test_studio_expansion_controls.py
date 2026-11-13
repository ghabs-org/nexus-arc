"""Legacy imports, structured grading, output blocking and remote continuations."""

import json
import time
from unittest.mock import AsyncMock

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_evaluations import additional_checks, validate_case
from nexus.core.studio_memory_sources import StudioMemorySources


def done(app, run):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        current = app.run(run["id"])
        if current["status"] not in {"running", "queued"}:
            return current
        time.sleep(0.01)
    pytest.fail("Run did not finish")


def test_memory_import_is_idempotent_and_cannot_import_system_instructions(tmp_path):
    app = StudioService(tmp_path)
    try:
        source = {
            "format": "nexus-arc-memory/v1",
            "conversation": {
                "title": "Legacy notes",
                "owner_id": "other-user",
                "messages": [
                    {"role": "user", "text": "Evidence"},
                    {"role": "assistant", "text": "Reviewed"},
                ],
            },
        }
        first = app.workbench.memory_sources.import_document(source, None)
        again = app.workbench.memory_sources.import_document(source, None)
        assert first["id"] == again["id"] and again["already_imported"] is True
        assert app.session(first["id"])["owner_id"] is None
        source["conversation"]["messages"][0]["role"] = "system"
        with pytest.raises(ValueError, match="user and assistant"):
            app.workbench.memory_sources.import_document(source, None)
    finally:
        app.close()


def test_legacy_redis_reads_transcript_only_after_mapped_ownership_check(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGACY_REDIS", "redis://localhost:6379/0")
    read = []

    class Redis:
        def hget(self, key, chat):
            assert key == "user_chats:42"
            return json.dumps({"title": "Owned chat"}) if chat == "own" else None

        def lrange(self, key, start, end):
            read.append(key)
            return [json.dumps({"role": "user", "text": "Owned evidence"})]

        def close(self):
            pass

    app = StudioService(tmp_path)
    try:
        sources = StudioMemorySources(
            app.workbench,
            {"old": {"kind": "redis", "url_env": "LEGACY_REDIS", "identities": {"desktop": 42}}},
            lambda url: Redis(),
        )
        with pytest.raises(PermissionError, match="does not belong"):
            sources.import_chat("old", "other-user-chat", None)
        assert read == []
        imported = sources.import_chat("old", "own", None)
        assert (
            read == ["chat_history:own"]
            and app.session(imported["id"])["messages"][0]["content"] == "Owned evidence"
        )
    finally:
        app.close()


@pytest.mark.asyncio
async def test_structured_grading_and_malformed_judge_never_pass_by_default():
    case = validate_case(
        {
            "input": "Return status",
            "checks": [
                {"type": "json_equals", "path": ["status"], "expected": "ready"},
                {"type": "number_range", "path": ["score"], "min": 0.7, "max": 1},
            ],
        }
    )
    result = await additional_checks(None, case, '{"status":"ready","score":0.9}', "run", None)
    assert all(item["passed"] for item in result)
    result = await additional_checks(None, case, '{"status":"ready","score":true}', "run", None)
    assert result[0]["passed"] and not result[1]["passed"]
    case = validate_case(
        {
            "input": "Review",
            "judge": {"provider": "codex", "rubric": "Evidence based", "threshold": 0.8},
        }
    )

    class Judge:
        _invoke = AsyncMock(return_value=AgentResult(success=True, output='{"score":"perfect"}'))

    result = await additional_checks(Judge(), case, "Answer", "run", "alice")
    assert result[0]["passed"] is False and "valid score" in result[0]["error"]


def test_guarded_output_is_buffered_blocked_and_not_saved_in_traces(tmp_path):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent_streaming(self, *args):
            pytest.fail("Guarded output streamed before checks")

        async def execute_agent(self, context):
            return AgentResult(success=True, output="SECRET-OUTPUT")

    app = StudioService(tmp_path, provider_factory=lambda name: Provider())
    try:
        app.save_definition(
            "agents", {"name": "Guarded", "guardrails": {"forbidden_phrases": ["SECRET-OUTPUT"]}}
        )
        run = done(app, app.start_run({"task": "PRIVATE-INPUT", "agent_id": "guarded"}))
        assert run["status"] == "failed" and run["output"] == ""
        assert "guardrail" in run["error"]
        traces = app.workbench.get(["traces", run["id"]], None)
        assert traces["guardrails"][0]["status"] == "blocked"
        assert "PRIVATE-INPUT" not in json.dumps(traces) and "SECRET-OUTPUT" not in json.dumps(
            traces
        )
    finally:
        app.close()


def test_remote_input_continues_existing_task_without_submitting_a_new_task(tmp_path):
    messages = []
    origin = "http://127.0.0.1:9998"

    def client(url, body, headers):
        if body is None:
            return {
                "supportedInterfaces": [
                    {"url": origin + "/rpc", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
                ]
            }
        message = body["params"]["message"]
        messages.append(message)
        task = {
            "id": "task-1",
            "contextId": "context-1",
            "status": {"state": "input-required" if len(messages) == 1 else "completed"},
            "artifacts": [{"parts": [{"text": "Completed with input"}]}],
        }
        return {"jsonrpc": "2.0", "id": body["id"], "result": {"task": task}}

    app = StudioService(tmp_path, external_options={"request_client": client})
    try:
        app.catalog.save(
            "externalagents",
            {"name": "Remote", "data": {"url": origin, "allow_private": True, "enabled": True}},
        )
        first = done(
            app,
            app.start_run({"kind": "external", "external_agent_id": "remote", "task": "Review"}),
        )
        assert first["status"] == "waiting" and len(messages) == 1
        second = done(
            app, app.external.continue_task(first["id"], {"task": "Additional evidence"}, None)
        )
        assert second["status"] == "completed", second
        assert (
            len(messages) == 2
            and messages[1]["taskId"] == "task-1"
            and messages[1]["contextId"] == "context-1"
        )
        assert app.run(first["id"])["status"] == "completed"
    finally:
        app.close()


def test_yaml_parallel_group_runs_together_and_keeps_outputs(tmp_path):
    import asyncio
    import threading
    import time

    from nexus.core.models import AgentResult
    from nexus.core.studio import StudioService

    barrier = threading.Barrier(2)
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            if "Concurrent" in context.prompt:
                await asyncio.to_thread(barrier.wait, 3)
            return AgentResult(success=True, output="Evidence")

    app = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        app.save_definition("agents", {"name": "Researcher", "instructions": "Investigate"})
        app.save_definition(
            "workflows",
            {
                "name": "Parallel YAML",
                "steps": [
                    {"id": "first", "agent_type": "researcher", "prompt": "Concurrent A"},
                    {
                        "id": "second",
                        "agent_type": "researcher",
                        "prompt": "Concurrent B",
                        "parallel_with": ["first"],
                    },
                    {"id": "third", "agent_type": "researcher", "prompt": "Combine {result}"},
                ],
            },
        )
        run = app.start_run({"kind": "workflow", "workflow_id": "parallel-yaml", "task": "Check"})
        deadline = time.monotonic() + 8
        while app.run(run["id"])["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
        result = app.run(run["id"])
        assert result["status"] == "completed", result
        assert len(calls) == 3
        assert "Evidence" in calls[2]
    finally:
        app.close()

"""Rich A2A results use real tasks and independent HTTP clients."""

import base64
import json

import pytest

from nexus.core.models import AgentResult
from tests.test_studio_a2a_http import gateway as gateway
from tests.test_studio_a2a_http import message, replace_workflow


def save_file(studio, data=b"original output", **kwargs):
    return studio.catalog.save(
        "artifacts",
        {
            "name": "Published note",
            "data": {"filename": "result.txt", "content_base64": base64.b64encode(data).decode()},
            **kwargs,
        },
    )


def publish(studio, identity, *parts):
    studio._update_run(identity, content_version=1, structured_content=list(parts))


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_workflow_result_preserves_json_type_across_bindings(gateway):
    studio, entry, _, request = gateway
    entry = replace_workflow(
        studio,
        entry,
        [
            {
                "id": "value",
                "kind": "switch",
                "cases": [
                    {
                        "condition": {"left": 1, "op": "eq", "right": 1},
                        "value": {"cities": ["Rome"], "total": 2, "verified": True},
                    }
                ],
            }
        ],
    )
    payload = {**message(), "configuration": {"acceptedOutputModes": ["application/json"]}}
    status, result, _ = request("/rest/message:send", payload)
    assert status == 200, result
    task = result["task"]
    value = json.loads(task["artifacts"][0]["parts"][0]["text"])
    assert task["artifacts"][1]["parts"] == [{"data": value, "mediaType": "application/json"}]
    assert studio.run(task["id"])["structured_content"][0]["data"] == value
    old = request(
        "",
        {"jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": {"id": task["id"]}},
        headers={"A2A-Version": "0.3"},
    )[1]["result"]
    assert old["artifacts"][1]["parts"] == [{"kind": "data", "data": value}]
    assert request("/rest/message:send", payload)[1]["task"] == task
    assert len(studio.records("runs")) == 1


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_provider_publishes_pinned_artifact_and_json_without_storage_details(gateway):
    studio, entry, _, request = gateway
    artifact = save_file(studio)

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(
                success=True,
                output="Report ready",
                metadata={
                    "structured_content": [
                        {"type": "json", "data": {"count": 1}},
                        {"type": "artifact", "artifact_id": artifact["id"], "revision": 1},
                    ]
                },
            )

    studio.provider_factory = lambda _: Provider()
    agent = studio.save_definition(
        "agents", {"name": "Publisher", "provider": "codex", "system_prompt": "Publish report"}
    )
    studio.catalog.save(
        "entrypoints",
        {
            "id": entry["id"],
            "revision": entry["revision"],
            "name": entry["name"],
            "data": {**entry["data"], "target_kind": "agent", "target_id": agent["id"]},
        },
    )
    status, result, _ = request("/rest/message:send", message())
    assert status == 200, result
    task = result["task"]
    files = task["artifacts"][-1]
    assert files["parts"] == [
        {
            "filename": "result.txt",
            "raw": base64.b64encode(b"original output").decode(),
            "mediaType": "text/plain",
        }
    ]
    save_file(studio, b"newer private revision", id=artifact["id"], revision=1)
    assert request("/rest/tasks/" + task["id"])[1] == task
    assert "blob_key" not in json.dumps(task) and "store_id" not in json.dumps(task)
    legacy = studio.a2a.task(entry, task["id"], "0.3")["artifacts"][-1]
    assert base64.b64decode(legacy["parts"][0]["file"]["bytes"]) == b"original output"


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_input_files_never_become_implicit_output(gateway):
    studio, entry, _, request = gateway
    payload = message()
    payload["message"]["parts"].append(
        {"raw": base64.b64encode(b"private input").decode(), "filename": "input.txt"}
    )
    task = request("/rest/message:send", payload)[1]["task"]
    assert len([a for a in studio.catalog.list("artifacts") if a["id"].startswith("a2a-")]) == 1
    assert not any("raw" in p for a in task["artifacts"] for p in a["parts"])


@pytest.mark.parametrize("invalid", ["@@@", "YQ==\n"])
def test_all_files_validate_before_any_persistence(gateway, invalid):
    studio, _, _, request = gateway
    payload = message()
    payload["message"]["parts"] = [
        {"raw": "YQ==", "filename": "valid.txt"},
        {"raw": invalid, "filename": "invalid.txt"},
    ]
    assert request("/rest/message:send", payload)[0] == 400
    assert not studio.catalog.list("artifacts") and not studio.records("runs")


@pytest.mark.parametrize("data", [None, False, 42, [1, "two"], "value"])
def test_version_one_accepts_all_json_values(gateway, data):
    studio, entry, _, request = gateway
    payload = message()
    payload["message"]["parts"] = [{"data": data}]
    assert request("/rest/message:send", payload)[0] == 200


@pytest.mark.parametrize(
    "modes", [None, {}, "application/json", [False], ["video/unsupported"], ["text/plain"] * 21]
)
def test_invalid_output_preferences_never_start_tasks(gateway, modes):
    studio, _, _, request = gateway
    assert (
        request(
            "/rest/message:send", {**message(), "configuration": {"acceptedOutputModes": modes}}
        )[0]
        == 400
    )
    assert not studio.records("runs")


def test_output_limits_missing_revisions_and_foreign_owner_fail_closed(gateway, monkeypatch):
    studio, entry, _, request = gateway
    task = request("/rest/message:send", message())[1]["task"]
    artifact = save_file(studio)
    part = {"type": "artifact", "artifact_id": artifact["id"], "revision": 2}
    publish(studio, task["id"], part)
    assert request("/rest/tasks/" + task["id"])[0] == 500
    publish(studio, task["id"], {**part, "revision": 1})
    import nexus.core.studio_a2a_content as content

    monkeypatch.setattr(content, "INLINE_LIMIT", 2)
    assert request("/rest/tasks/" + task["id"])[0] == 500
    monkeypatch.setattr(content, "INLINE_LIMIT", 16 * 1024 * 1024)
    foreign = save_file(studio, id="foreign-result", requester_nexus_id="bob")
    publish(studio, task["id"], {**part, "artifact_id": foreign["id"], "revision": 1})
    assert request("/rest/tasks/" + task["id"])[0] == 500
    publish(studio, task["id"], {"type": "json", "data": float("nan")})
    assert request("/rest/tasks/" + task["id"])[0] == 500


def test_continuation_context_cannot_cross_task(gateway):
    studio, entry, _, request = gateway
    entry = replace_workflow(
        studio,
        entry,
        [{"id": "question", "kind": "input", "name": "City", "output_schema": {"type": "string"}}],
    )
    task = request("/rest/message:send", message())[1]["task"]
    follow = message("follow")
    follow["message"].update(taskId=task["id"], contextId="foreign-context")
    assert request("/rest/message:send", follow)[0] == 400
    assert studio.run(task["id"])["status"] == "waiting"
    follow["message"]["contextId"] = task["contextId"]
    assert (
        request("/rest/message:send", follow)[1]["task"]["status"]["state"]
        == "TASK_STATE_COMPLETED"
    )

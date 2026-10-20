"""Independent HTTP requests exercise the shared REST/JSON-RPC task service."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler
from tests.test_studio_a2a_extended import wait


@pytest.fixture
def gateway(tmp_path, monkeypatch, request):
    monkeypatch.setenv("HTTP_A2A_TOKEN", "synthetic-rest-key-local-only-1234")
    options = (
        None
        if getattr(request, "param", "files") == "files"
        else {
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "rest",
            "workers": 1,
        }
    )
    studio = StudioService(tmp_path / "workspace", state_options=options)
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Local task",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "words",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": {"ref": "input"}},
                    }
                ],
                "output_mapping": {"ref": "nodes.words"},
            },
        },
    )
    entry = studio.catalog.save(
        "entrypoints",
        {
            "name": "REST fixture",
            "data": {
                "target_kind": "workflow",
                "target_id": workflow["id"],
                "token_env": "HTTP_A2A_TOKEN",
                "enabled": True,
                "a2a_enabled": True,
            },
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "csrf-test"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    base = origin + "/api/a2a/" + entry["id"]

    def request(
        path,
        body=None,
        method=None,
        token="synthetic-rest-key-local-only-1234",
        headers=None,
        raw=None,
    ):
        payload = (
            raw if raw is not None else json.dumps(body).encode() if body is not None else None
        )
        req = urllib.request.Request(
            base + path,
            data=payload,
            method=method or ("POST" if payload is not None else "GET"),
            headers={
                "Authorization": "Bearer " + token,
                "A2A-Version": "1.0",
                "Content-Type": "application/a2a+json",
                **(headers or {}),
            },
        )
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            data = response.read()
            return (
                response.status,
                data.decode()
                if response.headers.get_content_type() == "text/event-stream"
                else json.loads(data),
                response.headers,
            )

    yield studio, entry, base, request
    server.shutdown()
    server.server_close()
    studio.close()


def message(identity="one"):
    return {
        "message": {
            "role": "ROLE_USER",
            "messageId": identity,
            "parts": [{"text": "one two three"}],
        }
    }


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_bindings_share_identity_deduplication_polling_and_history(gateway):
    studio, entry, base, request = gateway
    status, sent, headers = request("/rest/message:send", message())
    assert status == 200 and headers.get_content_type() == "application/a2a+json"
    task = sent["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "3"
    status, rpc, _ = request(
        "", {"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": message()}
    )
    assert status == 200 and rpc["result"]["task"]["id"] == task["id"]
    status, fetched, _ = request("/rest/tasks/" + task["id"] + "?historyLength=1")
    assert (
        status == 200
        and fetched["id"] == task["id"]
        and fetched["history"][0]["messageId"] == "one"
    )
    status, listed, _ = request("/rest/tasks?pageSize=1&contextId=one&status=TASK_STATE_COMPLETED")
    assert status == 200 and listed["totalSize"] == 1
    assert listed["tasks"][0]["id"] == task["id"]
    assert len(studio.records("runs")) == 1


def test_cards_advertise_only_implemented_bindings_and_output_modes(gateway):
    _, _, _, request = gateway
    status, card, _ = request("/rest/extendedAgentCard")
    assert status == 200
    assert {i["protocolBinding"] for i in card["supportedInterfaces"]} == {"JSONRPC", "HTTP+JSON"}
    assert all(i["protocolVersion"] == "1.0" for i in card["supportedInterfaces"])
    assert not card["capabilities"]["pushNotifications"]
    assert {"text/plain", "application/json", "application/octet-stream"} <= set(
        card["defaultOutputModes"]
    )
    assert not any(mode.startswith("image/") for mode in card["defaultInputModes"])


@pytest.mark.parametrize(
    "path,body,headers,expected,reason",
    [
        ("/rest/tasks/missing", None, {}, 404, "TASK_NOT_FOUND"),
        (
            "/rest/message:send",
            {**message(), "configuration": {"taskPushNotificationConfig": {}}},
            {},
            400,
            "PUSH_NOTIFICATION_NOT_SUPPORTED",
        ),
        ("/rest/message:send", message(), {"A2A-Version": "0.3"}, 400, "VERSION_NOT_SUPPORTED"),
        ("/rest/message:send", message(), {"Origin": "https://another.example"}, 403, None),
        ("/rest/message:send", message(), {"Content-Type": "text/plain"}, 415, None),
        ("/rest/tasks?pageSize=0", None, {}, 400, None),
        ("/rest/tasks?pageSize=-1", None, {}, 400, None),
        ("/rest/tasks?pageSize=1&pageSize=2", None, {}, 400, None),
        ("/rest/tasks?requester_nexus_id=another", None, {}, 400, None),
        ("/rest/unknown", None, {}, 404, None),
        (
            "/rest/tasks/missing/pushNotificationConfigs",
            None,
            {},
            400,
            "PUSH_NOTIFICATION_NOT_SUPPORTED",
        ),
    ],
)
def test_rest_errors_are_typed_and_do_not_create_work(
    gateway, path, body, headers, expected, reason
):
    studio, _, _, request = gateway
    status, result, _ = request(path, body, headers=headers)
    assert status == result["error"]["code"] == expected
    assert "jsonrpc" not in result
    if reason:
        assert result["error"]["details"][0]["reason"] == reason
        assert result["error"]["details"][0]["domain"] == "a2a-protocol.org"
    assert studio.records("runs") == []


def test_authentication_and_cross_entrypoint_task_isolation(gateway):
    studio, entry, _, request = gateway
    assert request("/rest/tasks", token="wrong")[0] == 403
    first = request("/rest/message:send", message())[1]["task"]
    # Same bearer credentials do not grant another entrypoint's tasks.
    other = studio.catalog.save("entrypoints", {"name": "Other", "data": entry["data"]})
    mapping = studio._read(studio._path("a2a-tasks", first["id"]))
    mapping["entrypoint_id"] = other["id"]
    studio._write(studio._path("a2a-tasks", first["id"]), mapping)
    assert request("/rest/tasks/" + first["id"])[0] == 404
    assert request("/rest/tasks")[1]["totalSize"] == 0


def test_sse_uses_stream_response_and_never_repeats_submission(gateway):
    studio, _, _, request = gateway
    status, stream, headers = request("/rest/message:stream", message())
    assert status == 200 and headers.get_content_type() == "text/event-stream"
    events = [json.loads(line[6:]) for line in stream.splitlines() if line.startswith("data: ")]
    assert events and "task" in events[0]
    assert all("jsonrpc" not in event for event in events)
    identity = events[0]["task"]["id"]
    wait(studio, identity, "completed")
    assert request("/rest/tasks/" + identity)[0] == 200
    # Terminal subscribe has a typed error and never starts another run.
    status, error, _ = request("/rest/tasks/" + identity + ":subscribe", {})
    assert status == 400 and error["error"]["details"][0]["reason"] == "UNSUPPORTED_OPERATION"
    assert len(studio.records("runs")) == 1


def test_completed_cancel_reports_error_without_rewriting_completed_effect(gateway):
    studio, _, _, request = gateway
    task = request("/rest/message:send", message())[1]["task"]
    status, error, _ = request("/rest/tasks/" + task["id"] + ":cancel", {})
    assert status == 400 and error["error"]["details"][0]["reason"] == "TASK_NOT_CANCELABLE"
    assert studio.run(task["id"])["status"] == "completed"
    assert studio.run(task["id"])["output"] == "3"
    assert request("/rest/tasks/" + task["id"] + ":cancel", {"id": "different"})[0] == 400


def replace_workflow(studio, entry, nodes, output=None):
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Interactive task",
            "graph": {
                "version": 2,
                "nodes": nodes,
                "output_mapping": output or {"ref": "nodes"},
            },
        },
    )
    return studio.catalog.save(
        "entrypoints",
        {
            "id": entry["id"],
            "revision": entry["revision"],
            "name": entry["name"],
            "data": {**entry["data"], "target_id": workflow["id"]},
        },
    )


def test_cross_binding_continuation_cannot_approve_operator_gate(gateway):
    studio, entry, _, request = gateway
    entry = replace_workflow(
        studio,
        entry,
        [
            {"id": "question", "kind": "input", "output_schema": {"type": "string"}},
            {"id": "gate", "kind": "approval", "depends_on": ["question"]},
        ],
    )
    task = request("/rest/message:send", message())[1]["task"]
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    follow = {
        "message": {
            "role": "ROLE_USER",
            "messageId": "follow",
            "taskId": task["id"],
            "parts": [{"text": "Rome"}],
        }
    }
    rpc = {"jsonrpc": "2.0", "id": 2, "method": "SendMessage", "params": follow}
    continued = request("", rpc)[1]["result"]["task"]
    assert continued["id"] == task["id"]
    assert studio.run(task["id"])["awaiting_approval"]
    # Same message retries across bindings never resume an approval.
    assert request("/rest/message:send", follow)[1]["task"]["id"] == task["id"]
    follow["message"]["messageId"] = "cannot-approve"
    status, error, _ = request("/rest/message:send", follow)
    assert status == 400 and error["error"]["details"][0]["reason"] == "UNSUPPORTED_OPERATION"
    assert len(studio.records("runs")) == 1


def test_running_subscribe_reuses_task_and_revocation_closes_stream(gateway):
    import asyncio

    from nexus.tools import tool_registry

    studio, entry, base, request = gateway
    started, release = threading.Event(), threading.Event()
    calls = []

    async def effect():
        calls.append("effect")
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return "finished"

    tool_registry.register("rest:effect", effect, "Controlled local task effect")
    try:
        entry = replace_workflow(
            studio,
            entry,
            [{"id": "effect", "kind": "tool", "tool_id": "rest:effect", "kwargs": {}}],
        )
        payload = {**message(), "configuration": {"returnImmediately": True}}
        task = request("/rest/message:send", payload)[1]["task"]
        assert started.wait(3)
        subscription = urllib.request.Request(
            base + "/rest/tasks/" + task["id"] + ":subscribe",
            data=b"{}",
            headers={
                "Authorization": "Bearer synthetic-rest-key-local-only-1234",
                "A2A-Version": "1.0",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(subscription, timeout=5) as stream:
            first = json.loads(stream.readline().decode().removeprefix("data: "))
            assert first["task"]["id"] == task["id"]
            studio.catalog.save(
                "entrypoints",
                {
                    "id": entry["id"],
                    "revision": entry["revision"],
                    "name": entry["name"],
                    "data": {**entry["data"], "enabled": False},
                },
            )
            # Drain the separator; no further task payload may be published.
            assert not any(line.startswith(b"data:") for line in stream)
        assert calls == ["effect"] and len(studio.records("runs")) == 1
    finally:
        release.set()
        tool_registry.unregister("rest:effect", func=effect)


def test_running_cancel_reports_actual_state_and_does_not_undo_effect(gateway):
    import asyncio

    from nexus.tools import tool_registry

    studio, entry, _, request = gateway
    started, release = threading.Event(), threading.Event()
    calls = []

    async def effect():
        calls.append("already-performed")
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return "acknowledged"

    tool_registry.register("rest:cancel-effect", effect, "Local cancellation fixture")
    try:
        entry = replace_workflow(
            studio,
            entry,
            [{"id": "effect", "kind": "tool", "tool_id": "rest:cancel-effect", "kwargs": {}}],
        )
        task = request(
            "/rest/message:send", {**message(), "configuration": {"returnImmediately": True}}
        )[1]["task"]
        assert started.wait(3)
        status, cancelled, _ = request("/rest/tasks/" + task["id"] + ":cancel", {})
        assert status == 200 and cancelled["status"]["state"] in {
            "TASK_STATE_WORKING",
            "TASK_STATE_CANCELED",
        }
        wait(studio, task["id"], "cancelled")
        assert calls == ["already-performed"]
        assert "external effects may still complete" in studio.run(task["id"])["error"]
    finally:
        release.set()
        tool_registry.unregister("rest:cancel-effect", func=effect)

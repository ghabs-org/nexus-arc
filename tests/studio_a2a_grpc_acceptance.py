"""Optional explicit suite: requires nexus-arc[studio-grpc], no unexplained skips."""

import grpc
import pytest
from a2a.types import a2a_pb2 as messages
from a2a.types import a2a_pb2_grpc as service
from google.protobuf.json_format import ParseDict
from google.rpc import error_details_pb2, status_pb2

from nexus.core.studio_a2a_grpc import StudioA2AGRPC
from tests.test_studio_a2a_http import gateway as gateway
from tests.test_studio_a2a_http import message
from tests.test_studio_a2a_push import callback as callback

META = (("authorization", "Bearer synthetic-rest-key-local-only-1234"),)


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_grpc_structured_files_and_callback_crud_share_rest_tasks(callback):
    import base64

    from google.protobuf.json_format import MessageToDict

    from tests.test_studio_a2a_content import publish, save_file

    studio, entry, http, config, observed, _, _, _ = callback
    listener = StudioA2AGRPC(studio, entry["id"])
    try:
        with grpc.insecure_channel(listener.address) as channel:
            client = service.A2AServiceStub(channel)
            task = client.SendMessage(
                ParseDict(message(), messages.SendMessageRequest()), metadata=META, timeout=10
            ).task
            artifact = save_file(studio)
            publish(
                studio,
                task.id,
                {"type": "json", "data": [None, 3, True]},
                {"type": "artifact", "artifact_id": artifact["id"], "revision": 1},
            )
            rich = client.GetTask(messages.GetTaskRequest(id=task.id), metadata=META)
            assert MessageToDict(rich.artifacts[1].parts[0])["data"] == [None, 3, True]
            assert rich.artifacts[-1].parts[0].raw == b"original output"
            assert (
                http("/rest/tasks/" + task.id)[1]["artifacts"][-1]["parts"][0]["raw"]
                == base64.b64encode(b"original output").decode()
            )
            saved = client.CreateTaskPushNotificationConfig(
                ParseDict({**config, "taskId": task.id}, messages.TaskPushNotificationConfig()),
                metadata=META,
            )
            assert saved.id == "receiver" and not saved.authentication.credentials
            assert (
                client.GetTaskPushNotificationConfig(
                    messages.GetTaskPushNotificationConfigRequest(task_id=task.id, id="receiver"),
                    metadata=META,
                ).url
                == config["url"]
            )
            assert (
                len(
                    client.ListTaskPushNotificationConfigs(
                        messages.ListTaskPushNotificationConfigsRequest(task_id=task.id),
                        metadata=META,
                    ).configs
                )
                == 1
            )
            studio.a2a.push.tick(100)
            assert observed[0][2]["task"]["id"] == task.id
            client.DeleteTaskPushNotificationConfig(
                messages.DeleteTaskPushNotificationConfigRequest(task_id=task.id, id="receiver"),
                metadata=META,
            )
            assert not studio.records("a2a-push")
    finally:
        listener.close()


@pytest.fixture
def listener(gateway):
    studio, entry, base, http = gateway
    server = StudioA2AGRPC(studio, entry["id"])
    channel = grpc.insecure_channel(server.address)
    try:
        yield studio, entry, server, service.A2AServiceStub(channel), http
    finally:
        channel.close()
        server.close()


def reason(error):
    raw = dict(error.value.trailing_metadata())["grpc-status-details-bin"]
    status = status_pb2.Status.FromString(raw)
    info = error_details_pb2.ErrorInfo()
    assert status.details[0].Unpack(info)
    assert info.domain == "a2a-protocol.org"
    return info.reason


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_generated_client_interoperates_with_rest_and_legacy_rpc(listener):
    studio, _, _, client, http = listener
    result = client.SendMessage(
        ParseDict(message(), messages.SendMessageRequest()), metadata=META, timeout=10
    )
    identity = result.task.id
    assert result.task.status.state == messages.TASK_STATE_COMPLETED
    assert result.task.artifacts[0].parts[0].text == "3"
    assert http("/rest/message:send", message())[1]["task"]["id"] == identity
    assert client.GetTask(messages.GetTaskRequest(id=identity), metadata=META).id == identity
    assert client.ListTasks(messages.ListTasksRequest(page_size=1), metadata=META).total_size == 1
    assert len(studio.records("runs")) == 1


def test_grpc_card_tracks_running_listener_only(listener):
    _, _, server, client, http = listener
    card = client.GetExtendedAgentCard(messages.GetExtendedAgentCardRequest(), metadata=META)
    assert [i.protocol_binding for i in card.supported_interfaces] == [
        "JSONRPC",
        "HTTP+JSON",
        "GRPC",
    ]
    assert card.supported_interfaces[-1].url == server.address
    server.close()
    assert [
        i["protocolBinding"] for i in http("/rest/extendedAgentCard")[1]["supportedInterfaces"]
    ] == ["JSONRPC", "HTTP+JSON"]


def test_grpc_auth_and_typed_terminal_errors(listener):
    _, _, _, client, _ = listener
    with pytest.raises(grpc.RpcError) as denied:
        client.ListTasks(messages.ListTasksRequest(), timeout=5)
    assert denied.value.code() == grpc.StatusCode.PERMISSION_DENIED
    with pytest.raises(grpc.RpcError) as missing:
        client.GetTask(messages.GetTaskRequest(id="missing"), metadata=META, timeout=5)
    assert missing.value.code() == grpc.StatusCode.NOT_FOUND
    assert reason(missing) == "TASK_NOT_FOUND"
    result = client.SendMessage(ParseDict(message(), messages.SendMessageRequest()), metadata=META)
    with pytest.raises(grpc.RpcError) as cancelled:
        client.CancelTask(messages.CancelTaskRequest(id=result.task.id), metadata=META)
    assert cancelled.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert reason(cancelled) == "TASK_NOT_CANCELABLE"


def test_grpc_stream_and_unsupported_push_share_task_service(listener):
    studio, _, _, client, _ = listener
    with pytest.raises(grpc.RpcError) as empty_push:
        client.SendMessage(
            ParseDict(
                {**message(), "configuration": {"taskPushNotificationConfig": {}}},
                messages.SendMessageRequest(),
            ),
            metadata=META,
        )
    assert reason(empty_push) == "PUSH_NOTIFICATION_NOT_SUPPORTED"
    assert studio.records("runs") == []
    events = list(
        client.SendStreamingMessage(
            ParseDict(message(), messages.SendMessageRequest()), metadata=META, timeout=10
        )
    )
    assert events and events[0].HasField("task")
    with pytest.raises(grpc.RpcError) as unsupported:
        client.CreateTaskPushNotificationConfig(
            messages.TaskPushNotificationConfig(), metadata=META
        )
    assert unsupported.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert reason(unsupported) == "PUSH_NOTIFICATION_NOT_SUPPORTED"
    assert len(studio.records("runs")) == 1


def test_grpc_requires_tls_for_external_listener_and_closes_with_studio(gateway):
    studio, entry, _, _ = gateway
    with pytest.raises(ValueError, match="TLS"):
        StudioA2AGRPC(studio, entry["id"], host="0.0.0.0")
    server = StudioA2AGRPC(studio, entry["id"])
    studio.close()
    assert server.closed and not studio.a2a.grpc_servers


def test_grpc_continuation_reuses_input_task_without_approving_gate(listener):
    from tests.test_studio_a2a_http import replace_workflow

    studio, entry, _, client, http = listener
    replace_workflow(
        studio,
        entry,
        [
            {"id": "question", "kind": "input", "output_schema": {"type": "string"}},
            {"id": "gate", "kind": "approval", "depends_on": ["question"]},
        ],
    )
    original = client.SendMessage(
        ParseDict(message(), messages.SendMessageRequest()), metadata=META
    )
    identity = original.task.id
    assert original.task.status.state == messages.TASK_STATE_INPUT_REQUIRED
    follow = {
        "message": {
            "role": "ROLE_USER",
            "messageId": "grpc-follow",
            "taskId": identity,
            "parts": [{"text": "Rome"}],
        }
    }
    continued = client.SendMessage(ParseDict(follow, messages.SendMessageRequest()), metadata=META)
    assert continued.task.id == identity and studio.run(identity)["awaiting_approval"]
    assert http("/rest/message:send", follow)[1]["task"]["id"] == identity
    follow["message"]["messageId"] = "grpc-cannot-approve"
    with pytest.raises(grpc.RpcError) as rejected:
        client.SendMessage(ParseDict(follow, messages.SendMessageRequest()), metadata=META)
    assert reason(rejected) == "UNSUPPORTED_OPERATION"
    assert len(studio.records("runs")) == 1


@pytest.mark.parametrize("gateway", ["sqlite"], indirect=True)
def test_grpc_sql_restart_retains_task_mapping_and_input_receipt(gateway):
    from nexus.core.studio import StudioService
    from tests.test_studio_a2a_http import replace_workflow

    studio, entry, _, _ = gateway
    replace_workflow(
        studio,
        entry,
        [{"id": "question", "kind": "input", "output_schema": {"type": "string"}}],
        {"ref": "nodes.question"},
    )
    first = StudioA2AGRPC(studio, entry["id"])
    with grpc.insecure_channel(first.address) as channel:
        client = service.A2AServiceStub(channel)
        task = client.SendMessage(
            ParseDict(message(), messages.SendMessageRequest()), metadata=META
        ).task
    root = studio.root
    studio.close()
    restored = StudioService(
        root,
        state_options={
            "url": "sqlite:///" + str(root.parent / "state.sqlite"),
            "namespace": "rest",
            "workers": 1,
        },
    )
    listener = StudioA2AGRPC(restored, entry["id"])
    try:
        with grpc.insecure_channel(listener.address) as channel:
            client = service.A2AServiceStub(channel)
            assert (
                client.GetTask(messages.GetTaskRequest(id=task.id), metadata=META).status.state
                == messages.TASK_STATE_INPUT_REQUIRED
            )
            follow = {
                "message": {
                    "role": "ROLE_USER",
                    "messageId": "after-restart",
                    "taskId": task.id,
                    "parts": [{"text": "Rome"}],
                }
            }
            completed = client.SendMessage(
                ParseDict(follow, messages.SendMessageRequest()), metadata=META
            ).task
            assert (
                completed.id == task.id and completed.status.state == messages.TASK_STATE_COMPLETED
            )
            assert len(restored.records("runs")) == 1
    finally:
        restored.close()


@pytest.mark.parametrize("streaming", [False, True])
def test_listener_stop_releases_waiters_without_cancelling_task(listener, streaming):
    import time

    from tests.test_studio_a2a_http import replace_workflow

    studio, entry, server, client, http = listener
    replace_workflow(studio, entry, [{"id": "slow", "kind": "delay", "delay_seconds": 60}])
    request = ParseDict(message(), messages.SendMessageRequest())
    pending = (
        client.SendStreamingMessage(request, metadata=META)
        if streaming
        else client.SendMessage.future(request, metadata=META)
    )
    if streaming:
        assert next(pending).HasField("task")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        runs = studio.records("runs")
        if runs and runs[0]["status"] == "running":
            break
        time.sleep(0.01)
    assert len(runs) == 1 and runs[0]["status"] == "running"
    started = time.monotonic()
    server.close()
    assert time.monotonic() - started < 3
    identity = runs[0]["id"]
    assert http("/rest/tasks/" + identity)[1]["status"]["state"] == "TASK_STATE_WORKING"
    assert len(studio.records("runs")) == 1
    studio.cancel_run(identity)
    pending.cancel()

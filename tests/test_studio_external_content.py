"""Real independent A2A peers exercise uploads, results and read-only recovery."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_expansion_controls import done
from tests.test_studio_team import team as team


@pytest.fixture
def peer():
    state = {
        "mode": "inline",
        "requests": [],
        "messages": [],
        "version": "1.0",
        "input_modes": [
            "text/plain",
            "application/json",
            "application/octet-stream",
            "application/pdf",
        ],
    }
    state.update(file_entered=threading.Event(), file_release=threading.Event(), block_file=False)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, value, status=200, kind="application/json"):
            body = json.dumps(value).encode() if kind == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["requests"].append(("GET", self.path, self.headers.get("Authorization")))
            if self.path == "/card":
                return self.reply(
                    {
                        "name": "Independent peer",
                        "protocolVersion": "0.3.0",
                        "url": state["origin"] + "/rpc",
                        "preferredTransport": "JSONRPC",
                        "supportedInterfaces": [
                            {
                                "url": state["origin"] + "/rpc",
                                "protocolBinding": "JSONRPC",
                                "protocolVersion": "1.0",
                            }
                        ],
                        "defaultInputModes": state["input_modes"],
                        "defaultOutputModes": [
                            "text/plain",
                            "application/json",
                            "application/octet-stream",
                        ],
                        "capabilities": {
                            "streaming": True,
                            "pushNotifications": state.get("push", False),
                        },
                    }
                )
            if self.path == "/file":
                state["file_entered"].set()
                if state["block_file"]:
                    state["file_release"].wait(5)
                if state["mode"] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "/must-not-follow")
                    self.end_headers()
                    return
                return self.reply(b"URL file evidence", kind="application/octet-stream")
            self.reply({}, 501)

        def do_POST(self):
            state["requests"].append(("POST", self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer synthetic-peer-token":
                return self.reply({}, 401)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method, params = body["method"], body["params"]
            legacy = method.startswith(("message/", "tasks/"))
            if method in {"SendMessage", "SendStreamingMessage", "message/send", "message/stream"}:
                state["messages"].append(params["message"])
                if state.get("on_send"):
                    state["on_send"](params)
            raw = base64.b64encode(b"Inline file evidence").decode()
            file = (
                {"kind": "file", "file": {"name": "report.bin", "bytes": raw}}
                if legacy
                else {"raw": raw, "filename": "report.bin"}
            )
            data = {
                "data": {"cities": ["Rome"], "verified": True},
                **({"kind": "data"} if legacy else {}),
            }
            if state["mode"] in {"url", "redirect", "cross-origin"}:
                url = (
                    "https://unapproved.invalid"
                    if state["mode"] == "cross-origin"
                    else state["origin"]
                ) + "/file"
                file = (
                    {"kind": "file", "file": {"name": "report.bin", "uri": url}}
                    if legacy
                    else {"url": url, "filename": "report.bin"}
                )
            parts = [{"text": "Remote report", **({"kind": "text"} if legacy else {})}, data, file]
            if state["mode"] == "malformed":
                parts.append(
                    {"raw": "@@@", "filename": "bad.bin"}
                    if not legacy
                    else {"kind": "file", "file": {"name": "bad.bin", "bytes": "@@@"}}
                )
            if state["mode"] == "path":
                if legacy:
                    file["file"]["name"] = "../escape"
                else:
                    file["filename"] = "../escape"
            paused = state["mode"] == "pause" and len(state["messages"]) == 1
            working = (
                state["mode"] in {"changed-id", "changed-context"} and len(state["requests"]) == 2
            )
            task = {
                "id": "remote-task",
                "contextId": "remote-context",
                "status": {
                    "state": ("input-required" if paused else "working" if working else "completed")
                    if legacy
                    else (
                        "TASK_STATE_INPUT_REQUIRED"
                        if paused
                        else "TASK_STATE_WORKING"
                        if working
                        else "TASK_STATE_COMPLETED"
                    )
                },
                "artifacts": []
                if paused or working
                else [{"artifactId": "report", "parts": parts}],
                **({"kind": "task"} if legacy else {}),
            }
            if method in {"GetTask", "tasks/get"}:
                if state["mode"] == "changed-id":
                    task["id"] = "foreign-task"
                if state["mode"] == "changed-context":
                    task["contextId"] = "foreign-context"
            result = (
                task
                if legacy or method in {"GetTask", "tasks/get", "CancelTask", "tasks/cancel"}
                else {"task": task}
            )
            envelope = {"jsonrpc": "2.0", "id": body["id"], "result": result}
            if method in {"SendStreamingMessage", "message/stream"}:
                encoded = ("data: " + json.dumps(envelope) + "\n\n").encode()
                return self.reply(encoded, kind="text/event-stream")
            self.reply(envelope)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["origin"] = f"http://127.0.0.1:{server.server_port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield state
    finally:
        state["file_release"].set()
        server.shutdown()
        server.server_close()


def connection(studio, peer, version="1.0", streaming=False, actor=None):
    return studio.catalog.save(
        "externalagents",
        {
            "name": "Independent peer",
            "requester_nexus_id": actor,
            "data": {
                "url": peer["origin"] + "/rpc",
                "card_path": "/card",
                "token_env": "PEER_TOKEN",
                "protocol_version": version,
                "streaming": streaming,
                "allow_private": True,
                "enabled": True,
            },
        },
    )


@pytest.fixture
def client(tmp_path, monkeypatch, request):
    monkeypatch.setenv("PEER_TOKEN", "synthetic-peer-token")
    options = (
        {"url": "sqlite:///" + str(tmp_path / "state.sqlite"), "namespace": "client", "workers": 1}
        if getattr(request, "param", "files") == "sqlite"
        else None
    )
    studio = StudioService(tmp_path / "client", state_options=options)
    try:
        yield studio
    finally:
        studio.close()


def artifact(studio, data=b"Uploaded evidence", actor=None, **kwargs):
    return studio.catalog.save(
        "artifacts",
        {
            "id": "upload",
            "name": "Upload",
            "requester_nexus_id": actor,
            "data": {"filename": "upload.bin", "content_base64": base64.b64encode(data).decode()},
            **kwargs,
        },
    )


def run(studio, profile, **payload):
    return done(
        studio,
        studio.start_run(
            {
                "kind": "external",
                "external_agent_id": profile["id"],
                "task": "Review evidence",
                **payload,
            }
        ),
    )


@pytest.mark.parametrize("client", ["files", "sqlite"], indirect=True)
@pytest.mark.parametrize("version", ["1.0", "0.3"])
@pytest.mark.parametrize("streaming", [False, True])
def test_round_trip_rich_input_output_and_refresh_never_resends(client, peer, version, streaming):
    profile = connection(client, peer, version, streaming)
    attached = artifact(client)
    result = run(
        client, profile, external_input={"question": "review"}, artifact_ids=[attached["id"]]
    )
    assert result["status"] == "completed", result
    assert result["output"] == "Remote report"
    assert result["structured_content"][1] == {
        "type": "json",
        "data": {"cities": ["Rome"], "verified": True},
    }
    file = result["structured_content"][-1]
    assert (
        client.artifacts.get(client.artifacts.revision(file["artifact_id"], file["revision"]))
        == b"Inline file evidence"
    )
    sent = peer["messages"][0]
    assert sent["parts"][1]["data"] == {"question": "review"}
    uploaded = (
        sent["parts"][2].get("raw") if version == "1.0" else sent["parts"][2]["file"]["bytes"]
    )
    assert base64.b64decode(uploaded) == b"Uploaded evidence"
    before = [a["id"] for a in client.catalog.list("artifacts")]
    client.external.refresh_task(result["id"], None)
    assert [a["id"] for a in client.catalog.list("artifacts")] == before
    events = client.execution.snapshot(result["id"], None)["timeline"]
    assert len([event for event in events if event.get("artifact_id") == file["artifact_id"]]) == 1
    assert len(peer["messages"]) == 1
    assert all(row[2] == "Bearer synthetic-peer-token" for row in peer["requests"])


@pytest.mark.parametrize("version", ["1.0", "0.3"])
def test_same_origin_file_url_uses_selected_credentials(client, peer, version):
    peer["mode"] = "url"
    profile = connection(client, peer, version)
    result = run(client, profile)
    assert result["status"] == "completed", result
    file = result["structured_content"][-1]
    assert (
        client.artifacts.get(client.artifacts.revision(file["artifact_id"], 1))
        == b"URL file evidence"
    )
    assert len([r for r in peer["requests"] if r[1] == "/file"]) == 1


@pytest.mark.parametrize("mode", ["malformed", "path", "cross-origin", "redirect"])
def test_invalid_outputs_import_nothing_and_recovery_only_reads(client, peer, mode):
    peer["mode"] = mode
    profile = connection(client, peer)
    result = run(client, profile)
    assert result["status"] == "failed", result
    assert not [a for a in client.catalog.list("artifacts") if a["id"].startswith("remote-")]
    assert not any(r[1] == "/must-not-follow" for r in peer["requests"])
    peer["mode"] = "inline"
    client.external.refresh_task(result["id"], None)
    assert client.run(result["id"])["status"] == "completed"
    assert len(peer["messages"]) == 1


@pytest.mark.parametrize("mode", ["changed-id", "changed-context"])
def test_task_identity_and_context_cannot_change_during_polling(client, peer, mode):
    peer["mode"] = mode
    result = run(client, connection(client, peer))
    assert result["status"] == "failed" and "changed task" in result["error"], result
    assert client.records("external-runs")[0]["task_id"] == "remote-task"
    assert len(peer["messages"]) == 1


@pytest.mark.parametrize("version", ["1.0", "0.3"])
def test_continuation_accepts_rich_input_on_existing_task(client, peer, version):
    peer["mode"] = "pause"
    profile = connection(client, peer, version)
    first = run(client, profile)
    assert first["status"] == "waiting"
    attached = artifact(client)
    continued = done(
        client,
        client.external.continue_task(
            first["id"],
            {
                "task": "More evidence",
                "external_input": {"city": "Rome"},
                "artifact_ids": [attached["id"]],
            },
            None,
        ),
    )
    assert continued["status"] == "completed", continued
    assert peer["messages"][1]["taskId"] == "remote-task"
    assert peer["messages"][1]["contextId"] == "remote-context"
    assert len(peer["messages"][1]["parts"]) == 3
    assert client.run(first["id"])["structured_content"] == continued["structured_content"]


@pytest.mark.parametrize("value", [None, False, 7, ["Rome"]])
def test_version_one_keeps_all_json_input_types(client, peer, value):
    result = run(client, connection(client, peer), external_input=value)
    assert result["status"] == "completed", result
    assert peer["messages"][0]["parts"][1]["data"] == value


def test_invalid_and_legacy_nonobject_input_rejected_before_queue(client, peer):
    profile = connection(client, peer, "0.3")
    for value in [None, [1], float("nan"), {"large": "x" * 64000}]:
        with pytest.raises(ValueError):
            run(client, profile, external_input=value)
    assert not client.records("runs") and not peer["messages"]


def test_provider_modes_are_checked_before_sending_files_or_json(client, peer):
    peer["input_modes"] = ["text/plain"]
    profile = connection(client, peer)
    result = run(client, profile, external_input={"city": "Rome"})
    assert result["status"] == "failed" and "input mode" in result["error"]
    assert not peer["messages"]


def test_pinned_attachment_uses_old_revision_after_replace(client, peer):
    profile = connection(client, peer)
    first = artifact(client)
    payload = client.external.prepare_payload(
        {
            "external_agent_id": profile["id"],
            "task": "Review",
            "artifact_refs": [{"id": first["id"], "revision": 1}],
        }
    )
    artifact(client, b"newer evidence", revision=1)
    card = client.external.discover(profile["id"], None)
    parts = client.external.content.parts(payload, profile, card, None)
    assert base64.b64decode(parts[-1]["raw"]) == b"Uploaded evidence"
    changed = {**profile, "data": {**profile["data"], "token_env": "OTHER_ACCOUNT"}}
    with pytest.raises(ValueError, match="changed while queued"):
        client.external.content.parts(payload, changed, card, None)


def test_large_binary_input_is_not_extracted_into_prompt_context(client, peer):
    profile = connection(client, peer)
    attached = artifact(client, b"\x00" * (300 * 1024))
    result = run(client, profile, artifact_ids=[attached["id"]])
    assert result["status"] == "completed", result
    assert len(base64.b64decode(peer["messages"][0]["parts"][-1]["raw"])) == 300 * 1024


def test_foreign_artifact_and_connection_access_rejected_before_queue(team, peer):
    studio, policy, _, request, _ = team
    policy.environment_resolver = lambda user: ({"PEER_TOKEN": "synthetic-peer-token"}, None)
    profile = connection(studio, peer, actor="alice")
    private = artifact(studio, actor="bob")
    payload = {
        "kind": "external",
        "external_agent_id": profile["id"],
        "task": "Review",
        "artifact_ids": [private["id"]],
    }
    assert request("/api/runs", payload, user="alice")[0] == 403
    payload["artifact_ids"] = []
    assert request("/api/runs", payload, user="bob")[0] == 403
    assert not studio.records("runs") and not peer["messages"]


def test_revoked_access_during_download_cannot_publish_files(team, peer):
    studio, policy, _, request, _ = team
    policy.environment_resolver = lambda user: ({"PEER_TOKEN": "synthetic-peer-token"}, None)
    profile = connection(studio, peer, actor="alice")
    peer.update(mode="url", block_file=True)
    status, queued = request(
        "/api/runs",
        {"kind": "external", "external_agent_id": profile["id"], "task": "Review"},
        user="alice",
    )
    assert status == 202, queued
    assert peer["file_entered"].wait(5)
    policy.bindings["alice"] = "viewer"
    peer["file_release"].set()
    result = done(studio, queued)
    assert result["status"] == "failed", result
    assert not [a for a in studio.catalog.list("artifacts") if a["id"].startswith("remote-")]
    assert len(peer["messages"]) == 1


def test_profile_change_blocks_refresh_before_remote_request(client, peer):
    profile = connection(client, peer)
    result = run(client, profile)
    count = len(peer["requests"])
    client.catalog.save(
        "externalagents",
        {
            "id": profile["id"],
            "revision": 1,
            "name": profile["name"],
            "data": {**profile["data"], "token_env": "OTHER_ACCOUNT"},
        },
    )
    with pytest.raises(ValueError, match="connection changed"):
        client.external.refresh_task(result["id"], None)
    assert len(peer["requests"]) == count


@pytest.mark.parametrize("mode", ["inline", "url"])
def test_oversized_file_results_are_rejected_before_local_publication(
    client, peer, monkeypatch, mode
):
    import nexus.core.studio_external_content as content

    monkeypatch.setattr(content, "FILE_LIMIT", 3)
    monkeypatch.setattr(content, "TOTAL_LIMIT", 3)
    peer["mode"] = mode
    result = run(client, connection(client, peer))
    assert result["status"] == "failed", result
    assert not [a for a in client.catalog.list("artifacts") if a["id"].startswith("remote-")]
    assert len(peer["messages"]) == 1


def test_nonfinite_remote_json_and_mixed_parts_never_publish(client, peer):
    profile = connection(client, peer)
    for part in [
        {"data": float("nan")},
        {"text": "mixed", "data": {}},
        {"raw": "YQ==", "filename": "../escape"},
    ]:
        with pytest.raises(ValueError):
            client.external.content.plan(
                [{"parts": [{"raw": "YQ==", "filename": "valid.bin"}, part]}], profile, None
            )
    assert not client.catalog.list("artifacts")


def test_worker_checks_queued_connection_before_even_discovering(client, peer):
    import asyncio

    profile = connection(client, peer)
    payload = client.external.prepare_payload(
        {"external_agent_id": profile["id"], "task": "Review"}
    )
    client.catalog.save(
        "externalagents",
        {
            "id": profile["id"],
            "revision": 1,
            "name": profile["name"],
            "data": {**profile["data"], "token_env": "OTHER_ACCOUNT"},
        },
    )
    with pytest.raises(ValueError, match="changed while queued"):
        asyncio.run(client.external.execute("not-dispatched", payload))
    assert not peer["requests"]


def test_disabled_connection_stops_download_before_http(client, peer):
    profile = connection(client, peer)
    client.catalog.save(
        "externalagents",
        {
            "id": profile["id"],
            "revision": 1,
            "name": profile["name"],
            "data": {**profile["data"], "enabled": False},
        },
    )
    with pytest.raises(ValueError, match="disabled"):
        client.external.download(peer["origin"] + "/file", profile, None, 1000)
    assert not peer["requests"]

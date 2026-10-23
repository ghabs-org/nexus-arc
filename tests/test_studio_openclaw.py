"""Signed operator handoffs exercise real Studio jobs and checkpoint services."""

import base64
import hashlib
import hmac
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_openclaw import OpenClawConflictError
from nexus.core.studio_team import NexusTeamPolicy
from nexus.studio import make_studio_handler
from tests.studio_openclaw_http_acceptance import read_events, stop_process, wait_for_start
from tests.test_studio_durable import options
from tests.test_studio_team import NexusIdentity
from tests.test_studio_workflow_v2 import wait

SECRET = "synthetic-openclaw-secret-" + "s" * 32


def test_documented_entrypoint_saves_and_discovers(app, monkeypatch):
    guide = (Path(__file__).parents[1] / "docs" / "STUDIO-OPENCLAW.md").read_text()
    spec = yaml.safe_load(re.search(r"```yaml\n(.*?)```", guide, re.S).group(1))
    monkeypatch.setenv(spec["data"]["token_env"], SECRET)
    saved = app.catalog.save("entrypoints", spec)
    headers, raw = signed(
        envelope(entrypoint_id=saved["id"], channel=spec["data"]["allowed_channels"][0])
    )
    result = app.openclaw.receive(saved["id"], headers, raw)
    assert result["items"] == [{"target": "agents:reviewer", "name": "Reviewer"}]


def test_fault_runner_preserves_startup_error_when_child_already_exited(tmp_path):
    log, errors = tmp_path / "events.ndjson", tmp_path / "runner.err"
    with log.open("w") as output, errors.open("w") as stderr:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('dataset engine unavailable\\n'); sys.exit(7)",
            ],
            stdout=output,
            stderr=stderr,
            start_new_session=True,
        )
        try:
            with pytest.raises(RuntimeError, match="status 7: dataset engine unavailable"):
                wait_for_start(process, log, errors, timeout=5)
        finally:
            stop_process(process)
    assert process.returncode == 7


def test_fault_log_reader_waits_for_complete_json_lines(tmp_path):
    log = tmp_path / "events.ndjson"
    log.write_text('{"event":"listening"}\n{"event":"req')
    assert read_events(log) == [{"event": "listening"}]


def envelope(operation="discover", arguments=None, **extra):
    return {
        "version": 1,
        "entrypoint_id": "openclaw",
        "id": "request-1",
        "sender_id": "telegram:alice",
        "channel": "telegram:room",
        "session_id": "thread-1",
        "operation": operation,
        "arguments": arguments or {},
        **extra,
    }


def signed(body, timestamp=None):
    raw = json.dumps(body).encode()
    timestamp = str(int(time.time()) if timestamp is None else timestamp)
    return {
        "X-Nexus-Timestamp": timestamp,
        "X-Nexus-Signature": "sha256="
        + hmac.new(SECRET.encode(), timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest(),
    }, raw


def invoke(app, operation="discover", arguments=None, **extra):
    headers, raw = signed(envelope(operation, arguments, **extra))
    return app.openclaw.receive("openclaw", headers, raw)


def setup(app):
    app.save_definition("agents", {"name": "Reviewer", "instructions": "Review the task"})
    app.save_definition("agents", {"name": "Hidden", "instructions": "Outside the grant"})
    for kind in ("approval", "input"):
        app.save_definition(
            "workflows",
            {
                "name": kind,
                "graph": {
                    "version": 2,
                    "nodes": [
                        {
                            "id": "gate",
                            "kind": kind,
                            **({"output_schema": {"type": "string"}} if kind == "input" else {}),
                        },
                        {
                            "id": "count",
                            "kind": "tool",
                            "tool_id": "text:wordcount",
                            "kwargs": {"text": "one two"},
                            "depends_on": ["gate"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.count"},
                },
            },
        )
    app.catalog.save(
        "entrypoints",
        {
            "name": "OpenClaw",
            "data": {
                "type": "openclaw",
                "enabled": True,
                "target_kind": "agent",
                "target_id": "reviewer",
                "token_env": "OPENCLAW_TEST_SECRET",
                "identity_map": {"telegram:alice": "desktop", "telegram:bob": "desktop"},
                "allowed_channels": ["telegram:room"],
                "operator_targets": ["agents:reviewer", "workflows:approval", "workflows:input"],
            },
        },
    )


class Provider:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        return AgentResult(success=True, output="Reviewed")


@pytest.fixture(params=["file", "sql"])
def app(tmp_path, monkeypatch, request):
    monkeypatch.setenv("OPENCLAW_TEST_SECRET", SECRET)
    kwargs = {"state_options": options(tmp_path, workers=1)} if request.param == "sql" else {}
    service = StudioService(tmp_path / "workspace", provider_factory=lambda _: Provider(), **kwargs)
    setup(service)
    yield service
    service.close()


def launch(app, target="workflows:approval", **extra):
    return invoke(app, "submit", {"target": target, "task": "Review"}, **extra)["run"]


def status(app, identity, **extra):
    return invoke(app, "status", {"run_id": identity}, **extra)["run"]


def test_discovery_and_scope_are_explicit(app):
    assert {item["target"] for item in invoke(app)["items"]} == {
        "agents:reviewer",
        "workflows:approval",
        "workflows:input",
    }
    with pytest.raises(PermissionError):
        launch(app, "agents:hidden")
    with pytest.raises(PermissionError):
        invoke(app, sender_id="telegram:unknown")
    with pytest.raises(PermissionError):
        invoke(app, channel="telegram:elsewhere")
    saved = app.catalog.get("entrypoints", "openclaw")
    app.catalog.save("entrypoints", {**saved, "data": {**saved["data"], "operator_targets": []}})
    with pytest.raises(PermissionError):
        invoke(app)


def test_signature_timestamp_route_and_envelope_rejection(app):
    headers, raw = signed(envelope(), timestamp=1)
    with pytest.raises(PermissionError):
        app.openclaw.receive("openclaw", headers, raw)
    headers, raw = signed(envelope())
    with pytest.raises(PermissionError):
        app.openclaw.receive("openclaw", headers, raw + b" ")
    with pytest.raises(PermissionError):
        invoke(app, entrypoint_id="another")
    with pytest.raises(ValueError):
        invoke(app, requester_nexus_id="owner")
    with pytest.raises(ValueError):
        invoke(app, "discover", {"credentials": "forbidden"})


def test_duplicate_submission_has_one_run_and_receipt(app):
    first = launch(app)
    again = invoke(app, "submit", {"target": "workflows:approval", "task": "Review"})
    assert again["duplicate"] and again["run"]["id"] == first["id"]
    assert len(app.records("runs")) == 1
    assert invoke(app, "receipt", {"request_id": "request-1"})["run"]["id"] == first["id"]
    with pytest.raises(OpenClawConflictError, match="different arguments"):
        invoke(app, "submit", {"target": "workflows:approval", "task": "Changed"})
    with pytest.raises(FileNotFoundError):
        invoke(app, "receipt", {"request_id": "request-1"}, sender_id="telegram:bob")


def test_concurrent_duplicate_submission_is_claimed_once(app):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: launch(app), range(4)))
    assert len({result["id"] for result in results}) == 1
    assert len(app.records("runs")) == 1


def test_failed_enqueue_is_atomic_in_sql_and_uncertain_on_files(app, monkeypatch):
    original = app.operations.launch_target

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("connection lost after dispatch")

    monkeypatch.setattr(app.operations, "launch_target", interrupted)
    with pytest.raises(RuntimeError):
        launch(app)
    if app.sql_state:
        # The queued job and receipt share the outer SQL transaction. Neither
        # becomes visible to a worker when the dispatch transaction rolls back.
        assert not app.records("runs")
        monkeypatch.setattr(app.operations, "launch_target", original)
        launch(app)
    else:
        with pytest.raises(OpenClawConflictError, match="uncertain"):
            launch(app)
    assert len(app.records("runs")) == 1


def test_approval_continues_same_run_and_rejects_other_caller(app):
    run = wait(app, launch(app), "waiting")
    token = status(app, run["id"])["reply_token"]
    with pytest.raises(PermissionError):
        invoke(
            app,
            "reply",
            {"token": token, "decision": "approve"},
            id="reply",
            sender_id="telegram:bob",
        )
    with pytest.raises(PermissionError):
        status(app, run["id"], sender_id="telegram:bob")
    resumed = invoke(app, "reply", {"token": token, "decision": "approve"}, id="reply")
    assert wait(app, resumed["run"])["output"] == "2"
    assert resumed["run"]["id"] == run["id"] and len(app.records("runs")) == 1
    with pytest.raises(PermissionError, match="used"):
        invoke(app, "reply", {"token": token, "decision": "approve"}, id="replay")
    assert invoke(app, "reply", {"token": token, "decision": "approve"}, id="reply")["duplicate"]


def test_expired_and_changed_gate_tokens_fail(app):
    run = wait(app, launch(app), "waiting")
    token = status(app, run["id"])["reply_token"]
    path = app._path("openclaw-gates", token.split(".")[0])
    gate = app._read(path)
    app._write(path, {**gate, "expires": 1})
    with pytest.raises(PermissionError):
        invoke(app, "reply", {"token": token, "decision": "approve"}, id="expired")
    fresh = status(app, run["id"])["reply_token"]
    app._update_run(run["id"], awaiting_approval=[{"id": "changed", "name": "Changed"}])
    with pytest.raises(OpenClawConflictError):
        invoke(app, "reply", {"token": fresh, "decision": "approve"}, id="changed")


def test_input_validation_keeps_gate_then_resumes(app):
    run = wait(app, launch(app, "workflows:input"), "waiting")
    token = status(app, run["id"])["reply_token"]
    pending = run["awaiting_input"][0]["id"]
    with pytest.raises(ValueError):
        invoke(app, "reply", {"token": token, "values": {pending: 42}}, id="bad")
    result = invoke(app, "reply", {"token": token, "values": {pending: "Evidence"}}, id="good")
    assert wait(app, result["run"])["output"] == "2"


def test_revocation_and_cancellation(app):
    run = wait(app, launch(app), "waiting")
    saved = app.catalog.get("entrypoints", "openclaw")
    app.catalog.save(
        "entrypoints",
        {**saved, "data": {**saved["data"], "identity_map": {"telegram:bob": "desktop"}}},
    )
    with pytest.raises(PermissionError):
        invoke(app, "cancel", {"run_id": run["id"]}, id="cancel")
    app.catalog.save(
        "entrypoints", {**saved, "revision": app.catalog.get("entrypoints", "openclaw")["revision"]}
    )
    result = invoke(app, "cancel", {"run_id": run["id"]}, id="cancel")
    assert result["run"]["status"] == "cancelled"


def test_artifact_revision_is_pinned_and_scoped(app):
    run = wait(app, launch(app, "agents:reviewer"))
    artifact = app.catalog.save(
        "artifacts",
        {
            "name": "Report",
            "data": {
                "filename": "report.txt",
                "content_base64": base64.b64encode(b"original").decode(),
            },
        },
    )
    app._update_run(
        run["id"],
        structured_content=[
            {"type": "artifact", "artifact_id": artifact["id"], "revision": artifact["revision"]}
        ],
    )
    app.catalog.save(
        "artifacts",
        {
            **artifact,
            "data": {
                "filename": "report.txt",
                "content_base64": base64.b64encode(b"replacement").decode(),
            },
        },
    )
    items = invoke(app, "artifacts", {"run_id": run["id"]})["items"]
    assert items[0]["revision"] == artifact["revision"]
    args = {"run_id": run["id"], "artifact_id": artifact["id"], "revision": artifact["revision"]}
    result = invoke(app, "artifact", args)
    assert base64.b64decode(result["artifact"]["content_base64"]) == b"original"
    with pytest.raises(PermissionError):
        invoke(app, "artifact", {**args, "revision": artifact["revision"] + 1})
    with pytest.raises(PermissionError):
        invoke(app, "artifact", args, sender_id="telegram:bob")


@pytest.mark.parametrize("sql", [False, True])
def test_restart_preserves_receipts_gates_and_agent_conversation(tmp_path, monkeypatch, sql):
    monkeypatch.setenv("OPENCLAW_TEST_SECRET", SECRET)
    kwargs = {"state_options": options(tmp_path, workers=1)} if sql else {}
    root = tmp_path / "workspace"
    service = StudioService(root, provider_factory=lambda _: Provider(), **kwargs)
    setup(service)
    first = wait(service, launch(service, "agents:reviewer", id="chat"))
    gate_run = wait(service, launch(service, id="gate"), "waiting")
    token = status(service, gate_run["id"])["reply_token"]
    service.close()
    restored = StudioService(root, provider_factory=lambda _: Provider(), **kwargs)
    try:
        assert launch(restored, "agents:reviewer", id="chat")["id"] == first["id"]
        second = wait(restored, launch(restored, "agents:reviewer", id="chat-next"))
        assert first["session_id"] == second["session_id"]
        resumed = invoke(restored, "reply", {"token": token, "decision": "approve"}, id="reply")
        assert wait(restored, resumed["run"])["output"] == "2"
        assert len(restored.records("runs")) == 3
    finally:
        restored.close()


def test_team_http_scope_csrf_separation_and_live_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_TEST_SECRET", SECRET)
    manager = NexusIdentity()
    policy = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login",
        manager=manager,
        bindings={"alice": "builder", "bob": "operator", "viewer": "viewer", "owner": "owner"},
        environment_resolver=lambda _: ({"OPENAI_API_KEY": "synthetic"}, None),
    )
    app = StudioService(tmp_path / "project", state_dir=tmp_path / "state", team_policy=policy)
    setup(app)
    record = app.catalog.get("entrypoints", "openclaw")
    app.catalog.save(
        "entrypoints",
        {
            **record,
            "data": {
                **record["data"],
                "identity_map": {
                    "telegram:alice": "alice",
                    "telegram:bob": "bob",
                    "telegram:viewer": "viewer",
                },
            },
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(app, "csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(body, extra=None):
        headers, raw = signed(body)
        headers.update(
            {"Host": "studio.example.test", "Content-Type": "application/json", **(extra or {})}
        )
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/openclaw/openclaw",
            data=raw,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    try:
        assert request(envelope())[0] == 200
        assert request(envelope(), {"Origin": "https://studio.example.test"})[0] == 403
        assert request(envelope(), {"Host": "foreign.example.test"})[0] == 403
        assert request(envelope(), {"X-Nexus-Signature": "invalid"})[0] == 403
        assert request(envelope(sender_id="telegram:viewer"))[0] == 403
        code, result = request(
            envelope("submit", {"target": "workflows:approval", "task": "Review"})
        )
        assert code == 200, result
        run = wait(app, result["run"], "waiting")
        assert run["requester_nexus_id"] == "alice"
        assert (
            request(envelope("status", {"run_id": run["id"]}, sender_id="telegram:bob"))[0] == 403
        )
        assert (
            request(envelope("submit", {"target": "workflows:approval", "task": "Changed"}))[0]
            == 409
        )
        policy.bindings["alice"] = "viewer"
        assert request(envelope("status", {"run_id": run["id"]}))[0] == 403
        policy.bindings["alice"] = "builder"
        assert request(envelope("cancel", {"run_id": run["id"]}, id="cancel"))[0] == 200
        assert app.run(run["id"])["status"] == "cancelled"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        app.close()

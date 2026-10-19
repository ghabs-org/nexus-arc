"""Review integrity, safe setup, real pooled MCP and incoming/streaming A2A."""

import asyncio
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def done(service, run):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        value = service.run(run["id"])
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(0.01)
    pytest.fail("Run did not stop")


def test_inbox_binds_review_to_current_gate_and_resolves_it(tmp_path):
    app = StudioService(tmp_path)
    try:
        app.save_definition(
            "workflows",
            {
                "name": "Review",
                "graph": {
                    "nodes": [
                        {"id": "gate", "name": "Human review", "kind": "approval"},
                        {
                            "id": "count",
                            "name": "Count",
                            "kind": "tool",
                            "tool_id": "text:wordcount",
                            "kwargs": {"text": "one two"},
                        },
                    ],
                    "edges": [
                        {"source": "START", "target": "gate"},
                        {"source": "gate", "target": "count"},
                    ],
                },
            },
        )
        run = done(
            app,
            app.start_run(
                {"kind": "workflow", "workflow_id": "review", "task": "Count after review"}
            ),
        )
        assert run["status"] == "waiting", run
        item = app.workbench.inbox.items(None)[0]
        assert item["actions"] == ["approve", "reject"]
        app._update_run(run["id"], task="Changed task to review")
        with pytest.raises(ValueError, match="changed"):
            app.workbench.inbox.review(
                item["id"], {"digest": item["digest"], "decision": "approve", "confirm": True}, None
            )
        item = app.workbench.inbox.items(None)[0]
        with pytest.raises(ValueError, match="confirm"):
            app.workbench.inbox.review(
                item["id"], {"digest": item["digest"], "decision": "approve"}, None
            )
        resumed = app.workbench.inbox.review(
            item["id"], {"digest": item["digest"], "decision": "approve", "confirm": True}, None
        )
        assert done(app, resumed)["status"] == "completed"
        assert app.workbench.inbox.items(None) == []
        assert app.records("review-decisions")[0]["decision"] == "approve"
        with pytest.raises(FileNotFoundError):
            app.workbench.inbox.review(
                item["id"], {"digest": item["digest"], "decision": "approve", "confirm": True}, None
            )
    finally:
        app.close()


def test_guided_connection_check_is_read_only_and_revision_bound(tmp_path):
    import sqlite3

    app = StudioService(tmp_path)
    with sqlite3.connect(tmp_path / "data.sqlite") as connection:
        connection.execute("CREATE TABLE evidence (value TEXT)")
    try:
        record = app.catalog.save(
            "connectors",
            {
                "name": "Evidence",
                "data": {"type": "sqlite", "path": "data.sqlite", "enabled": False},
            },
        )
        with pytest.raises(ValueError, match="disabled"):
            app.operations.call_connector(record["id"], {})
        check = app.workbench.onboarding.probe(
            record["id"], {"revision": 1, "action": "delete", "query": "DROP TABLE evidence"}, None
        )
        assert check["authorization_verified"]
        assert not app.catalog.get("connectors", record["id"])["data"]["enabled"]
        app.catalog.save(
            "connectors", {**record, "data": {**record["data"], "path": "other.sqlite"}}
        )
        with pytest.raises(ValueError, match="again"):
            app.workbench.onboarding.enable(
                record["id"], {"digest": check["digest"], "confirm": True}, None
            )
        latest = app.catalog.get("connectors", record["id"])
        latest = app.catalog.save("connectors", {**latest, "data": record["data"]})
        check = app.workbench.onboarding.probe(record["id"], {"revision": latest["revision"]}, None)
        enabled = app.workbench.onboarding.enable(
            record["id"], {"digest": check["digest"], "confirm": True}, None
        )
        assert enabled["data"]["enabled"] is True
        assert "evidence" in app.operations.call_connector(record["id"], {})
    finally:
        app.close()


def test_mcp_pool_reuses_sessions_across_request_loops_and_isolates_accounts(tmp_path):
    from nexus.tools.mcp_sessions import MCPSessionPool

    script = tmp_path / "server.py"
    script.write_text("""import os
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer
server = MCPServer("Pooled test")
count = 0
@server.tool()
def evidence() -> str:
    global count
    count += 1
    return str(os.getpid()) + ":" + str(count)
@server.tool()
def broken() -> str:
    raise ValueError("untrusted server detail")
server.run(transport="stdio")
""")
    pool = MCPSessionPool(idle_seconds=0.5)

    def call(actor, tool="evidence"):
        return asyncio.run(
            pool.invoke(actor, sys.executable, [str(script)], {"PATH": os.defpath}, tool=tool)
        )

    try:
        first, second = call("alice"), call("alice")
        assert first.split(":")[0] == second.split(":")[0]
        assert first.endswith(":1") and second.endswith(":2")
        other = call("bob")
        assert other.split(":")[0] != first.split(":")[0] and other.endswith(":1")
        with pytest.raises(RuntimeError, match="Review the action"):
            call("alice", "broken")
        fresh = call("alice")
        assert fresh.split(":")[0] != first.split(":")[0] and fresh.endswith(":1")
        time.sleep(0.8)
        assert not pool.entries
    finally:
        pool.close()


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            await asyncio.sleep(0.1)
            return AgentResult(success=True, output="Remote evidence")

    app = StudioService(tmp_path / "server", provider_factory=lambda _: Provider())
    app.save_definition("agents", {"name": "Researcher", "instructions": "Read"})
    monkeypatch.setenv("TEST_A2A_TOKEN", "test-private-gateway-token-123456789")
    app.catalog.save(
        "entrypoints",
        {
            "name": "Research",
            "data": {
                "target_kind": "agent",
                "target_id": "researcher",
                "token_env": "TEST_A2A_TOKEN",
                "enabled": True,
                "a2a_enabled": True,
            },
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(app, "browser-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None, token=True, version="1.0"):
        headers = {"Content-Type": "application/json", "A2A-Version": version}
        if token:
            headers["Authorization"] = "Bearer " + os.environ["TEST_A2A_TOKEN"]
        req = urllib.request.Request(
            origin + path,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                raw = response.read()
                return response.status, json.loads(raw) if response.headers.get_content_type() in {
                    "application/json",
                    "application/manifest+json",
                } else raw
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    yield app, origin, request, calls
    server.shutdown()
    server.server_close()
    thread.join()
    app.close()


@pytest.mark.parametrize("version", ["1.0", "0.3"])
def test_real_incoming_a2a_streams_and_deduplicates_one_message(gateway, version):
    app, origin, request, calls = gateway
    assert request("/api/a2a/research/agent-card.json", token=False)[0] == 403
    assert (
        request("/api/a2a/research/agent-card.json", version=version)[1]["capabilities"][
            "streaming"
        ]
        is True
    )
    message = {
        "messageId": "one-message",
        "role": "ROLE_USER" if version == "1.0" else "user",
        "parts": [{"text": "Check evidence", **({"kind": "text"} if version == "0.3" else {})}],
    }
    payload = {
        "jsonrpc": "2.0",
        "id": "request-1",
        "method": "SendStreamingMessage" if version == "1.0" else "message/stream",
        "params": {"message": message},
    }
    status, stream = request("/api/a2a/research", payload, version=version)
    assert status == 200 and b"Remote evidence" in stream
    assert b"TASK_STATE_COMPLETED" in stream or b'"state": "completed"' in stream
    events = [
        json.loads(line[6:])["result"]
        for line in stream.decode().splitlines()
        if line.startswith("data: ")
    ]
    task = events[0]["task"] if version == "1.0" else events[0]
    payload["method"] = "SendMessage" if version == "1.0" else "message/send"
    payload["params"]["configuration"] = {"returnImmediately": True}
    returned = request("/api/a2a/research", payload, version=version)[1]["result"]
    assert (returned["task"] if version == "1.0" else returned)["id"] == task["id"]
    assert len(calls) == 1
    payload["params"]["message"]["parts"][0]["text"] = "Different text"
    assert request("/api/a2a/research", payload, version=version)[1]["error"]["code"] == -32602
    assert (
        request(
            "/api/a2a/research",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "GetTask" if version == "1.0" else "tasks/get",
                "params": {"id": "unrelated"},
            },
            version=version,
        )[1]["error"]["code"]
        == -32001
    )
    assert len(calls) == 1


@pytest.mark.parametrize("version", ["1.0", "0.3"])
def test_studio_to_studio_streaming_round_trip(gateway, tmp_path, version):
    _, origin, _, calls = gateway
    client = StudioService(tmp_path / "client")
    try:
        client.catalog.save(
            "externalagents",
            {
                "name": "Remote",
                "data": {
                    "url": origin + "/api/a2a/research",
                    "card_path": "/api/a2a/research/agent-card.json",
                    "token_env": "TEST_A2A_TOKEN",
                    "protocol_version": version,
                    "streaming": True,
                    "enabled": True,
                    "allow_private": True,
                },
            },
        )
        result = done(
            client,
            client.start_run(
                {"kind": "external", "external_agent_id": "remote", "task": "Verify the bridge"}
            ),
        )
        assert result["status"] == "completed" and result["output"] == "Remote evidence", result
        assert len(calls) == 1
    finally:
        client.close()


def test_installation_assets_and_readiness_are_served(gateway):
    _, _, request, _ = gateway
    manifest = request("/manifest.webmanifest")[1]
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert request(icon["src"])[1].startswith(b"\x89PNG")
    assert request("/service-worker.js")[0] == 200
    report = request("/api/workbench/readiness")[1]
    assert any(item["status"] == "not_verified" for item in report["items"])


def test_invalid_a2a_send_cannot_start_a_job(gateway):
    _, _, request, calls = gateway
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": "invalid-mode",
                "role": "ROLE_USER",
                "parts": [{"text": "Do not run"}],
            },
            "historyLength": -1,
        },
    }
    assert request("/api/a2a/research", payload)[1]["error"]["code"] == -32602
    assert not calls
    card = request("/api/a2a/research/agent-card.json")[1]
    assert card["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"
    assert "securityRequirements" in card and "security" not in card


@pytest.mark.parametrize("acknowledged", [True, False])
def test_broken_remote_stream_reads_known_task_and_never_resends(tmp_path, acknowledged):
    methods = []

    def request(url, body, headers):
        if body is None:
            return {
                "name": "Remote",
                "capabilities": {"streaming": True},
                "supportedInterfaces": [
                    {
                        "url": "https://agent.example.test/rpc",
                        "protocolBinding": "JSONRPC",
                        "protocolVersion": "1.0",
                    }
                ],
            }
        methods.append(body["method"])
        return {
            "jsonrpc": "2.0",
            "id": body["id"],
            "result": {
                "id": "known-task",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "artifacts": [{"parts": [{"text": "Recovered result"}]}],
            },
        }

    def stream(url, body, headers):
        methods.append(body["method"])
        if acknowledged:
            yield {
                "task": {
                    "id": "known-task",
                    "contextId": "context",
                    "status": {"state": "TASK_STATE_WORKING"},
                }
            }
        raise ConnectionError("Connection dropped")

    app = StudioService(
        tmp_path, external_options={"request_client": request, "stream_client": stream}
    )
    try:
        app.catalog.save(
            "externalagents",
            {
                "name": "Remote",
                "data": {
                    "url": "https://agent.example.test",
                    "enabled": True,
                    "streaming": True,
                    "allow_private": True,
                },
            },
        )
        result = done(
            app,
            app.start_run(
                {"kind": "external", "external_agent_id": "remote", "task": "One message"}
            ),
        )
        assert methods == (
            ["SendStreamingMessage", "GetTask"] if acknowledged else ["SendStreamingMessage"]
        )
        if acknowledged:
            assert result["status"] == "completed" and result["output"] == "Recovered result"
        else:
            assert result["status"] == "failed"
            assert app.records("external-runs")[0]["status"] == "uncertain"
    finally:
        app.close()


def test_https_deployment_verifier_checks_certificate_login_and_private_routes(tmp_path):
    import importlib.util
    import ssl
    import subprocess
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    from nexus.core.studio_team import NexusTeamPolicy

    config = tmp_path / "tls.conf"
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\n"
    )
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-config",
            str(config),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )

    class Identity:
        def get_session_and_setup_status(self, session):
            return {
                "exists": session == "a" * 32,
                "status": "completed",
                "nexus_id": "alice",
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }

        def has_project_access(self, identity, project):
            return identity == "alice" and project == "test"

    server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args: None)
    origin = f"https://localhost:{server.server_port}"
    policy = NexusTeamPolicy(
        project_key="test",
        public_url=origin,
        login_url=origin + "/login",
        bindings={"alice": "owner"},
        manager=Identity(),
        environment_resolver=lambda _: ({}, None),
    )
    app = StudioService(tmp_path / "project", team_policy=policy, state_dir=tmp_path / "private")
    base_handler = make_studio_handler(app, "csrf")

    class LoginHandler(base_handler):
        def do_GET(self):  # noqa: N802 — HTTP handler method name
            if self.path == "/login":
                return self.send_bytes(200, b"<html>Nexus test login</html>", "text/html")
            return super().do_GET()

    server.RequestHandlerClass = LoginHandler
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    spec = importlib.util.spec_from_file_location(
        "studio_verify", Path(__file__).parents[1] / "deploy/studio/verify.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        report = module.verify(origin, session="a" * 32, ca_file=str(cert))
        assert report["passed"], report
        assert not module.verify(origin, session="b" * 32, ca_file=str(cert))["passed"]
        with pytest.raises(urllib.error.URLError):
            module.verify(origin, session="a" * 32)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()


def test_separate_worker_processes_consume_one_durable_job_once(tmp_path):
    import subprocess

    options = {
        "url": os.getenv("NEXUS_STUDIO_TEST_DATABASE_URL")
        or "sqlite:///" + str(tmp_path / "state.sqlite"),
        "namespace": "process-" + tmp_path.name[-24:],
        "workers": 0,
    }
    project = tmp_path / "project"
    app = StudioService(project, state_options=options)
    app.save_definition("agents", {"name": "Reviewer", "instructions": "Read"})
    run = app.start_run({"agent_id": "reviewer", "task": "Execute once"})
    app.close()
    script = tmp_path / "worker.py"
    script.write_text("""import asyncio,json,os,sys,time
from pathlib import Path
from nexus.core.studio import StudioService
from nexus.core.models import AgentResult
root,options,identity,receipts=sys.argv[1:]
options=json.loads(options)
options["workers"]=1
class Provider:
    async def check_availability(self): return True
    async def execute_agent(self, context):
        Path(receipts,str(os.getpid())).write_text("executed")
        await asyncio.sleep(.2)
        return AgentResult(success=True,output="Process result")
app=StudioService(root,state_options=options,provider_factory=lambda _:Provider())
try:
    deadline=time.monotonic()+12
    while time.monotonic()<deadline:
        run=app.run(identity)
        if run["status"]=="completed": break
        time.sleep(.05)
    else: raise RuntimeError("Worker deadline exceeded")
finally: app.close()
""")
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    env = {**os.environ, "PYTHONPATH": str(__import__("pathlib").Path(__file__).parents[1])}
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                str(script),
                str(project),
                json.dumps(options),
                run["id"],
                str(receipts),
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            _, error = process.communicate(timeout=20)
            assert process.returncode == 0, error.decode()
        assert len(list(receipts.iterdir())) == 1
        restored = StudioService(project, state_options=options)
        try:
            assert restored.run(run["id"])["output"] == "Process result"
        finally:
            restored.close()
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

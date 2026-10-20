"""Streaming snapshots, typed content and private pinned preview HTTP contracts."""

import asyncio
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_conversations import TEXT_LIMIT
from nexus.core.studio_team import NexusTeamPolicy
from nexus.studio import make_studio_handler


def finished(studio, run):
    for _ in range(500):
        record = studio.run(run["id"])
        if record["status"] not in {"queued", "running"}:
            return record
        time.sleep(0.01)
    pytest.fail("Task did not finish")


def test_stream_snapshot_is_recoverable_and_final_answer_is_authoritative(tmp_path):
    emitted, release = threading.Event(), threading.Event()
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent_streaming(self, context, delta):
            calls.append(context)
            delta("**Partial** [source:1]\n\n| A | B |\n|---|---|\n")
            emitted.set()
            await asyncio.to_thread(release.wait, 5)
            delta("| one | two |")
            return AgentResult(
                success=True,
                output="**Final**\n\n```python\nprint(1)\n```",
                metadata={"structured_content": [{"type": "json", "data": {"ok": True}}]},
            )

    app = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        run = app.start_run({"task": "Stream once"})
        assert emitted.wait(5)
        first = app.run(run["id"])
        assert first["stream"]["sequence"] == 1 and first["status"] == "running"
        assert "Partial" in first["output"]
        # Any polling client can recover this same persisted snapshot without submission.
        assert app.run(run["id"]) == first
        release.set()
        result = finished(app, run)
        assert result["status"] == "completed"
        assert result["output"].startswith("**Final**") and "Partial" not in result["output"]
        assert result["stream"]["status"] == "completed" and result["stream"]["sequence"] == 3
        assert (
            app.session(run["session_id"])["messages"][-1]["structured_content"]
            == result["structured_content"]
        )
        app.conversations.delta(run["id"], "Late duplicate", first["stream"]["invocation_id"])
        assert app.run(run["id"])["output"] == result["output"]
        assert len(calls) == 1
    finally:
        release.set()
        app.close()


def test_partial_stream_failure_preserves_output_without_committing_conversation(tmp_path):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent_streaming(self, context, delta):
            delta("Partial evidence")
            raise RuntimeError("Provider connection lost")

    app = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        result = finished(app, app.start_run({"task": "No automatic replay"}))
        assert result["status"] == "failed" and result["output"] == "Partial evidence"
        assert result["stream"]["status"] == "interrupted-or-failed"
        # The attempt is kept as a failed turn holding the error, never the partial text.
        [session] = app.records("sessions")
        reply = app.session(session["id"])["messages"][-1]
        assert reply["status"] == "failed" and "Provider connection lost" in reply["content"]
        assert "Partial evidence" not in reply["content"]
    finally:
        app.close()


def test_live_preview_is_bounded_and_late_invocation_cannot_clobber_another(tmp_path):
    app = StudioService(tmp_path)
    try:
        app._write(
            app._path("runs", "test"),
            {
                "id": "test",
                "kind": "chat",
                "status": "running",
                "output": "",
                "provider": "codex",
                "created_at": "2026-10-18T00:00:00+00:00",
            },
        )
        app.conversations.begin("test", "first")
        app.conversations.delta("test", "x" * (TEXT_LIMIT + 10), "first")
        assert len(app.run("test")["output"]) == TEXT_LIMIT
        assert app.run("test")["stream"]["truncated"]
        app.conversations.begin("test", "second")
        app.conversations.delta("test", "Old turn", "first")
        assert app.run("test")["output"] == ""
    finally:
        app.close()


def test_typed_content_drops_foreign_artifacts_and_unserializable_data(tmp_path):
    app = StudioService(tmp_path)
    try:
        artifact = app.catalog.save(
            "artifacts",
            {
                "name": "Evidence",
                "requester_nexus_id": "alice",
                "data": {
                    "filename": "evidence.json",
                    "content_base64": base64.b64encode(b'{"evidence":true}').decode(),
                },
            },
        )
        app.team = SimpleNamespace(
            bindings={"alice": "operator", "bob": "operator"}, visible=NexusTeamPolicy.visible
        )
        result = AgentResult(
            success=True,
            output="Plain compatible answer",
            metadata={
                "structured_content": [
                    {"type": "artifact", "artifact_id": artifact["id"], "revision": 1},
                    {"type": "json", "data": float("nan")},
                    {"type": "html", "text": "<script>bad()</script>"},
                    {"type": "json", "data": {"count": 1}},
                ]
            },
        )
        assert len(app.conversations.content(result, {"requester_nexus_id": "alice"})) == 2
        assert app.conversations.content(result, {"requester_nexus_id": "bob"}) == [
            {"type": "json", "data": {"count": 1}}
        ]
        assert result.output == "Plain compatible answer"
    finally:
        app.team = None
        app.close()


@pytest.fixture
def server(tmp_path):
    app = StudioService(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(app, "csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path):
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}" + path, timeout=5
            ) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as response:
            return response.code, response.headers, response.read()

    request.host = f"127.0.0.1:{server.server_port}"
    yield app, request
    server.shutdown()
    server.server_close()
    thread.join()
    app.close()


def test_pinned_preview_endpoint_and_viewer_keep_archived_revisions(server):
    app, request = server
    first = app.catalog.save(
        "artifacts",
        {
            "name": "Evidence",
            "data": {
                "filename": "evidence.md",
                "content_base64": base64.b64encode(b"Old revision").decode(),
            },
        },
    )
    second = app.catalog.save(
        "artifacts",
        {
            "id": first["id"],
            "revision": 1,
            "name": "Evidence",
            "data": {
                "filename": "evidence.md",
                "content_base64": base64.b64encode(b"New revision").decode(),
            },
        },
    )
    app.catalog.archive(
        "artifacts", first["id"], {"revision": second["revision"], "archived": True}
    )
    route = f"/api/catalog/artifacts/{first['id']}/preview"
    code, _, body = request(route + "?revision=1&page=1")
    assert code == 200 and json.loads(body)["text"] == "Old revision"
    assert request(route + "?revision=0")[0] == 400
    assert request(route + "?revision=1&page=0")[0] == 400
    assert request(f"/api/catalog/artifacts/{first['id']}/versions/1")[2] == b"Old revision"
    code, headers, body = request("/artifact.html?id=evidence&revision=1&page=1")
    assert code == 200 and b"artifact-viewer.js" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.test",
        "https://*.test",
        "https://host.test/path",
        "https://user:secret@host.test",
        "https://host.test; frame-ancestors *",
        "https://host.test?token=bad",
        "https://host.test:99999",
    ],
)
def test_embed_origins_reject_implicit_or_unsafe_access(origin):
    with pytest.raises(ValueError):
        NexusTeamPolicy._embed_origins([origin])


def test_embed_origins_are_explicit_https_and_deduplicated():
    assert NexusTeamPolicy._embed_origins(None) == []
    assert NexusTeamPolicy._embed_origins(["https://PARENT.test/", "https://parent.test"]) == [
        "https://parent.test"
    ]


def test_only_agent_surface_allows_reviewed_embedding_and_api_stays_authenticated(server):
    app, request = server
    _, headers, _ = request("/agent.html?agent=evidence-guide")
    assert "frame-ancestors 'self';" in headers["Content-Security-Policy"]
    app.team = SimpleNamespace(
        host=request.host,
        embed_origins=["https://portal.example.test"],
        principal=lambda headers: None,
        login_url="https://studio.example.test/login",
    )
    # The local test server keeps the same trusted host; no CORS or token forwarding.
    try:
        _, headers, _ = request("/agent.html?agent=evidence-guide")
        assert (
            "frame-ancestors 'self' https://portal.example.test;"
            in headers["Content-Security-Policy"]
        )
        _, headers, _ = request("/")
        assert "frame-ancestors 'none';" in headers["Content-Security-Policy"]
        assert request("/api/bootstrap")[0] == 401
    finally:
        app.team = None

"""Local catalog and operations exercise persistence, providers and authorization."""

import base64
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


@pytest.fixture
def app(tmp_path):
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            context.attachment_contents = [
                Path(item["path"]).read_bytes()
                for item in (context.metadata or {}).get("attachment_files", [])
            ]
            calls.append(context)
            if "cause provider failure" in context.prompt:
                return AgentResult(success=False, output="", error="Test quota error")
            return AgentResult(success=True, output="Evidence checked. Clear result.")

    service = StudioService(tmp_path, provider_factory=lambda name: Provider())
    service.save_definition(
        "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review evidence"}
    )
    yield service, calls
    service.close()


def done(service, run):
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        record = service.run(run["id"])
        if record["status"] not in {"queued", "running"}:
            return record
        time.sleep(0.01)
    pytest.fail("Run did not finish")


def save(service, kind, data, name="Sample", **extra):
    return service.catalog.save(kind, {"name": name, "data": data, **extra})


def test_catalog_revision_conflicts_history_archive_restore_and_restart(app):
    service, _ = app
    first = save(service, "prompts", {"content": "Use evidence"})
    second = save(service, "prompts", {"content": "Use supplied evidence"}, revision=1)
    assert second["revision"] == 2
    with pytest.raises(ValueError, match="latest revision"):
        save(service, "prompts", {"content": "Stale"}, revision=1)
    assert service.catalog.history("prompts", first["id"])[0]["data"]["content"] == "Use evidence"
    archived = service.catalog.archive("prompts", first["id"], {"revision": 2})
    assert not service.catalog.list("prompts")
    with pytest.raises(ValueError, match="archived"):
        service.catalog.get("prompts", first["id"])
    restored = service.catalog.archive(
        "prompts", first["id"], {"revision": archived["revision"], "archived": False}
    )
    other = StudioService(service.root)
    try:
        assert other.catalog.get("prompts", first["id"]) == restored
    finally:
        other.close()


def test_catalog_paths_and_symlinks_cannot_escape_workspace(app, tmp_path):
    service, _ = app
    with pytest.raises(ValueError):
        save(service, "prompts", {"content": "Text"}, id="../../outside")
    with pytest.raises(ValueError):
        save(service, "unknown", {"content": "Text"})
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    directory = service.state / "catalog" / "skills"
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside"):
        save(service, "skills", {"content": "Text"})
    assert list(outside.iterdir()) == []


def test_prompt_agent_skill_attachment_context_reaches_provider_and_output_is_artifact(app):
    service, calls = app
    save(service, "prompts", {"content": "Analyze only supplied evidence"}, name="Evidence")
    skill = save(service, "skills", {"content": "Cite uncertainty"}, name="Accuracy")
    service.save_definition(
        "agents",
        {"name": "Specialist", "instructions": "Special instructions", "skill_ids": [skill["id"]]},
    )
    artifact = save(
        service,
        "artifacts",
        {
            "filename": "notes.txt",
            "content_base64": base64.b64encode(b"Supplied source material").decode(),
        },
    )
    run = done(
        service,
        service.start_run({"task": "/evidence @specialist", "artifact_ids": [artifact["id"]]}),
    )
    assert run["status"] == "completed", run
    for text in [
        "Analyze only supplied evidence",
        "Special instructions",
        "Cite uncertainty",
    ]:
        assert text in calls[0].prompt
    assert "Supplied source material" not in calls[0].prompt
    assert calls[0].attachment_contents == [b"Supplied source material"]
    assert "file:" in calls[0].prompt
    assert "/evidence" not in calls[0].prompt
    artifact_id = "run-" + run["id"]
    limit = time.monotonic() + 2
    while time.monotonic() < limit and not service._path("catalog/artifacts", artifact_id).exists():
        time.sleep(0.01)
    assert service.catalog.artifact(artifact_id)[1] == b"Evidence checked. Clear result."
    assert "content_base64" not in service.catalog.list("artifacts")[0]["data"]


def test_binary_is_a_file_while_oversize_and_archived_context_are_rejected(app):
    service, calls = app
    item = save(service, "artifacts", {"filename": "binary.bin", "content_base64": "AP8="})
    assert (
        done(service, service.start_run({"task": "Read", "artifact_ids": [item["id"]]}))["status"]
        == "completed"
    )
    assert calls[0].attachment_contents == [b"\x00\xff"]
    with pytest.raises(ValueError, match="16 MiB"):
        save(
            service,
            "artifacts",
            {
                "filename": "big",
                "content_base64": base64.b64encode(b"x" * (16 * 1024 * 1024 + 1)).decode(),
            },
        )
    with pytest.raises(ValueError, match="Filename|filename"):
        save(service, "artifacts", {"filename": "../bad", "content_base64": "eA=="})
    with pytest.raises(ValueError, match="list"):
        service.start_run({"task": "Read", "artifact_ids": "sample"})


def test_toolsets_reference_real_tools_only(app):
    service, _ = app
    assert save(service, "toolsets", {"tools": ["text:wordcount"]})["data"]["tools"] == [
        "text:wordcount"
    ]
    with pytest.raises(ValueError, match="registered"):
        save(service, "toolsets", {"tools": ["imaginary:run"]})


def test_evaluation_reports_objective_checks_and_experiment_variants(app):
    service, calls = app
    suite = save(
        service,
        "evaluations",
        {
            "target_kind": "agent",
            "target_id": "reviewer",
            "cases": [
                {"input": "Check sources", "contains": ["Evidence"], "excludes": ["unsupported"]},
                {"input": "Check clarity", "contains": ["missing phrase"]},
            ],
        },
    )
    run = done(
        service,
        service.start_run(
            {
                "kind": "evaluation",
                "evaluation_id": suite["id"],
                "variants": [
                    {"name": "A", "provider": "codex"},
                    {"name": "B", "provider": "claude-code", "model": "opus"},
                ],
            }
        ),
    )
    assert run["status"] == "completed", run
    report = service._read(service._path("evaluation-reports", run["id"]))
    assert report["passed"] == 2 and report["total"] == 4
    assert report["suite_revision"] == suite["revision"]
    assert calls[-1].model_override == "opus"
    failed = save(
        service,
        "evaluations",
        {
            "target_kind": "agent",
            "target_id": "reviewer",
            "cases": [{"input": "cause provider failure", "contains": ["success"]}],
        },
        name="Failure",
    )
    run = done(service, service.start_run({"kind": "evaluation", "evaluation_id": failed["id"]}))
    assert run["status"] == "failed" and "quota" in run["error"]
    report = service._read(service._path("evaluation-reports", run["id"]))
    assert report["passed"] == 0 and report["results"][0]["error"]


def test_one_time_schedule_runs_once_persists_claim_and_can_be_disabled(app):
    service, calls = app
    now = datetime.now(UTC)
    job = save(
        service,
        "schedules",
        {
            "target_kind": "agent",
            "target_id": "reviewer",
            "task": "Scheduled check",
            "run_at": (now - timedelta(seconds=1)).isoformat(),
            "enabled": True,
        },
    )
    service.operations.tick(now)
    state = service._read(service._path("schedule-state", job["id"]))
    assert done(service, {"id": state["run_id"]})["status"] == "completed"
    service.operations.tick(now + timedelta(hours=1))
    assert len(calls) == 1
    other = StudioService(service.root)
    try:
        other.operations.tick(now + timedelta(days=1))
        assert len(other.records("runs")) == 1
    finally:
        other.close()
    with pytest.raises(ValueError, match="timezone"):
        save(
            service,
            "schedules",
            {
                "target_kind": "agent",
                "target_id": "reviewer",
                "task": "Run",
                "run_at": "2026-10-17T12:00:00",
            },
        )


def test_entrypoint_requires_enabled_and_configured_strong_bearer(app, monkeypatch):
    service, calls = app
    entry = save(
        service,
        "entrypoints",
        {
            "target_kind": "agent",
            "target_id": "reviewer",
            "token_env": "NEXUS_TEST_INBOUND",
            "enabled": True,
        },
    )
    assert not service.operations.authorize_entrypoint(entry["id"], "")
    monkeypatch.setenv("NEXUS_TEST_INBOUND", "a-long-random-test-secret-at-least-24-characters")
    assert not service.operations.authorize_entrypoint(entry["id"], "wrong")
    assert service.operations.authorize_entrypoint(
        entry["id"], "a-long-random-test-secret-at-least-24-characters"
    )
    assert (
        done(service, service.operations.trigger_entrypoint(entry["id"], {"task": "Inbound task"}))[
            "status"
        ]
        == "completed"
    )
    assert len(calls) == 1


def test_sqlite_connector_reads_but_cannot_write_or_escape(app):
    service, _ = app
    database = service.root / "sample.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE notes (body TEXT)")
        db.execute("INSERT INTO notes VALUES ('Evidence')")
    item = save(service, "connectors", {"type": "sqlite", "path": "sample.db", "enabled": True})
    assert "Evidence" in service.operations.call_connector(
        item["id"], {"query": "SELECT body FROM notes"}
    )
    for query in [
        "DELETE FROM notes",
        "ATTACH DATABASE '/tmp/escape.db' AS outside",
        "CREATE TABLE bad (a TEXT)",
    ]:
        with pytest.raises(sqlite3.DatabaseError):
            service.operations.call_connector(item["id"], {"query": query})
    with pytest.raises(ValueError, match="inside"):
        save(service, "connectors", {"type": "sqlite", "path": "../outside.db"})
    from nexus.tools import tool_registry

    name = next(name for name in service.operations.tools)
    assert tool_registry.get(name)
    service.catalog.archive("connectors", item["id"], {"revision": 1})
    assert tool_registry.get(name) is None


def test_graph_parallel_branches_conditional_skips_and_real_node_context(app):
    service, calls = app
    graph = {
        "nodes": [
            {"id": "first", "name": "First", "agent_id": "reviewer", "prompt": "First {task}"},
            {"id": "second", "name": "Second", "agent_id": "reviewer", "prompt": "Second {task}"},
            {"id": "merge", "name": "Merge", "agent_id": "reviewer", "prompt": "Merge {task}"},
            {"id": "skip", "name": "Skip", "agent_id": "reviewer", "prompt": "Skip {task}"},
        ],
        "edges": [
            {"source": "START", "target": "first"},
            {"source": "START", "target": "second"},
            {"source": "first", "target": "merge"},
            {"source": "second", "target": "merge"},
            {"source": "first", "target": "skip", "contains": "absent phrase"},
        ],
    }
    saved = service.save_definition("workflows", {"name": "Parallel review", "graph": graph})
    run = done(
        service,
        service.start_run({"kind": "workflow", "workflow_id": saved["id"], "task": "sources"}),
    )
    assert run["status"] == "completed", run
    assert run["graph_nodes"] == {
        "first": "completed",
        "second": "completed",
        "skip": "skipped",
        "merge": "completed",
    }
    assert len(calls) == 3 and "Completed upstream nodes" in calls[-1].prompt
    assert service.validate_workflow(saved["id"])["valid"]


def test_graph_approval_persists_and_resume_does_not_repeat_completed_nodes(app):
    service, calls = app
    graph = {
        "nodes": [
            {"id": "before", "name": "Before", "agent_id": "reviewer", "prompt": "Review {task}"},
            {"id": "gate", "name": "Approve result", "kind": "approval"},
            {"id": "after", "name": "After", "agent_id": "reviewer", "prompt": "Continue {task}"},
        ],
        "edges": [
            {"source": "START", "target": "before"},
            {"source": "before", "target": "gate"},
            {"source": "gate", "target": "after"},
        ],
    }
    saved = service.save_definition("workflows", {"name": "Gated", "graph": graph})
    run = done(
        service,
        service.start_run({"kind": "workflow", "workflow_id": saved["id"], "task": "sources"}),
    )
    assert run["status"] == "waiting" and len(calls) == 1
    assert run["awaiting_approval"][0]["id"] == "gate"
    resumed = done(service, service.graph.resume(run["id"], "approve"))
    assert resumed["status"] == "completed", resumed
    assert len(calls) == 2
    assert "Evidence checked" in calls[-1].prompt
    with pytest.raises(ValueError, match="not awaiting"):
        service.graph.resume(run["id"], "approve")


def test_graph_rejects_cycles_unreachable_nodes_and_unknown_prompt_fields(app):
    service, _ = app
    node = {"id": "one", "name": "One", "agent_id": "reviewer", "prompt": "Do {task}"}
    for graph in [
        {"nodes": [node], "edges": []},
        {
            "nodes": [node, {**node, "id": "two"}],
            "edges": [{"source": "two", "target": "one"}, {"source": "one", "target": "two"}],
        },
        {
            "nodes": [{**node, "prompt": "Read {task.__class__}"}],
            "edges": [{"source": "START", "target": "one"}],
        },
    ]:
        with pytest.raises(ValueError):
            service.save_definition("workflows", {"name": "Invalid", "graph": graph})


def test_graph_tool_node_uses_real_registry_without_model_inference(app):
    service, calls = app
    graph = {
        "nodes": [
            {
                "id": "count",
                "name": "Count words",
                "kind": "tool",
                "tool_id": "text:wordcount",
                "kwargs": {"text": "{task}"},
            }
        ],
        "edges": [{"source": "START", "target": "count"}],
    }
    saved = service.save_definition("workflows", {"name": "Count", "graph": graph})
    run = done(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": saved["id"], "task": "one two three"}
        ),
    )
    assert run["status"] == "completed", run
    assert run["output"] == "Count words\n3" and calls == []
    assert run["providers_used"] == ["local-tool"]


def test_model_profiles_definition_edits_and_metadata_events(app):
    service, _ = app
    model = save(service, "models", {"provider": "claude-code", "model": "opus"})
    assert model["data"]["model"] == "opus"
    current = service.definitions("agents")[0]
    payload = {
        "name": "Reviewed",
        "provider": "codex",
        "instructions": "New instructions",
        "revision": current["revision"],
    }
    updated = service.update_definition("agents", current["id"], payload)
    assert updated["document"]["spec"]["purpose"] == "New instructions"
    with pytest.raises(ValueError, match="changed"):
        service.update_definition("agents", current["id"], payload)
    run = done(service, service.start_run({"task": "Generate event"}))
    events = service.mesh_status()["events"]
    assert any(event["run_id"] == run["id"] and event["status"] == "completed" for event in events)
    exported = service.export_definitions()
    assert exported["format"] == "nexus-studio-export/v1"
    assert "models" in exported["catalog"] and "sessions" not in exported


def test_completion_waits_until_output_artifact_is_saved(app, monkeypatch):
    service, _ = app
    from threading import Event

    entered, release = Event(), Event()
    original = service.catalog.capture_run

    def capture(run):
        entered.set()
        release.wait(3)
        return original(run)

    monkeypatch.setattr(service.catalog, "capture_run", capture)
    queued = service.start_run({"task": "Delay artifact save"})
    assert entered.wait(2)
    try:
        assert service.run(queued["id"])["status"] == "running"
    finally:
        release.set()
    run = done(service, queued)
    assert run["artifact_id"] and service.catalog.get("artifacts", run["artifact_id"])


def test_catalog_webhooks_connector_and_all_browser_assets_over_http(app, monkeypatch):
    import re
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from nexus.studio import make_studio_handler

    service, _ = app
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service, "test-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def request(path, body=None, headers=None):
        req = urllib.request.Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Content-Type": "application/json",
                "X-Nexus-Studio-Token": "test-csrf",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    try:
        status, _, html = request("/")
        assert status == 200
        for asset in re.findall(r'<script[^>]+src="([^"]+)"', html.decode()):
            assert request(asset)[0] == 200, asset
        status, _, body = request(
            "/api/catalog/prompts", {"name": "Reusable", "data": {"content": "Check evidence"}}
        )
        assert status == 201 and json.loads(body)["revision"] == 1
        artifact = save(service, "artifacts", {"filename": "sample.bin", "content_base64": "AP8="})
        status, headers, body = request(f"/api/catalog/artifacts/{artifact['id']}/download")
        assert (
            status == 200 and body == b"\x00\xff" and "attachment" in headers["Content-Disposition"]
        )
        entry = save(
            service,
            "entrypoints",
            {
                "target_kind": "agent",
                "target_id": "reviewer",
                "token_env": "NEXUS_TEST_WEBHOOK",
                "enabled": True,
            },
        )
        monkeypatch.setenv("NEXUS_TEST_WEBHOOK", "a-long-random-test-secret-at-least-24-characters")
        path = f"/api/webhooks/{entry['id']}"
        assert request(path, {"task": "Inbound"})[0] == 403
        assert request(path, {"task": "Inbound"}, {"Authorization": "Bearer wrong"})[0] == 403
        status, _, body = request(
            path,
            {"task": "Inbound"},
            {"Authorization": "Bearer a-long-random-test-secret-at-least-24-characters"},
        )
        assert status == 202 and done(service, json.loads(body))["status"] == "completed"
        assert (
            request(
                path,
                {"task": "Inbound"},
                {
                    "Origin": "https://outside.invalid",
                    "Authorization": "Bearer a-long-random-test-secret-at-least-24-characters",
                },
            )[0]
            == 403
        )
        assert b"a-long-random-test-secret" not in request("/api/export")[2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_graph_evaluation_cases_execute_independently(app):
    service, calls = app
    service.save_definition(
        "workflows",
        {
            "name": "Review graph",
            "graph": {
                "nodes": [
                    {
                        "id": "review",
                        "name": "Review",
                        "kind": "agent",
                        "agent_id": "reviewer",
                        "prompt": "{task}",
                    }
                ],
                "edges": [{"source": "START", "target": "review"}],
            },
        },
    )
    suite = save(
        service,
        "evaluations",
        {
            "target_kind": "workflow",
            "target_id": "review-graph",
            "cases": [
                {"input": "First unique input", "contains": ["Evidence"], "excludes": []},
                {"input": "Second unique input", "contains": ["Evidence"], "excludes": []},
            ],
        },
    )
    run = done(service, service.start_run({"kind": "evaluation", "evaluation_id": suite["id"]}))
    assert run["status"] == "completed", run
    assert len(calls) == 2
    assert "First unique input" in calls[0].prompt
    assert "Second unique input" in calls[1].prompt
    assert "First unique input" not in calls[1].prompt

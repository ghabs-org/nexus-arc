"""Scheduled dispatch acceptance through real catalog, queue, restart and providers."""

import concurrent.futures
import threading
from datetime import UTC, datetime, timedelta

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_calendar import next_occurrence
from nexus.core.studio_prompts import StudioPromptHistory
from nexus.core.studio_schedules import StudioSchedules
from tests.test_studio_knowledge_vectors import done


def service(tmp_path, calls, workers=0):
    class Model:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(success=True, output="Scheduled response")

    app = StudioService(
        tmp_path / "project",
        state_dir=tmp_path / "private",
        state_options={
            "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
            "namespace": "calendar-acceptance",
            "workers": workers,
        },
        provider_factory=lambda _: Model(),
    )
    if not app.definitions("agents"):
        app.save_definition(
            "agents", {"name": "Assistant", "provider": "codex", "instructions": "Help"}
        )
    return app


def schedule(app, start, name="Review", **data):
    return app.catalog.save(
        "schedules",
        {
            "name": name,
            "data": {
                "target_kind": "agent",
                "target_id": "assistant",
                "task": "Review",
                "run_at": start.isoformat(),
                "enabled": True,
                "interval_seconds": 0,
                **data,
            },
        },
    )


def test_two_controllers_coalesce_downtime_and_overdue_once_then_restart_workers(tmp_path):
    calls = []
    controller = service(tmp_path, calls)
    start = datetime(2026, 10, 19, 7, tzinfo=UTC)
    recurring = schedule(controller, start, interval_seconds=60)
    once = schedule(controller, start, name="One time")
    controller.close()  # Neither occurrence has a job before the restart.
    left, right = service(tmp_path, calls), service(tmp_path, calls)
    barrier = threading.Barrier(2)

    def tick(app):
        barrier.wait(timeout=5)
        app.operations.tick(start + timedelta(hours=2))

    try:
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            list(pool.map(tick, [left, right]))
        assert len(left.records("runs")) == 2
        assert len(left.records("schedule-occurrences")) == 2
        assert StudioSchedules(left).details(once["id"])["upcoming"] == []
        assert len(StudioSchedules(left).details(recurring["id"])["upcoming"]) == 5
    finally:
        left.close()
        right.close()
    worker = service(tmp_path, calls, workers=2)
    try:
        for run in worker.records("runs"):
            assert done(worker, run)["status"] == "completed"
        assert len(calls) == 2
        worker.operations.tick(start + timedelta(hours=2))
        assert len(worker.records("runs")) == 2
    finally:
        worker.close()


@pytest.mark.parametrize(
    "cron,first,blocked,next_due",
    [
        (
            "30 2 * * *",
            "2026-03-28T01:30:00+00:00",
            "2026-03-29T01:30:00+00:00",
            "2026-03-30T00:30:00+00:00",
        ),
        (
            "30 2 * * *",
            "2026-10-25T00:30:00+00:00",
            "2026-10-25T01:30:00+00:00",
            "2026-10-26T01:30:00+00:00",
        ),
        (
            "0 9 * * 1-5",
            "2026-03-27T08:00:00+00:00",
            "2026-03-29T07:00:00+00:00",
            "2026-03-30T07:00:00+00:00",
        ),
        (
            "0 9 * * 1-5",
            "2026-10-23T07:00:00+00:00",
            "2026-10-25T08:00:00+00:00",
            "2026-10-26T08:00:00+00:00",
        ),
    ],
)
def test_actual_cron_dispatch_preserves_prompt_and_dst_policy_across_restart(
    tmp_path, cron, first, blocked, next_due
):
    calls = []
    app = service(tmp_path, calls, workers=1)
    prompt = app.catalog.save(
        "prompts",
        {
            "name": "Daily review",
            "data": {
                "content": "Review {{Topic}}",
                "variables": {"Topic": "Invoices"},
                "shortcut": "daily",
            },
        },
    )
    initial = datetime.fromisoformat(first)
    assert next_occurrence(cron, "Europe/Rome", initial - timedelta(minutes=1)) == initial
    item = schedule(app, initial, cron=cron, timezone="Europe/Rome", task="/daily")
    app.operations.tick(initial)
    assert done(app, app.records("runs")[0])["status"] == "completed"
    assert "Review Invoices" in calls[0]
    app.close()
    app = service(tmp_path, calls, workers=1)
    try:
        app.operations.tick(datetime.fromisoformat(blocked))
        assert len(calls) == 1
        app.operations.tick(datetime.fromisoformat(next_due))
        for run in app.records("runs"):
            assert done(app, run)["status"] == "completed"
        assert len(calls) == 2
        details = StudioSchedules(app).details(item["id"], datetime.fromisoformat(next_due))
        assert details["schedule"]["data"]["timezone"] == "Europe/Rome"
        assert len(details["occurrences"]) == 2
        assert {v["status"] for v in details["occurrences"]} == {"completed"}
        assert prompt["id"] == "daily-review"
    finally:
        app.close()


def test_pause_resume_name_edit_and_legacy_state_do_not_replay_completed_once(tmp_path):
    app = service(tmp_path, [], workers=1)
    now = datetime(2026, 10, 19, 7, tzinfo=UTC)
    item = schedule(app, now)
    manager = StudioSchedules(app)
    try:
        app.operations.tick(now)
        assert done(app, app.records("runs")[0])["status"] == "completed"
        path = app._path("schedule-state", item["id"])
        legacy = app._read(path)
        legacy.pop("cadence")
        legacy.pop("version")
        app._write(path, legacy)
        paused = manager.enabled(item["id"], {"revision": item["revision"], "enabled": False})
        app.operations.tick(now + timedelta(days=1))
        renamed = app.catalog.save("schedules", {**paused, "name": "Renamed"})
        resumed = manager.enabled(item["id"], {"revision": renamed["revision"], "enabled": True})
        app.operations.tick(now + timedelta(days=2))
        assert len(app.records("runs")) == 1
        assert manager.details(item["id"])["upcoming"] == []
        with pytest.raises(ValueError, match="changed"):
            manager.enabled(item["id"], {"revision": item["revision"], "enabled": False})
        assert resumed["data"]["enabled"]
        assert len(app.catalog.history("schedules", item["id"])) == 3
    finally:
        app.close()


def test_paused_recurring_schedule_coalesces_once_when_explicitly_resumed(tmp_path):
    app = service(tmp_path, [], workers=1)
    now = datetime(2026, 10, 19, 7, tzinfo=UTC)
    item = schedule(app, now, interval_seconds=60)
    manager = StudioSchedules(app)
    try:
        paused = manager.enabled(item["id"], {"revision": 1, "enabled": False})
        app.operations.tick(now + timedelta(hours=4))
        assert app.records("runs") == []
        manager.enabled(item["id"], {"revision": paused["revision"], "enabled": True})
        app.operations.tick(now + timedelta(hours=4))
        assert done(app, app.records("runs")[0])["status"] == "completed"
        app.operations.tick(now + timedelta(hours=4))
        assert len(app.records("runs")) == 1
    finally:
        app.close()


def test_pause_after_listing_is_observed_before_claim(tmp_path, monkeypatch):
    app = service(tmp_path, [])
    item = schedule(app, datetime(2026, 10, 19, 7, tzinfo=UTC))
    listing = app.catalog.list

    def pause_after_listing(kind, **kwargs):
        result = listing(kind, **kwargs)
        if kind == "schedules":
            StudioSchedules(app).enabled(item["id"], {"revision": 1, "enabled": False})
        return result

    monkeypatch.setattr(app.catalog, "list", pause_after_listing)
    try:
        app.operations.tick(datetime(2026, 10, 19, 8, tzinfo=UTC))
        assert app.records("runs") == []
    finally:
        app.close()


def test_prompt_comparison_restore_conflicts_and_no_recursive_expansion(tmp_path):
    app = service(tmp_path, [])
    history = StudioPromptHistory(app)
    first = app.catalog.save(
        "prompts",
        {
            "name": "Review",
            "data": {
                "content": "Review {{Topic}}",
                "variables": {"Topic": "Invoices"},
                "tags": ["daily"],
                "shortcut": "daily",
            },
        },
    )
    second = app.catalog.save(
        "prompts",
        {
            **first,
            "data": {
                "content": "Explain {{Topic}}",
                "variables": {"Topic": "Payments"},
                "shortcut": "explain",
            },
        },
    )
    try:
        compare = history.compare(first["id"], 1)
        assert set(compare["changed_fields"]) == {"content", "variables", "tags", "shortcut"}
        assert compare["current_revision"] == 2 and compare["selected"]["variables"] == {
            "Topic": "Invoices"
        }
        with pytest.raises(ValueError, match="changed"):
            history.restore(first["id"], {"revision": 1, "restore_revision": 1})
        restored = history.restore(first["id"], {"revision": 2, "restore_revision": 1})
        assert restored["revision"] == 3 and restored["data"] == first["data"]
        assert app.catalog.history("prompts", first["id"])[0]["revision"] == second["revision"]
        assert app.catalog.expand({"task": "/daily"})["expanded_task"] == "Review Invoices"
        assert (
            app.catalog.expand(
                {"task": "/review", "prompt_values": {"review": {"Topic": "{{Topic}}"}}}
            )["expanded_task"]
            == "Review {{Topic}}"
        )
        with pytest.raises(ValueError, match="conflicts"):
            app.catalog.save(
                "prompts", {"name": "Other", "data": {"content": "Other", "shortcut": "daily"}}
            )
    finally:
        app.close()


def test_sql_dispatch_and_occurrence_commit_together_after_interruption(tmp_path, monkeypatch):
    app = service(tmp_path, [])
    now = datetime(2026, 10, 19, 7, tzinfo=UTC)
    item = schedule(app, now)
    write = app._write
    injected = []

    def fail_after_enqueue(path, record):
        if (
            getattr(path, "collection", "") == "schedule-occurrences"
            and record.get("run_id")
            and not injected
        ):
            injected.append(True)
            raise OSError("Interrupted before dispatch transaction commit")
        return write(path, record)

    monkeypatch.setattr(app, "_write", fail_after_enqueue)
    try:
        with pytest.raises(OSError, match="Interrupted"):
            app.operations.tick(now)
        assert app.records("runs") == [] and app.records("schedule-occurrences") == []
        assert not app._path("schedule-state", item["id"]).exists()
        app.operations.tick(now)
        assert len(app.records("runs")) == 1 and len(app.records("schedule-occurrences")) == 1
    finally:
        app.close()


def test_cadence_edit_does_not_overlap_queued_occurrence(tmp_path):
    calls = []
    app = service(tmp_path, calls)
    now = datetime(2026, 10, 19, 7, tzinfo=UTC)
    item = schedule(app, now, interval_seconds=60)
    app.operations.tick(now)
    app.catalog.save("schedules", {**item, "data": {**item["data"], "interval_seconds": 120}})
    app.operations.tick(now + timedelta(minutes=5))
    assert len(app.records("runs")) == 1
    app.close()
    app = service(tmp_path, calls, workers=1)
    try:
        assert done(app, app.records("runs")[0])["status"] == "completed"
        app.operations.tick(now + timedelta(minutes=5))
        assert len(app.records("runs")) == 2
        for run in app.records("runs"):
            assert done(app, run)["status"] == "completed"
        assert len(calls) == 2
    finally:
        app.close()


def test_quick_build_receives_rendered_prompt_and_variables(tmp_path):
    from tests.test_quick_build import proposal

    contexts = []

    class Builder:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            import json

            contexts.append(context.prompt)
            return AgentResult(success=True, output=json.dumps(proposal()))

    app = service(tmp_path, [], workers=1)
    app.builder.provider_factory = lambda _: Builder()
    prompt = app.catalog.save(
        "prompts",
        {
            "name": "Review",
            "data": {
                "content": "Review {{Topic}}",
                "variables": {"Topic": "Invoices"},
                "shortcut": "daily",
            },
        },
    )
    try:
        run = app.start_run(
            {
                "kind": "build",
                "task": "/daily",
                "prompt_values": {prompt["id"]: {"Topic": "Quarterly invoices"}},
            }
        )
        assert done(app, run)["status"] == "completed"
        assert "Review Quarterly invoices" in contexts[0] and "/daily" not in contexts[0]
    finally:
        app.close()


def test_prompt_restore_revalidates_reclaimed_shortcut_and_revisions(tmp_path):
    app = service(tmp_path, [])
    history = StudioPromptHistory(app)
    first = app.catalog.save(
        "prompts", {"name": "Review", "data": {"content": "First", "shortcut": "daily"}}
    )
    app.catalog.save("prompts", {**first, "data": {"content": "Second", "shortcut": "weekly"}})
    app.catalog.save(
        "prompts", {"name": "Another", "data": {"content": "Other", "shortcut": "daily"}}
    )
    try:
        with pytest.raises(ValueError, match="conflicts"):
            history.restore(first["id"], {"revision": 2, "restore_revision": 1})
        assert app.catalog.get("prompts", first["id"])["revision"] == 2
        with pytest.raises(ValueError, match="positive"):
            history.compare(first["id"], True)
        with pytest.raises(ValueError, match="changed"):
            history.restore(first["id"], {"revision": True, "restore_revision": 1})
    finally:
        app.close()


def test_lifecycle_http_routes_require_csrf_and_reject_stale_review(tmp_path):
    import json
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from nexus.studio import make_studio_handler

    app = service(tmp_path, [])
    item = schedule(app, datetime(2030, 1, 7, 8, tzinfo=UTC), enabled=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(app, "fixture-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/api/studio/schedules/{item['id']}"

    def post(payload, csrf):
        req = urllib.request.Request(
            url + "/enabled",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Nexus-Studio-Token": csrf},
        )
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    try:
        with urllib.request.urlopen(url) as response:
            result = json.load(response)
        assert result["version"] == 1 and len(result["upcoming"]) == 1
        assert post({"revision": 1, "enabled": True}, "wrong")[0] == 403
        assert post({"revision": 1, "enabled": True}, "fixture-csrf")[0] == 200
        assert post({"revision": 1, "enabled": False}, "fixture-csrf")[0] == 400
        assert app.catalog.get("schedules", item["id"])["data"]["enabled"]
        assert post({"revision": 2, "enabled": "false"}, "fixture-csrf")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()


@pytest.mark.parametrize("resource", ["schedules", "prompts"])
def test_lifecycle_mutations_retain_builder_permission(tmp_path, resource):
    from nexus.core.studio_team import NexusTeamPolicy, TeamAccessError
    from tests.test_studio_team import NexusIdentity

    app = service(tmp_path, [])
    app.team = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login",
        bindings={"alice": "operator", "owner": "owner"},
        manager=NexusIdentity(),
    )
    try:
        with pytest.raises(TeamAccessError, match="builder"):
            app.features.post(
                [resource, "missing", "enabled" if resource == "schedules" else "restore"],
                {"revision": 1, "enabled": True, "restore_revision": 1},
                {"nexus_id": "alice", "role": "operator"},
            )
    finally:
        app.team = None
        app.close()


def test_missing_legacy_history_preserves_completed_progress_until_reviewed_timing_edit(tmp_path):
    app = service(tmp_path, [], workers=1)
    now = datetime(2026, 10, 19, 7, tzinfo=UTC)
    item = schedule(app, now)
    manager = StudioSchedules(app)
    try:
        app.operations.tick(now)
        assert done(app, app.records("runs")[0])["status"] == "completed"
        path = app._path("schedule-state", item["id"])
        legacy = app._read(path)
        legacy.pop("cadence")
        app._write(path, legacy)
        renamed = app.catalog.save("schedules", {**item, "name": "Renamed"})
        app._path(f"catalog-history/schedules/{item['id']}", "1").unlink()
        app.operations.tick(now + timedelta(days=1))
        assert len(app.records("runs")) == 1
        assert manager.details(item["id"])["state"]["legacy_timing_unverified"]
        paused = manager.enabled(item["id"], {"revision": renamed["revision"], "enabled": False})
        rescheduled = app.catalog.save(
            "schedules",
            {
                **paused,
                "data": {
                    **paused["data"],
                    "run_at": (now + timedelta(days=2)).isoformat(),
                    "enabled": True,
                },
            },
        )
        app.operations.tick(now + timedelta(days=2))
        assert len(app.records("runs")) == 2
        assert not manager.details(rescheduled["id"])["state"].get("legacy_timing_unverified")
    finally:
        app.close()


def test_schedule_ready_made_workflow_without_task_and_with_input(tmp_path):
    calls = []
    app = service(tmp_path, calls, workers=0)
    try:
        app.save_definition(
            "workflows",
            {
                "id": "content-pipeline",
                "name": "Content Pipeline",
                "description": "Generate and review content",
                "steps": [{"name": "Step 1", "agent_type": "assistant"}],
            },
        )
        now = datetime(2026, 10, 19, 7, tzinfo=UTC)
        # Agent schedule still requires a task
        with pytest.raises(ValueError, match="Task"):
            app.catalog.save(
                "schedules",
                {
                    "name": "Agent Schedule Missing Task",
                    "data": {
                        "target_kind": "agent",
                        "target_id": "assistant",
                        "run_at": now.isoformat(),
                        "enabled": True,
                        "interval_seconds": 0,
                    },
                },
            )

        # Ready-made workflow schedule succeeds without explicit task and accepts workflow_input
        saved = app.catalog.save(
            "schedules",
            {
                "name": "Workflow Schedule",
                "data": {
                    "target_kind": "workflow",
                    "target_id": "content-pipeline",
                    "workflow_input": {"topic": "AI agents"},
                    "run_at": now.isoformat(),
                    "enabled": True,
                    "interval_seconds": 0,
                },
            },
        )
        assert saved["data"]["task"] == "Generate and review content"
        assert saved["data"]["workflow_input"] == {"topic": "AI agents"}

        launched = []
        original_start_run = app.start_run

        def tracking_start_run(payload, **kwargs):
            launched.append(dict(payload))
            return original_start_run(payload, **kwargs)

        app.start_run = tracking_start_run

        app.operations.tick(now)
        runs = app.records("runs")
        assert len(runs) == 1
        assert len(launched) == 1
        assert launched[0]["kind"] == "workflow"
        assert launched[0]["workflow_id"] == "content-pipeline"
        assert launched[0]["task"] == "Generate and review content"
        assert launched[0]["workflow_input"] == {"topic": "AI agents"}
    finally:
        app.close()


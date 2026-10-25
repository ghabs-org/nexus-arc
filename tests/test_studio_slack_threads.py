"""Signed Slack thread history reuses owned Nexus sessions without replay."""

import hashlib
import hmac
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_messaging import done, entry
from tests.test_studio_team import team as team


def test_two_sql_controllers_serialize_the_same_thread(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    entered, release = threading.Event(), threading.Event()
    prompts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            if len(prompts) == 1:
                entered.set()
                assert release.wait(5)
            return AgentResult(success=True, output=f"Shared reply {len(prompts)}")

    options = {
        "url": "sqlite:///" + str(tmp_path / "shared.sqlite"),
        "namespace": "shared",
        "workers": 1,
    }
    left = StudioService(
        tmp_path / "workspace", state_options=options, provider_factory=lambda _: Provider()
    )
    right = None
    try:
        left.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
        entry(left, kind="slack", threaded_conversations=True)
        right = StudioService(
            left.root, state_options=options, provider_factory=lambda _: Provider()
        )
        receive(left, 1)
        left.messaging.tick()
        assert entered.wait(2)
        for number in [2, 3]:
            receive(left, number, thread="1700000001.000001")
            receive(right, number, thread="1700000001.000001")
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(service.messaging.tick) for service in [left, right]]
            for future in futures:
                future.result(timeout=5)
        assert len(left.records("runs")) == 1
        release.set()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            left.messaging.tick()
            right.messaging.tick()
            if all(e["status"] == "completed" for e in left.records("channel-events")):
                break
            time.sleep(0.01)
        runs = sorted(left.records("runs"), key=lambda r: r["created_at"])
        assert [r["task"] for r in runs] == ["Question 1", "Question 2", "Question 3"]
        assert len({r["session_id"] for r in runs}) == 1
        assert len(prompts) == 3 and len(left.session(runs[0]["session_id"])["messages"]) == 6
    finally:
        release.set()
        if right:
            right.close()
        left.close()


@pytest.mark.parametrize("sql", [False, True])
def test_thread_metadata_survives_stopped_backup_restore(tmp_path, monkeypatch, sql):
    from nexus.core.studio_backup import create_snapshot, restore_snapshot
    from nexus.core.studio_state import StudioSQLState

    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(success=True, output="Saved reply")

    url = "sqlite:///" + str(tmp_path / "state.sqlite")
    app = StudioService(
        tmp_path / "workspace",
        provider_factory=lambda _: Provider(),
        state_options={"url": url, "namespace": "source", "workers": 1} if sql else None,
    )
    try:
        app.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
        entry(app, kind="slack", threaded_conversations=True)
        receive(app, 1)
        run = finish(app)
        session = app.session(run["session_id"])
        root, state = app.root, app.state
    finally:
        app.close()
    source = StudioSQLState(url, "source") if sql else None
    target = StudioSQLState(url, "restored") if sql else None
    try:
        create_snapshot(root, tmp_path / "backup", state_dir=state, sql_state=source, quiesced=True)
        result = restore_snapshot(
            tmp_path / "backup", tmp_path / "restored", sql_state=target, quiesced=sql
        )
        restored = StudioService(
            result["root"],
            state_dir=result["state_dir"],
            provider_factory=lambda _: Provider(),
            state_options={"url": url, "namespace": "restored", "workers": 1} if sql else None,
        )
        try:
            assert (
                restored.session(run["session_id"])["slack_thread_binding"]
                == session["slack_thread_binding"]
            )
            assert restored.session(run["session_id"])["messages"] == session["messages"]
            assert not restored.catalog.get("entrypoints", "incoming")["data"]["enabled"]
            restored.messaging.tick()
            assert len(calls) == 1
        finally:
            restored.close()
    finally:
        if source:
            source.engine.dispose()
        if target:
            target.engine.dispose()


def test_shared_threads_never_inherit_other_users_history_and_revoke_pending_work(
    team, monkeypatch
):
    studio, _, manager, _, calls = team
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    entry(
        studio,
        kind="slack",
        threaded_conversations=True,
        identity_map={"alice": "alice", "bob": "bob"},
    )
    receive(studio, 1, text="Private Alice history")
    first = finish(studio)
    receive(studio, 2, sender="bob", thread="1700000001.000001")
    second = finish(studio)
    assert studio.session(first["session_id"])["owner_id"] == "alice"
    assert studio.session(second["session_id"])["owner_id"] == "bob"
    assert "Private Alice history" not in calls[-1].prompt
    receive(studio, 3, thread="1700000001.000001")
    manager.revoked.add("alice")
    studio.messaging.tick()
    assert len(studio.records("runs")) == 2
    assert any(e["status"] == "failed" for e in studio.records("channel-events"))


@pytest.mark.parametrize("sql", [False, True])
def test_thread_binding_and_history_survive_controller_restart(tmp_path, monkeypatch, sql):
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    prompts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            return AgentResult(success=True, output="Persistent reply")

    options = (
        {
            "state_options": {
                "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
                "namespace": "threads",
                "workers": 1,
            }
        }
        if sql
        else {}
    )
    root = tmp_path / "workspace"
    first_service = StudioService(root, provider_factory=lambda _: Provider(), **options)
    try:
        first_service.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
        entry(first_service, kind="slack", threaded_conversations=True)
        receive(first_service, 1)
        first = finish(first_service)
    finally:
        first_service.close()
    restarted = StudioService(root, provider_factory=lambda _: Provider(), **options)
    try:
        receive(restarted, 2, thread="1700000001.000001")
        second = finish(restarted)
        assert second["session_id"] == first["session_id"]
        assert "Persistent reply" in prompts[-1] and len(restarted.records("runs")) == 2
        receive(restarted, 1)
        restarted.messaging.tick()
        assert len(restarted.records("runs")) == 2
    finally:
        restarted.close()


def test_pending_messages_wait_in_receipt_order_and_deduplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    entered = threading.Event()
    release = threading.Event()
    prompts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            if len(prompts) == 1:
                entered.set()
                assert release.wait(5)
            return AgentResult(success=True, output=f"Reply {len(prompts)}")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
        entry(studio, kind="slack", threaded_conversations=True)
        receive(studio, 1)
        studio.messaging.tick()
        assert entered.wait(2)
        receive(studio, 2, thread="1700000001.000001")
        receive(studio, 3, thread="1700000001.000001")
        receive(studio, 2, thread="1700000001.000001")
        studio.messaging.tick()
        assert len(studio.records("runs")) == 1
        assert sum(e["status"] == "slack_received" for e in studio.records("channel-events")) == 2
        release.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            studio.messaging.tick()
            if all(e["status"] == "completed" for e in studio.records("channel-events")):
                break
            time.sleep(0.01)
        runs = sorted(studio.records("runs"), key=lambda r: r["created_at"])
        assert [r["task"] for r in runs] == ["Question 1", "Question 2", "Question 3"]
        assert len({r["session_id"] for r in runs}) == 1 and len(prompts) == 3
        assert "Question 2" in prompts[-1] and "Reply 2" in prompts[-1]
    finally:
        release.set()
        studio.close()


def test_uncertain_dispatch_is_never_replayed_and_blocks_implicit_followups(app, monkeypatch):
    studio, _ = app
    receive(studio, 1)

    def interrupted(*args, **kwargs):
        raise RuntimeError("Synthetic controller failure")

    original = studio.operations.launch_target
    monkeypatch.setattr(studio.operations, "launch_target", interrupted)
    # Mimic abrupt failure: the durable launching receipt remains, as it does
    # when process death bypasses the normal exception handler.
    from nexus.core.studio_slack_threads import context

    event = studio.records("channel-events")[0]
    with studio.lock:
        context(studio, event, studio.catalog.get("entrypoints", "incoming"))
        event["status"] = "slack_launching"
        studio._write(studio._path("channel-events", event["id"]), event)
    receive(studio, 2, thread="1700000001.000001")
    studio.messaging.tick()
    assert not studio.records("runs")
    assert any(e["status"] == "failed" for e in studio.records("channel-events"))
    monkeypatch.setattr(studio.operations, "launch_target", original)
    record = studio.catalog.get("entrypoints", "incoming")
    studio.catalog.save(
        "entrypoints", {**record, "description": "Owner reviewed uncertain dispatch"}
    )
    receive(studio, 3, thread="1700000001.000001")
    run = finish(studio)
    assert run["task"] == "Question 3" and len(studio.records("runs")) == 1


def receive(
    studio,
    number,
    *,
    sender="alice",
    channel="room",
    thread=None,
    text=None,
    workspace="TSELECTED",
    extra=None,
):
    event = {
        "type": "message",
        "user": sender,
        "channel": channel,
        "text": text or f"Question {number}",
        "ts": f"17000000{number:02}.000001",
    }
    if thread is not None:
        event["thread_ts"] = thread
    event.update(extra or {})
    body = json.dumps(
        {
            "type": "event_callback",
            "team_id": workspace,
            "event_id": f"event-{number}",
            "event": event,
        }
    ).encode()
    timestamp = str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(b"s" * 32, b"v0:" + timestamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    )
    return studio.messaging.receive(
        "incoming", {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}, body
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    prompts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            return AgentResult(success=True, output=f"Reply {len(prompts)}")

    studio = StudioService(tmp_path, provider_factory=lambda _: Provider())
    studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    entry(studio, kind="slack", threaded_conversations=True)
    try:
        yield studio, prompts
    finally:
        studio.close()


def finish(studio):
    studio.messaging.tick()
    run = done(studio, studio.records("runs")[0])
    studio.messaging.tick()
    return run


def test_thread_messages_continue_the_same_owned_conversation(app):
    studio, prompts = app
    receive(studio, 1)
    first = finish(studio)
    receive(studio, 2, thread="1700000001.000001")
    second = finish(studio)
    assert first["session_id"] == second["session_id"]
    assert "Question 1" in prompts[1] and "Reply 1" in prompts[1]
    assert len(studio.session(first["session_id"])["messages"]) == 4


def test_missing_conversation_is_not_silently_replaced(app):
    studio, _ = app
    receive(studio, 1)
    run = finish(studio)
    path = studio._path("sessions", run["session_id"])
    path.unlink()
    receive(studio, 2, thread="1700000001.000001")
    studio.messaging.tick()
    assert len(studio.records("runs")) == 1 and not path.exists()
    assert any(e["status"] == "failed" for e in studio.records("channel-events"))


def test_file_restart_marks_thread_launch_uncertain_without_replaying(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    studio = StudioService(tmp_path)
    try:
        studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
        entry(studio, kind="slack", threaded_conversations=True)
        receive(studio, 1)
        event = studio.records("channel-events")[0]
        event["status"] = "slack_launching"
        studio._write(studio._path("channel-events", event["id"]), event)
    finally:
        studio.close()
    restarted = StudioService(tmp_path)
    try:
        assert restarted.records("channel-events")[0]["status"] == "uncertain"
        receive(restarted, 2, thread="1700000001.000001")
        restarted.messaging.tick()
        assert not restarted.records("runs")
        assert any(e["status"] == "failed" for e in restarted.records("channel-events"))
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "change", [{"sender": "bob"}, {"thread": "1700000099.000001"}, {"workspace": "TOTHER"}]
)
def test_thread_history_isolates_sender_root_and_slack_workspace(app, change):
    studio, prompts = app
    receive(studio, 1, text="Private first question")
    first = finish(studio)
    receive(studio, 2, **{"thread": "1700000001.000001", **change})
    second = finish(studio)
    assert first["session_id"] != second["session_id"]
    assert "Private first question" not in prompts[-1]


def test_duplicate_events_and_client_session_ids_cannot_select_history(app):
    studio, prompts = app
    receive(studio, 1)
    first = finish(studio)
    receive(studio, 1)
    studio.messaging.tick()
    assert len(studio.records("runs")) == 1
    receive(
        studio,
        2,
        sender="bob",
        thread="1700000001.000001",
        extra={"session_id": first["session_id"]},
    )
    second = finish(studio)
    assert first["session_id"] != second["session_id"] and len(prompts) == 2


@pytest.mark.parametrize(
    "extra",
    [{"ts": None}, {"ts": 1.2}, {"ts": "not-ts"}, {"thread_ts": "../bad"}, {"thread_ts": None}],
)
def test_bad_verified_thread_metadata_fails_before_receipt_or_run(app, extra):
    studio, _ = app
    with pytest.raises(ValueError, match="timestamp"):
        receive(studio, 1, extra=extra)
    assert not studio.records("channel-events") and not studio.records("runs")


def test_entrypoint_revision_resets_binding_and_blocks_stale_pending_work(app):
    studio, prompts = app
    receive(studio, 1)
    first = finish(studio)
    receive(studio, 2, thread="1700000001.000001")
    saved = studio.catalog.get("entrypoints", "incoming")
    studio.catalog.save("entrypoints", {**saved, "description": "Reviewed revised entrypoint"})
    studio.messaging.tick()
    assert len(studio.records("runs")) == 1
    assert any(event["status"] == "failed" for event in studio.records("channel-events"))
    receive(studio, 3, thread="1700000001.000001")
    new = finish(studio)
    assert new["session_id"] != first["session_id"] and "Question 1" not in prompts[-1]


@pytest.mark.parametrize(
    "change", [{"archived": True}, {"owner_id": "stranger"}, {"slack_thread_binding": {}}]
)
def test_archived_or_changed_session_is_not_adopted_or_replaced(app, change):
    studio, _ = app
    receive(studio, 1)
    first = finish(studio)
    path = studio._path("sessions", first["session_id"])
    modified = {**studio.session(first["session_id"]), **change}
    studio._write(path, modified)
    receive(studio, 2, thread="1700000001.000001")
    studio.messaging.tick()
    assert len(studio.records("runs")) == 1
    assert studio.session(first["session_id"]) == modified
    assert len(studio.records("sessions")) == 1


def test_replies_use_verified_parent_and_old_notify_cannot_follow_changed_config(app):
    studio, _ = app
    adapter = AsyncMock()
    adapter.send_message.return_value = "1700000009.000001"
    studio.connections.factories["slack"] = lambda *_: adapter
    studio.catalog.save(
        "connectors",
        {
            "name": "Reply",
            "data": {"type": "slack", "channel": "room", "actions": ["send"], "enabled": True},
        },
    )
    record = studio.catalog.get("entrypoints", "incoming")
    record = studio.catalog.save(
        "entrypoints",
        {
            **record,
            "data": {**record["data"], "auto_reply": True, "notification_connector_id": "reply"},
        },
    )
    receive(studio, 1)
    run = finish(studio)
    assert adapter.send_message.call_args.kwargs == {"thread_ts": "1700000001.000001"}
    event = studio.records("channel-events")[0]
    studio.catalog.save(
        "entrypoints", {**record, "data": {**record["data"], "identity_map": {"bob": "desktop"}}}
    )
    with pytest.raises(PermissionError):
        studio.messaging.notify(event, record["data"], run)
    assert adapter.send_message.call_count == 1


@pytest.mark.parametrize("value", [None, "true", 1, {}, []])
def test_opt_in_must_be_boolean(app, value):
    studio, _ = app
    record = studio.catalog.get("entrypoints", "incoming")
    with pytest.raises(ValueError, match="boolean"):
        studio.catalog.save(
            "entrypoints", {**record, "data": {**record["data"], "threaded_conversations": value}}
        )
    assert studio.catalog.get("entrypoints", "incoming") == record


def test_legacy_slack_remains_unthreaded_without_opt_in(app):
    studio, prompts = app
    record = studio.catalog.get("entrypoints", "incoming")
    data = {k: v for k, v in record["data"].items() if k != "threaded_conversations"}
    studio.catalog.save("entrypoints", {**record, "data": data})
    receive(studio, 1)
    first = finish(studio)
    receive(studio, 2, thread="1700000001.000001")
    second = finish(studio)
    assert first["session_id"] != second["session_id"] and "Question 1" not in prompts[-1]


def test_workflow_target_rejects_thread_opt_in(app):
    studio, _ = app
    studio.save_definition(
        "workflows",
        {
            "name": "Review workflow",
            "graph": {
                "version": 2,
                "nodes": [{"id": "ask", "kind": "input", "output_schema": {"type": "integer"}}],
            },
        },
    )
    record = studio.catalog.get("entrypoints", "incoming")
    with pytest.raises(ValueError, match="agent target"):
        studio.catalog.save(
            "entrypoints",
            {
                **record,
                "data": {
                    **record["data"],
                    "target_kind": "workflow",
                    "target_id": "review-workflow",
                },
            },
        )

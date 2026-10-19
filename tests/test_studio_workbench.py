"""Workbench uses real ARC engines and ownership rules with injected external services."""

import asyncio
import base64
import json
import time

import pytest

from nexus.adapters.social.base import PublishResult, SocialPlatformAdapter
from nexus.adapters.transcription.base import TranscriptionResult
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def done(service, run):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        with service.lock:
            record = service.run(run["id"])
            if record["status"] not in {"queued", "running"}:
                return record
        time.sleep(0.01)
    pytest.fail("Job did not stop")


@pytest.fixture
def app(tmp_path):
    calls, publishes, audio = [], [], []
    flags = {"fail_beta": False, "parallel": 0, "max_parallel": 0}

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            flags["parallel"] += 1
            flags["max_parallel"] = max(flags["max_parallel"], flags["parallel"])
            try:
                await asyncio.sleep(0.03)
                if context.agent_name == "beta" and flags["fail_beta"]:
                    flags["fail_beta"] = False
                    return AgentResult(success=False, output="", error="One-time test failure")
                return AgentResult(success=True, output=f"Evidence from {context.agent_name}")
            finally:
                flags["parallel"] -= 1

    class Publisher(SocialPlatformAdapter):
        platform = "linkedin"

        def validate(self, post):
            return ["Bad content"] if "invalid" in post.content else []

        async def publish(self, post):
            publishes.append(post)
            return PublishResult.ok(post.platform, post.campaign_id, "test-key", "test-post")

    class Transcription:
        async def check_availability(self):
            return True

        async def transcribe(self, value):
            audio.append(value)
            return TranscriptionResult(
                text="Transcribed evidence",
                provider_used="test-whisper",
                language="en",
                segments=[],
            )

    service = StudioService(
        tmp_path,
        provider_factory=lambda name: Provider(),
        workbench_options={
            "publisher_factory": lambda platform, identity: Publisher(),
            "transcription_factory": lambda identity: Transcription(),
            "decision_client": lambda state, questions, **kwargs: {
                "route": {
                    "choice": "alpha",
                    "confidence": 0.8,
                    "probabilities": {"alpha": 0.8, "beta": 0.2},
                }
            },
        },
    )
    for name in ["alpha", "beta"]:
        service.save_definition(
            "agents", {"name": name, "provider": "codex", "instructions": "Use supplied evidence"}
        )
    yield service, calls, publishes, audio, flags
    service.close()


def test_memory_rename_archive_restore_export_preserves_messages(app):
    service, *_ = app
    run = done(service, service.start_run({"task": "Private memory evidence"}))
    identity = run["session_id"]
    original = service.session(identity)["messages"]
    service.workbench.edit_memory(identity, {"title": "Evidence notes", "archived": True}, None)
    assert service.workbench.memory(None)[0]["archived"] is True
    with pytest.raises(ValueError, match="Restore"):
        service.start_run({"task": "Continue", "session_id": identity})
    exported = service.workbench.get(["memory", identity, "export"], None)
    assert exported["conversation"]["messages"] == original
    service.workbench.edit_memory(identity, {"archived": False}, None)
    assert service.session(identity)["title"] == "Evidence notes"
    assert (
        done(service, service.start_run({"task": "Continue", "session_id": identity}))["status"]
        == "completed"
    )


def test_checkpoint_uses_sqlite_and_resumes_failed_graph_without_repeating_completed_nodes(app):
    service, calls, _, _, flags = app
    flags["fail_beta"] = True
    graph = {
        "nodes": [
            {"id": "first", "name": "First", "agent_id": "alpha", "prompt": "{task}"},
            {"id": "second", "name": "Second", "agent_id": "beta", "prompt": "{task}"},
        ],
        "edges": [{"source": "START", "target": "first"}, {"source": "first", "target": "second"}],
    }
    service.save_definition("workflows", {"name": "Resume test", "graph": graph})
    run = done(
        service,
        service.start_run(
            {"kind": "workflow", "workflow_id": "resume-test", "task": "Check evidence"}
        ),
    )
    assert run["status"] == "failed"
    checkpoint = service.workbench.checkpoint(run["id"], None)
    assert checkpoint["state"]["nodes"] == {"first": "completed"}
    assert (
        service._path("checkpoints", run["id"], "sqlite")
        .read_bytes()
        .startswith(b"SQLite format 3")
    )
    with pytest.raises(ValueError, match="changed"):
        service.workbench.resume_checkpoint(run["id"], {"digest": "stale"}, None)
    resumed = done(
        service,
        service.workbench.resume_checkpoint(run["id"], {"digest": checkpoint["digest"]}, None),
    )
    assert resumed["status"] == "completed", resumed
    assert [context.agent_name for context in calls] == ["alpha", "beta", "beta"]
    assert service.workbench.checkpoint(run["id"], None)["state"]["nodes"]["second"] == "completed"


@pytest.mark.parametrize("strategy", ["sequential", "parallel"])
def test_delegation_uses_arc_composites_and_real_provider_selection(app, strategy):
    service, calls, _, _, flags = app
    run = done(
        service,
        service.start_run(
            {
                "kind": "delegation",
                "strategy": strategy,
                "agent_ids": ["alpha", "beta"],
                "task": "Check evidence",
            }
        ),
    )
    assert run["status"] == "completed", run
    assert run["delegated_agents"] == ["alpha", "beta"]
    assert len(calls) == 2
    if strategy == "sequential":
        assert "Evidence from alpha" in calls[1].prompt and flags["max_parallel"] == 1
    else:
        assert "Evidence from alpha" not in calls[1].prompt and flags["max_parallel"] == 2
    peers = service.workbench.peer_cards(None)
    assert len(peers) == 2 and all(peer["state"] == "finished" for peer in peers)


def campaign(service, text="Reviewed evidence"):
    return service.workbench.campaigns.save(
        {"name": "Evidence launch", "channels": {"linkedin": {"content": text}}}, None
    )


def validate(service, record):
    run = service.workbench.campaigns.post(
        [record["id"], "validate"], {"revision": record["revision"]}, None
    )[1]
    assert done(service, run)["status"] == "completed"
    return service.workbench.campaigns.record(record["id"], None)


def test_campaign_review_is_revision_bound_and_publish_requires_confirmation_once(app):
    service, _, publishes, *_ = app
    record = campaign(service)
    with pytest.raises(ValueError, match="Validate"):
        service.workbench.campaigns.approve(record["id"], {"revision": 1}, None, True)
    record = validate(service, record)
    assert publishes == []
    service.workbench.campaigns.approve(record["id"], {"revision": 1}, None, True)
    with pytest.raises(ValueError, match="confirm"):
        service.workbench.campaigns.post([record["id"], "publish"], {"revision": 1}, None)
    run = service.workbench.campaigns.post(
        [record["id"], "publish"], {"revision": 1, "confirm_publish": True}, None
    )[1]
    assert done(service, run)["status"] == "completed"
    assert len(publishes) == 1 and publishes[0].content == "Reviewed evidence"
    assert service.workbench.campaigns.record(record["id"], None)["status"] == "published"
    with pytest.raises(ValueError):
        service.workbench.campaigns.post(
            [record["id"], "publish"], {"revision": 1, "confirm_publish": True}, None
        )
    assert len(publishes) == 1


def test_campaign_edits_invalidate_validation_and_approval(app):
    service, *_ = app
    record = validate(service, campaign(service))
    service.workbench.campaigns.approve(record["id"], {"revision": 1}, None, True)
    updated = service.workbench.campaigns.save(
        {
            "id": record["id"],
            "revision": 1,
            "name": "Revised",
            "channels": {"linkedin": {"content": "Different evidence"}},
        },
        None,
    )
    assert updated["status"] == "draft" and updated["revision"] == 2
    with pytest.raises(ValueError, match="Validate"):
        service.workbench.campaigns.approve(record["id"], {"revision": 2}, None, True)


def test_uncertain_publishing_is_not_automatically_retried(app):
    service, *_ = app
    record = validate(service, campaign(service))
    service.workbench.campaigns.approve(record["id"], {"revision": 1}, None, True)
    attempted = []

    class Ambiguous:
        async def publish(self, post):
            attempted.append(post)
            raise TimeoutError("Remote acknowledgement missing")

    service.workbench.campaigns.factory = lambda platform, identity: Ambiguous()
    run = service.workbench.campaigns.post(
        [record["id"], "publish"], {"revision": 1, "confirm_publish": True}, None
    )[1]
    assert done(service, run)["status"] == "failed"
    assert service.workbench.campaigns.record(record["id"], None)["status"] == "uncertain"
    with pytest.raises(ValueError):
        service.workbench.campaigns.post(
            [record["id"], "publish"], {"revision": 1, "confirm_publish": True}, None
        )
    assert len(attempted) == 1


def test_transcription_keeps_audio_out_of_run_records_and_removes_input_bytes(app):
    service, _, _, audio, _ = app
    run = service.workbench.jobs.upload_transcription(
        {
            "filename": "note.wav",
            "content_base64": base64.b64encode(b"test-only-audio").decode(),
            "language": "en",
        },
        None,
    )
    result = done(service, run)
    assert result["status"] == "completed" and result["output"] == "Transcribed evidence"
    assert audio[0].source == b"test-only-audio" and audio[0].language == "en"
    assert service.workbench.get(["transcriptions"], None)["items"][0]["filename"] == "note.wav"
    assert not list((service.state / "audio-bytes").glob("*.wav"))
    assert "test-only-audio" not in json.dumps(result)
    for filename in ["../outside.wav", "https://example.test/a.wav", "note.exe"]:
        with pytest.raises(ValueError):
            service.workbench.jobs.upload_transcription(
                {"filename": filename, "content_base64": "eA=="}, None
            )


@pytest.mark.parametrize(
    "answer,accepted,choice",
    [
        ({"choice": "alpha", "confidence": 0.8}, True, "alpha"),
        ({"choice": "alpha", "confidence": 0.2}, False, "beta"),
        ({"choice": "unknown", "confidence": 0.9}, False, "beta"),
        ({"choice": "alpha", "confidence": float("nan")}, False, "beta"),
        ({}, False, "beta"),
    ],
)
def test_typed_decisions_use_explicit_fallback_for_uncertain_or_invalid_results(
    app, answer, accepted, choice
):
    service, *_ = app
    service.workbench.jobs.decision_client = lambda *args, **kwargs: {"route": answer}
    run = done(
        service,
        service.start_run(
            {
                "kind": "decision",
                "task": "Review evidence",
                "instructions": "Choose a specialist",
                "options": {"alpha": "Research", "beta": "Review"},
                "default": "beta",
                "threshold": 0.6,
                "backend": "openrouter",
            }
        ),
    )
    assert run["status"] == "completed", run
    record = service.workbench.get(["decisions"], None)["items"][0]
    assert record["accepted"] is accepted and record["choice"] == choice


def test_personal_prompt_budget_keeps_current_task_and_guard_survives_restart(app):
    service, calls, *_ = app
    first = done(service, service.start_run({"task": "Long prior context " * 400}))
    controls = service.workbench.controls(None)
    service.workbench.save_controls(
        {
            "revision": controls["revision"],
            "prompt_budget_enabled": True,
            "max_prompt_chars": 1024,
            "summary_max_chars": 120,
            "cooldown_seconds": 60,
        },
        None,
    )
    run = done(
        service,
        service.start_run(
            {"task": "Current exact task", "agent_id": "alpha", "session_id": first["session_id"]}
        ),
    )
    assert run["status"] == "completed", run
    assert "Current exact task" in calls[-1].prompt and "Use supplied evidence" in calls[-1].prompt
    assert len(calls[-1].prompt) <= 1024 and run["prompt_budget"]["truncated"]
    duplicate = {
        "task": "Current exact task",
        "agent_id": "alpha",
        "session_id": first["session_id"],
    }
    with pytest.raises(ValueError, match="cooldown"):
        service.start_run(duplicate)
    other = StudioService(service.root)
    try:
        with pytest.raises(ValueError, match="cooldown"):
            other.start_run(duplicate)
        other.workbench.post(["controls", "reset"], {}, None)
        assert other.workbench.controls(None)["active_cooldowns"] == 0
    finally:
        other.close()
    # Rejection did not leave the conversation stuck in active_sessions.
    assert first["session_id"] not in service.active_sessions


def test_analytics_counts_runs_but_does_not_expose_prompts_or_output(app):
    service, *_ = app
    done(service, service.start_run({"task": "private task sentinel"}))
    report = service.workbench.analytics(None, 24)
    assert report["run_count"] == 1 and report["statuses"]["completed"] == 1
    assert "private task sentinel" not in json.dumps(report)
    assert "Evidence from" not in json.dumps(report)
    assert all(
        "duration_seconds" in event for event in report["events"] if event["status"] == "completed"
    )
    with pytest.raises(ValueError):
        service.workbench.analytics(None, 0)


def test_restart_never_repeats_inflight_publishing_and_releases_validation(app):
    service, *_ = app
    first = campaign(service)
    second = campaign(service)
    for record, status in [(first, "publishing"), (second, "validating")]:
        record.update(status=status, active_run="interrupted")
        service.workbench.campaigns.write(record)
    from nexus.core.studio_campaigns import StudioCampaigns

    recovered = StudioCampaigns(service.workbench)
    assert recovered.record(first["id"], None)["status"] == "uncertain"
    assert recovered.record(second["id"], None)["status"] == "draft"
    assert recovered.record(second["id"], None)["validation"]["passed"] is False
    assert "active_run" not in recovered.record(first["id"], None)


def test_external_provider_errors_do_not_persist_credentials(app):
    service, *_ = app
    record = campaign(service)

    def broken(*args):
        raise RuntimeError("Authorization Bearer secret-never-persist")

    service.workbench.campaigns.factory = broken
    run = done(
        service,
        service.start_run(
            {
                "kind": "campaign",
                "campaign_id": record["id"],
                "revision": 1,
                "campaign_action": "validate",
            }
        ),
    )
    assert run["status"] == "failed"
    assert "secret-never-persist" not in json.dumps(run)
    assert "secret-never-persist" not in json.dumps(
        service.workbench.campaigns.record(record["id"], None)
    )


def test_unfinished_audio_is_removed_on_recovery_and_invalid_agent_lists_rejected(app):
    service, *_ = app
    path = service._path("audio-bytes", "abandoned", "wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"private audio")
    service._write(
        service._path("audio-inputs", "abandoned"),
        {"id": "abandoned", "format": "wav", "claimed": False},
    )
    service.workbench.jobs.cleanup_audio()
    assert not path.exists()
    assert service._read(service._path("audio-inputs", "abandoned"))["interrupted"] is True
    with pytest.raises(ValueError, match="distinct saved agents"):
        service.start_run(
            {"kind": "delegation", "strategy": "parallel", "agent_ids": [{}], "task": "No work"}
        )

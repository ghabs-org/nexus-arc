"""Private signed Slack attachments reuse pinned artifacts without dispatch replay."""

import json
import threading
from unittest.mock import patch

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_slack_files import ARTIFACT_LIMIT, download_url
from tests.test_studio_messaging import done, entry
from tests.test_studio_slack_threads import app as app
from tests.test_studio_slack_threads import receive
from tests.test_studio_team import team as team


def configure(studio):
    studio.catalog.save(
        "connectors",
        {
            "name": "Reader",
            "data": {
                "type": "slack",
                "channel": "room",
                "token_env": "SLACK_FILES_TOKEN",
                "enabled": True,
                "actions": ["status"],
            },
        },
    )
    record = studio.catalog.get("entrypoints", "incoming")
    return studio.catalog.save(
        "entrypoints",
        {
            **record,
            "data": {
                **record["data"],
                "capture_slack_files": True,
                "notification_connector_id": "reader",
            },
        },
    )


def message(studio, number=1, **kw):
    return receive(
        studio,
        number,
        extra={
            "text": kw.pop("text", f"Question {number}"),
            "subtype": "file_share",
            "files": [{"id": "FFILE1", "url_private_download": "https://evil.invalid/leak"}],
        },
        **kw,
    )


def metadata(**kw):
    return {
        "ok": True,
        "file": {
            "id": "FFILE1",
            "name": "evidence.txt",
            "size": 8,
            "mode": "hosted",
            "is_external": False,
            "shares": {"private": {"room": [{"ts": "1700000001.000001"}]}},
            "url_private_download": "https://files.slack.com/files-pri/FFILE1/download/evidence.txt",
            **kw,
        },
    }


@pytest.fixture
def files(app, monkeypatch):
    studio, prompts = app
    configure(studio)
    monkeypatch.setenv("SLACK_FILES_TOKEN", "synthetic-slack-token")
    calls = []

    def read(url, token, limit, params=None):
        calls.append((url, token, limit, params))
        return json.dumps(metadata()).encode() if params else b"evidence"

    monkeypatch.setattr("nexus.core.studio_slack_files.read", read)
    return studio, prompts, calls


def test_signed_ids_only_and_capture_once_before_pinned_dispatch(files):
    studio, prompts, calls = files
    message(studio, text="")
    receipt = studio.records("channel-events")[0]
    assert receipt["status"] == "slack_files_pending" and not calls and not studio.records("runs")
    assert "evil.invalid" not in json.dumps(receipt)
    studio.messaging.tick()
    receipt = studio.records("channel-events")[0]
    assert receipt["status"] == "slack_files_ready" and len(calls) == 2
    artifact = studio.catalog.get("artifacts", receipt["slack_artifact_refs"][0]["id"])
    assert studio.artifacts.get(artifact) == b"evidence" and artifact["owner_id"] is None
    message(studio, text="")
    studio.messaging.tick()
    run = done(studio, studio.records("runs")[0])
    assert run["status"] == "completed" and run["artifact_refs"] == receipt["slack_artifact_refs"]
    assert run["task"] == "Inspect the attached files." and len(calls) == 2
    assert "evidence.txt" in prompts[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"shares": {}},
        {"is_external": True},
        {"mode": "external"},
        {"size": True},
        {"size": ARTIFACT_LIMIT + 1},
        {"name": "../secret"},
        {"url_private_download": "https://evil.invalid/leak"},
        {"id": "FFOREIGN"},
    ],
)
def test_invalid_metadata_never_saves_or_runs(files, changes, monkeypatch):
    studio, _, calls = files

    def read(*args, **kwargs):
        calls.append(args)
        return json.dumps(metadata(**changes)).encode()

    monkeypatch.setattr("nexus.core.studio_slack_files.read", read)
    message(studio)
    studio.messaging.tick()
    studio.messaging.tick()
    assert studio.records("channel-events")[0]["status"] == "failed"
    assert not studio.records("runs") and not studio.catalog.list("artifacts")
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["entrypoint", "connector", "mapping"])
def test_revocation_before_capture_does_not_resolve_account_or_fetch(files, change, monkeypatch):
    studio, _, calls = files
    message(studio)
    kind = "connectors" if change == "connector" else "entrypoints"
    identity = "reader" if kind == "connectors" else "incoming"
    record = studio.catalog.get(kind, identity)
    data = (
        {**record["data"], "enabled": False}
        if change != "mapping"
        else {**record["data"], "identity_map": {"bob": "desktop"}}
    )
    studio.catalog.save(kind, {**record, "data": data})
    studio.messaging.tick()
    assert (
        not calls
        and not studio.records("runs")
        and studio.records("channel-events")[0]["status"] == "failed"
    )


def test_download_configuration_change_is_rechecked_before_private_save(files, monkeypatch):
    studio, _, calls = files

    def read(url, token, limit, params=None):
        if params:
            return json.dumps(metadata()).encode()
        record = studio.catalog.get("connectors", "reader")
        studio.catalog.save("connectors", {**record, "data": {**record["data"], "enabled": False}})
        return b"evidence"

    monkeypatch.setattr("nexus.core.studio_slack_files.read", read)
    message(studio)
    studio.messaging.tick()
    assert (
        not studio.catalog.list("artifacts")
        and studio.records("channel-events")[0]["status"] == "failed"
    )


def test_attachment_revision_changes_fail_preflight_instead_of_using_new_bytes(files):
    studio, _, calls = files
    message(studio)
    studio.messaging.tick()
    record = studio.catalog.list("artifacts")[0]
    studio.catalog.save(
        "artifacts", {**record, "data": {"filename": "other.txt", "content_base64": "b3RoZXI="}}
    )
    studio.messaging.tick()
    assert not studio.records("runs") and studio.records("channel-events")[0]["status"] == "failed"


def test_pending_capture_orders_followups_and_does_not_hold_workspace_lock(files, monkeypatch):
    studio, prompts, calls = files
    entered = threading.Event()
    release = threading.Event()

    def read(url, token, limit, params=None):
        if params:
            return json.dumps(metadata()).encode()
        entered.set()
        assert release.wait(5)
        return b"evidence"

    monkeypatch.setattr("nexus.core.studio_slack_files.read", read)
    message(studio)
    worker = threading.Thread(target=studio.messaging.tick)
    worker.start()
    try:
        assert entered.wait(2)
        with studio.lock:
            receive(studio, 2, thread="1700000001.000001")
        studio.messaging.tick()
        assert not studio.records("runs")
    finally:
        release.set()
        worker.join(5)
    studio.messaging.tick()
    first = done(studio, studio.records("runs")[0])
    studio.messaging.tick()
    studio.messaging.tick()
    second = done(studio, next(r for r in studio.records("runs") if r["id"] != first["id"]))
    assert second["session_id"] == first["session_id"] and "Inspect" not in first["task"]


@pytest.mark.parametrize("value", [True, 1, "true", None])
def test_capture_requires_explicit_valid_thread_configuration(app, value):
    studio, _ = app
    record = studio.catalog.get("entrypoints", "incoming")
    with pytest.raises(ValueError):
        studio.catalog.save(
            "entrypoints",
            {
                **record,
                "data": {
                    **record["data"],
                    "threaded_conversations": False,
                    "capture_slack_files": value,
                },
            },
        )


def test_disabled_capture_preserves_legacy_ignore_behavior(app):
    studio, _ = app
    message(studio)
    assert not studio.records("channel-events")


@pytest.mark.parametrize(
    "url",
    [
        "http://files.slack.com/files-pri/F/a",
        "https://files.slack.com.evil.invalid/files-pri/F/a",
        "https://files.slack.com:443/files-pri/F/a",
        "https://user@files.slack.com/files-pri/F/a",
        "https://files.slack.com/files-pri/F/a#x",
        "https://files.slack.com/api/leak",
        "https://files.slack.com/files-pri/F/a\n",
    ],
)
def test_credentials_cannot_follow_untrusted_download_urls(url):
    with pytest.raises(ValueError):
        download_url(url)


@pytest.mark.parametrize("sql", [False, True])
def test_pending_and_captured_references_survive_restart(tmp_path, monkeypatch, sql):
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    monkeypatch.setenv("SLACK_FILES_TOKEN", "synthetic-slack-token")
    options = (
        {"url": "sqlite:///" + str(tmp_path / "state.sqlite"), "namespace": "files", "workers": 1}
        if sql
        else None
    )
    studio = StudioService(tmp_path / "workspace", state_options=options)
    studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    entry(studio, kind="slack", threaded_conversations=True)
    configure(studio)
    message(studio)
    studio.close()
    studio = StudioService(tmp_path / "workspace", state_options=options)
    try:
        monkeypatch.setattr(
            "nexus.core.studio_slack_files.read",
            lambda url, token, limit, params=None: (
                json.dumps(metadata()).encode() if params else b"evidence"
            ),
        )
        studio.messaging.tick()
        event = studio.records("channel-events")[0]
        assert event["status"] == "slack_files_ready"
        refs = event["slack_artifact_refs"]
        studio.close()
        studio = StudioService(tmp_path / "workspace", state_options=options)
        assert (
            studio.messaging.slack_files.context(studio.records("channel-events")[0])[
                "expected_artifact_refs"
            ]
            == refs
        )
    finally:
        studio.close()


def test_interrupted_capture_becomes_uncertain_without_relaunch(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    studio = StudioService(tmp_path)
    studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    entry(studio, kind="slack", threaded_conversations=True)
    configure(studio)
    message(studio)
    event = studio.records("channel-events")[0]
    event["status"] = "slack_files_fetching"
    studio._write(studio._path("channel-events", event["id"]), event)
    studio.close()
    studio = StudioService(tmp_path)
    try:
        with patch("nexus.core.studio_slack_files.read") as fetch:
            studio.messaging.tick()
            assert not fetch.called and studio.records("channel-events")[0]["status"] == "uncertain"
        assert not studio.records("runs")
    finally:
        studio.close()


def test_team_file_ownership_and_citation_downloads_are_not_shared(team, monkeypatch):
    studio, policy, _, request, _ = team
    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    policy.environment_resolver = lambda identity: (
        {"SLACK_FILES_TOKEN": identity + "-file-token"},
        None,
    )
    entry(
        studio,
        kind="slack",
        threaded_conversations=True,
        identity_map={"alice": "alice", "bob": "bob"},
    )
    configure(studio)
    tokens = []

    def read(url, token, limit, params=None):
        tokens.append(token)
        return json.dumps(metadata()).encode() if params else b"evidence"

    monkeypatch.setattr("nexus.core.studio_slack_files.read", read)
    message(studio)
    studio.messaging.tick()
    artifact = studio.catalog.list("artifacts")[0]
    assert artifact["owner_id"] == "alice" and tokens == ["alice-file-token"] * 2
    route = f"/api/catalog/artifacts/{artifact['id']}"
    assert request(route + "/download", user="bob")[0] == 403
    assert request(route + "/versions/1", user="bob")[0] == 403
    assert request(route + "/versions/1", user="alice")[0] == 200
    receive(studio, 2, thread="1700000001.000001")
    policy.bindings["alice"] = "viewer"
    studio.messaging.tick()
    assert not studio.records("runs")


def test_recovered_capture_claim_cannot_commit_late_download(files, monkeypatch):
    studio, _, _ = files

    def read(url, token, limit, params=None):
        if params:
            return json.dumps(metadata()).encode()
        event = studio.records("channel-events")[0]
        event["status"] = "uncertain"
        studio._write(studio._path("channel-events", event["id"]), event)
        return b"evidence"

    monkeypatch.setattr("nexus.core.studio_slack_files.read", read)
    message(studio)
    studio.messaging.tick()
    assert (
        not studio.catalog.list("artifacts")
        and studio.records("channel-events")[0]["status"] == "uncertain"
    )


@pytest.mark.parametrize("ids", [["FFILE1"] * 2, ["FFILE1"] * 6, ["not-a-file"], [None]])
def test_bad_signed_file_lists_fail_before_persistence(files, ids):
    studio, _, calls = files
    with pytest.raises(ValueError):
        receive(
            studio,
            1,
            extra={"subtype": "file_share", "files": [{"id": identity} for identity in ids]},
        )
    assert not calls and not studio.records("channel-events")


def test_combined_size_limit_prevents_partial_capture(files, monkeypatch):
    studio, _, calls = files

    def read(url, token, limit, params=None):
        calls.append(url)
        return (
            json.dumps(metadata(id=params["file"], size=ARTIFACT_LIMIT)).encode()
            if params
            else b"x" * ARTIFACT_LIMIT
        )

    monkeypatch.setattr("nexus.core.studio_slack_files.read", read)
    receive(
        studio,
        1,
        extra={
            "subtype": "file_share",
            "files": [{"id": identity} for identity in ["FFILE1", "FFILE2", "FFILE3"]],
        },
    )
    studio.messaging.tick()
    assert len(calls) == 5 and not studio.catalog.list("artifacts") and not studio.records("runs")
    assert studio.records("channel-events")[0]["status"] == "failed"


@pytest.mark.parametrize("sql", [False, True])
def test_stopped_restore_keeps_private_files_and_disables_gateway(tmp_path, monkeypatch, sql):
    from nexus.core.studio_backup import create_snapshot, restore_snapshot
    from nexus.core.studio_state import StudioSQLState

    monkeypatch.setenv("EVENT_SECRET", "s" * 32)
    monkeypatch.setenv("SLACK_FILES_TOKEN", "synthetic-slack-token")
    url = "sqlite:///" + str(tmp_path / "state.sqlite")
    studio = StudioService(
        tmp_path / "workspace",
        state_options={"url": url, "namespace": "source", "workers": 1} if sql else None,
    )
    try:
        studio.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
        entry(studio, kind="slack", threaded_conversations=True)
        configure(studio)
        monkeypatch.setattr(
            "nexus.core.studio_slack_files.read",
            lambda url, token, limit, params=None: (
                json.dumps(metadata()).encode() if params else b"evidence"
            ),
        )
        message(studio)
        studio.messaging.tick()
        event = studio.records("channel-events")[0]
        root, state = studio.root, studio.state
    finally:
        studio.close()
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
            state_options={"url": url, "namespace": "restored", "workers": 1} if sql else None,
        )
        try:
            ref = restored.records("channel-events")[0]["slack_artifact_refs"][0]
            assert ref == event["slack_artifact_refs"][0]
            assert (
                restored.artifacts.get(restored.artifacts.revision(ref["id"], ref["revision"]))
                == b"evidence"
            )
            assert not restored.catalog.get("entrypoints", "incoming")["data"]["enabled"]
            with patch("nexus.core.studio_slack_files.read") as fetch:
                restored.messaging.tick()
                assert not fetch.called and not restored.records("runs")
        finally:
            restored.close()
    finally:
        if source:
            source.engine.dispose()
        if target:
            target.engine.dispose()

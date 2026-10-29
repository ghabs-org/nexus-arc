"""Snapshot copies transfer explicit content, never source permissions or effects."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from nexus.core.studio_snapshot_continuation import continue_snapshot
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def fixture(studio, request):
    studio._write(
        studio._path("sessions", "source"),
        {
            "id": "source",
            "owner_id": "alice",
            "title": "Source",
            "provider": "claude",
            "agent_id": "SECRET_AGENT",
            "knowledge_project_id": "SECRET_PROJECT",
            "messages": [
                {"role": "user", "content": "Question", "run_id": "PRIVATE_RUN"},
                {"role": "assistant", "content": "Answer"},
            ],
        },
    )
    _, file = request(
        "/api/catalog/artifacts",
        {
            "name": "Original",
            "data": {
                "filename": "original.txt",
                "content_base64": base64.b64encode(b"OLD_REVISION").decode(),
            },
        },
        user="alice",
    )
    _, share = request(
        "/api/studio/shares",
        {"session_id": "source", "recipients": ["bob"], "artifact_ids": [file["id"]]},
        user="alice",
    )
    request("/api/catalog/artifacts", {"name": "Omitted", "data": {"filename": "omitted.txt", "content_base64": base64.b64encode(b"NEVER_SHARED").decode()}}, user="alice")
    return (
        file,
        share,
        {"snapshot": share["id"], "visibility": "private", "request_id": str(uuid.uuid4())},
    )


def test_private_copy_pins_file_bytes_and_is_idempotent_without_source_authority(team):
    studio, _, _, request, calls = team
    file, share, payload = fixture(studio, request)
    request(
        "/api/catalog/artifacts",
        {
            **file,
            "data": {
                "filename": "original.txt",
                "content_base64": base64.b64encode(b"NEW_REVISION").decode(),
            },
        },
        user="alice",
    )
    status, copied = request("/api/studio/shares/continue", payload, user="bob")
    assert status == 201, copied
    session = request("/api/sessions/" + copied["session_id"], user="bob")[1]
    assert session["owner_id"] == "bob" and len(session["messages"]) == 2
    assert session["agent_id"] is None and session["knowledge_project_id"] is None
    assert "PRIVATE_RUN" not in json.dumps(session) and "SECRET_AGENT" not in json.dumps(session)
    copy = studio.catalog.get("artifacts", copied["artifact_refs"][0]["id"])
    assert len(copied["artifact_refs"]) == 1
    assert copy["owner_id"] == "bob" and studio.artifacts.get(copy) == b"OLD_REVISION"
    assert request("/api/catalog/artifacts/" + file["id"], user="bob")[0] == 403
    assert request("/api/sessions/" + session["id"], user="alice")[0] == 403
    assert request("/api/studio/shares/continue", payload, user="bob")[1] == copied
    request(f"/api/studio/shares/{share['id']}/revoke", {}, user="alice")
    assert request("/api/studio/shares/continue", payload, user="bob")[0] == 404
    assert request("/api/sessions/" + session["id"], user="bob")[0] == 200
    assert (
        studio.artifacts.get(copy) == b"OLD_REVISION"
        and calls == []
        and studio.records("runs") == []
    )

    status, run = request("/api/runs", {"kind": "chat", "session_id": session["id"], "task": "Continue", "provider": "codex"}, user="bob")
    assert status == 202 and done(studio, run)["status"] == "completed"
    assert "Question" in calls[0].prompt and "Answer" in calls[0].prompt
    assert "OLD_REVISION" not in calls[0].prompt and "NEVER_SHARED" not in calls[0].prompt


def test_expiry_auth_csrf_and_unselected_files_cannot_transfer(team):
    studio, _, _, request, calls = team
    _, share, payload = fixture(studio, request)
    path = "/api/studio/shares/continue"
    assert request(path, payload)[0] == 401
    assert request(path, payload, user="viewer")[0] == 403
    assert request(path, payload, user="bob", csrf="wrong")[0] == 403
    assert request(path, {**payload, "recipients": ["viewer"]}, user="bob")[0] == 400
    assert request(path, {**payload, "request_id": "bad"}, user="bob")[0] == 400
    stored = studio._read(studio._path("conversation-shares", share["id"]))
    stored["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    studio._write(studio._path("conversation-shares", share["id"]), stored)
    assert request(path, payload, user="bob")[0] == 404
    assert len(studio.records("sessions")) == 1 and calls == []


def test_public_continuation_requires_login_and_never_stores_bearer_token(team):
    studio, _, _, request, calls = team
    _, _, _ = fixture(studio, request)
    request("/api/studio/sharing", {"enabled": True}, user="owner")
    _, share = request("/api/studio/shares", {"session_id": "source", "public": True}, user="alice")
    payload = {"snapshot": share["token"], "visibility": "public", "request_id": str(uuid.uuid4())}
    assert request("/api/studio/shares/continue", payload)[0] == 401
    status, result = request("/api/studio/shares/continue", payload, user="bob")
    assert status == 201, result
    session = studio.session(result["session_id"])
    assert share["token"] not in json.dumps(session)
    assert not any(key in session for key in ("public", "recipients", "token_hash", "run_id"))
    request("/api/studio/sharing", {"enabled": False}, user="owner")
    assert request("/api/studio/shares/continue", payload, user="bob")[0] == 404
    assert not calls


def test_interrupted_copy_retries_private_files_without_duplicate_session(team, monkeypatch):
    studio, _, _, request, _ = team
    _, _, payload = fixture(studio, request)
    actor = {"nexus_id": "bob", "role": "operator"}
    write = studio._write

    def fail_session(path, value):
        if path.parent.name == "sessions":
            raise OSError("Synthetic interruption")
        return write(path, value)

    monkeypatch.setattr(studio, "_write", fail_session)
    with pytest.raises(OSError):
        continue_snapshot(studio, payload, actor)
    monkeypatch.setattr(studio, "_write", write)
    copied = continue_snapshot(studio, payload, actor)
    assert continue_snapshot(studio, payload, actor) == copied
    assert (
        len([item for item in studio.catalog.list("artifacts") if item["owner_id"] == "bob"]) == 1
    )
    assert len(studio.records("sessions")) == 2


def test_oversized_messages_fail_before_creating_private_records(team):
    studio, _, _, request, _ = team
    _, share, payload = fixture(studio, request)
    record = studio._read(studio._path("conversation-shares", share["id"]))
    record["messages"] = [{"role": "user", "content": "x"}] * 2001
    studio._write(studio._path("conversation-shares", share["id"]), record)
    assert request("/api/studio/shares/continue", payload, user="bob")[0] == 400
    assert len(studio.records("sessions")) == 1
    assert not [item for item in studio.catalog.list("artifacts") if item["owner_id"] == "bob"]

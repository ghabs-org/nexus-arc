"""Conversation choices are canonical, private, bounded and recoverable."""

import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_nuclia import answer_sources, publish_answer
from nexus.core.studio_nuclia_attachments import COLLECTION
from nexus.core.studio_nuclia_documents import download_original
from tests.test_studio_nuclia import response
from tests.test_studio_nuclia_documents import source_run
from tests.test_studio_resources import done
from tests.test_studio_team import team as team


def metadata(page=1):
    return {
        "field_id": "discussion",
        "field_type": "conversation",
        "value": {
            "page": page,
            "pages": 2,
            "total": 2,
            "messages": [
                {
                    "ident": "message:one",
                    "content": {
                        "text": "UNRELATED_PRIVATE_MESSAGE",
                        "attachments": [
                            {
                                "filename": "policy.txt",
                                "uri": "https://untrusted.invalid/?token=EPHEMERAL_SECRET",
                                "size": 8,
                            }
                        ],
                        "attachments_fields": [
                            {"field_type": "file", "field_id": "supporting"},
                            {"field_type": "text", "field_id": "other"},
                        ],
                    },
                }
            ],
        },
    }


def conversation_source(studio, identity=None, **kwargs):
    record, selection = source_run(studio, identity, **kwargs)
    value = response()
    key = "r/c/discussion"
    value["citations"] = {key: [[0, 12]]}
    value["augmented_context"] = {
        "fields": {
            key: {
                "id": key,
                "text": "Conversation evidence",
                "augmentation_type": "conversation",
            }
        }
    }
    studio._update_run("source", status="running", citations=[])
    token = current_run_id.set("source")
    try:
        publish_answer(studio, answer_sources(value), identity, record["data"], record["id"])
    finally:
        current_run_id.reset(token)
    studio._update_run("source", status="completed")
    return record, selection


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-original-token")
    studio = StudioService(tmp_path)
    record, selection = conversation_source(studio)
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        if "?" in args[1]:
            return {"content": json.dumps(metadata()).encode(), "content_type": "application/json"}
        return {"content": b"Original attachment", "content_type": "text/plain"}

    studio.research.request = request
    try:
        yield studio, record, selection, calls
    finally:
        studio.close()


def listing(studio, record, selection, **kwargs):
    return download_original(
        studio, record["data"], None, {**selection, "operation": "list", **kwargs}, record["id"]
    )


def test_listing_saves_only_safe_choices_and_selected_files_use_canonical_routes(conversation):
    studio, record, selection, calls = conversation
    result = listing(studio, record, selection)
    assert result["next_page"] == 2 and result["next_offset"] is None
    assert len(result["attachments"]) == 2 and result["unsupported_references"] == 1
    assert (
        calls[0][0][1]
        == "https://example.org/api/v1/kb/selected/resource/r/conversation/discussion?show=value&page=1"
    )
    assert calls[0][1]["response_limit"] == 256 * 1024 and calls[0][1]["binary"] is True
    stored = str(studio.records(COLLECTION, limit=None)) + str(result)
    assert not any(
        value in stored
        for value in ("EPHEMERAL_SECRET", "untrusted.invalid", "UNRELATED_PRIVATE_MESSAGE")
    )
    for attachment, expected in zip(
        result["attachments"],
        (
            "/conversation/discussion/download/field/message%3Aone/0",
            "/file/supporting/download/field",
        ),
        strict=True,
    ):
        arguments = {**selection, "attachment_id": attachment["attachment_id"]}
        saved = download_original(studio, record["data"], None, arguments, record["id"])
        assert calls[-1][0][1].endswith(expected)
        assert saved["remote_revision_pinned"] is False
        assert (
            studio.artifacts.get(studio.artifacts.revision(saved["artifacts"][0]["id"], 1))
            == b"Original attachment"
        )
        cached = download_original(studio, record["data"], None, arguments, record["id"])
        assert cached["cached"] and cached["artifacts"] == saved["artifacts"]
    assert len(calls) == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"page": True},
        {"page": 0},
        {"page": 1001},
        {"offset": -1},
        {"offset": True},
        {"url": "https://evil.invalid"},
        {"message_id": "other"},
        {"attachment_index": 0},
    ],
)
def test_invalid_listing_selectors_do_not_contact_provider(conversation, changes):
    studio, record, selection, calls = conversation
    with pytest.raises(ValueError):
        listing(studio, record, selection, **changes)
    assert calls == [] and not studio.records(COLLECTION, limit=None)


@pytest.mark.parametrize(
    "change",
    [
        "field",
        "page",
        "pages",
        "duplicate",
        "message",
        "attachments",
        "refs",
        "json",
        "mime",
        "oversize",
    ],
)
def test_malformed_metadata_never_publishes_partial_choices(conversation, change):
    studio, record, selection, _ = conversation
    value = metadata()
    if change == "field":
        value["field_id"] = "other"
    elif change == "page":
        value["value"]["page"] = True
    elif change == "pages":
        value["value"]["pages"] = 1001
    elif change == "duplicate":
        value["value"]["messages"] *= 2
    elif change == "message":
        value["value"]["messages"][0]["ident"] = "../escape"
    elif change == "attachments":
        value["value"]["messages"][0]["content"]["attachments"] = {}
    elif change == "refs":
        value["value"]["messages"][0]["content"]["attachments_fields"][0]["field_id"] = "../escape"
    raw = (
        b"{"
        if change == "json"
        else b"x" * (256 * 1024 + 1)
        if change == "oversize"
        else json.dumps(value).encode()
    )
    studio.research.request = lambda *a, **k: {
        "content": raw,
        "content_type": "text/html" if change == "mime" else "application/json",
    }
    with pytest.raises(ValueError):
        listing(studio, record, selection)
    assert not studio.records(COLLECTION, limit=None)


def test_larger_attachment_pages_offer_explicit_offsets(conversation):
    studio, record, selection, _ = conversation
    value = metadata()
    value["value"]["total"] = 3
    value["value"]["messages"] = [
        {
            "ident": "message-" + str(i),
            "content": {"attachments": [{"filename": str(j)} for j in range(50)]},
        }
        for i in range(3)
    ]
    studio.research.request = lambda *a, **k: {
        "content": json.dumps(value).encode(),
        "content_type": "application/json",
    }
    first = listing(studio, record, selection)
    assert len(first["attachments"]) == 100 and first["next_offset"] == 100
    second = listing(studio, record, selection, offset=100)
    assert len(second["attachments"]) == 50 and second["next_offset"] is None
    assert {a["attachment_id"] for a in first["attachments"]}.isdisjoint(
        a["attachment_id"] for a in second["attachments"]
    )


def test_listing_cancelled_during_read_publishes_no_choices(conversation):
    studio, record, selection, _ = conversation
    studio._write(
        studio._path("runs", "list-job"),
        {
            "id": "list-job",
            "kind": "tool",
            "status": "running",
            "provider": "local-tool",
            "created_at": studio.run("source")["created_at"],
        },
    )

    def request(*a, **k):
        studio._update_run("list-job", cancel_requested=True)
        return {"content": json.dumps(metadata()).encode(), "content_type": "application/json"}

    studio.research.request = request
    token = current_run_id.set("list-job")
    try:
        with pytest.raises(ValueError, match="stopped"):
            listing(studio, record, selection)
    finally:
        current_run_id.reset(token)
    assert not studio.records(COLLECTION, limit=None)


def test_selections_and_captured_receipts_survive_restart(conversation):
    studio, record, selection, _ = conversation
    attachment = listing(studio, record, selection)["attachments"][0]
    arguments = {**selection, "attachment_id": attachment["attachment_id"]}
    saved = download_original(studio, record["data"], None, arguments, record["id"])
    studio.close()
    restored = StudioService(studio.root)
    try:
        restored.research.request = lambda *a, **k: pytest.fail(
            "Restart fetched a captured file again"
        )
        assert (
            download_original(restored, record["data"], None, arguments, record["id"])["artifacts"]
            == saved["artifacts"]
        )
    finally:
        restored.close()


def test_selected_target_changes_before_publication_are_rejected(conversation):
    studio, record, selection, _ = conversation
    attachment = listing(studio, record, selection)["attachments"][0]
    path = studio._path(COLLECTION, attachment["attachment_id"])

    def request(*a, **k):
        changed = studio._read(path)
        changed["target"]["index"] = 1
        studio._write(path, changed)
        return {"content": b"No publication", "content_type": "text/plain"}

    studio.research.request = request
    with pytest.raises(ValueError, match="changed"):
        download_original(
            studio,
            record["data"],
            None,
            {**selection, "attachment_id": attachment["attachment_id"]},
            record["id"],
        )
    assert not studio.records("source-originals", limit=None)


def test_shared_attachment_choices_and_files_stay_with_source_owner(team):
    studio, policy, _, request, _ = team
    _, selection = conversation_source(studio, "alice")
    calls = []
    studio.research.request = lambda *a, **k: (
        calls.append(a)
        or {"content": json.dumps(metadata()).encode(), "content_type": "application/json"}
    )
    route = "/api/studio/original-document"
    assert request(route, {**selection, "operation": "list"}, user="bob")[0] == 403
    assert not calls
    code, queued = request(route, {**selection, "operation": "list"}, user="alice")
    assert code == 202, queued
    run = done(studio, queued)
    assert run["status"] == "completed", run
    attachment = json.loads(run["output"])["attachments"][0]
    selected = {**selection, "attachment_id": attachment["attachment_id"]}
    assert request(route, selected, user="bob")[0] == 403
    policy.bindings["alice"] = "viewer"
    assert request(route, selected, user="alice")[0] == 403
    assert len(calls) == 1

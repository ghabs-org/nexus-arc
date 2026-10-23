"""Nuclia request bounds, citation integrity and late-publication fencing."""

import base64
from copy import deepcopy

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_enterprise import validate_enterprise
from nexus.core.studio_nuclia import answer_sources, publish_answer, request_body
from tests.test_studio_team import team as team


def response():
    return {
        "answer": "Thirty days.",
        "status": "success",
        "citations": {"r/f/file/0-12": [[0, 12]]},
        "retrieval_results": {
            "resources": {
                "r": {
                    "fields": {
                        "f/file": {
                            "paragraphs": {
                                "r/f/file/0-12": {
                                    "id": "r/f/file/0-12",
                                    "text": "Thirty days.",
                                    "position": {"page_number": 4},
                                },
                                "uncited": {"id": "uncited", "text": "Uncited text."},
                            }
                        }
                    }
                }
            }
        },
    }


@pytest.mark.parametrize(
    "collection,identity,kind",
    [
        ("paragraphs", "r/f/file/12-24", "neighbouring_paragraphs"),
        ("fields", "r/c/discussion", "conversation"),
    ],
)
def test_augmented_citations_keep_exact_text_and_provenance(collection, identity, kind):
    value = response()
    value["citations"] = {identity: [[0, 12]]}
    value["augmented_context"] = {
        collection: {
            identity: {
                "id": identity,
                "text": "Additional cited evidence.",
                "position": {"page_number": 7},
                "augmentation_type": kind,
            }
        }
    }
    parsed = answer_sources(value)
    assert len(parsed["sources"]) == 1
    source = parsed["sources"][0]
    assert source["text"] == "Additional cited evidence."
    assert source["augmentation_type"] == kind and source["original_page"] == 7
    assert source["field_id"] == "/".join(identity.split("/")[1:3])
    from nexus.core.studio_retrieval_evidence import evidence_document

    evidence = evidence_document(parsed, "Policy?", "knowledge", "run")
    assert evidence["contexts"] == [source["text"]]
    assert evidence["context_scope"] == "cited-text-blocks-only"


@pytest.mark.parametrize(
    "change",
    [
        {"id": "different"},
        {"position": {"page_number": -1}},
        {"augmentation_type": "unknown"},
        {"text": ""},
    ],
)
def test_malformed_augmented_citations_fail_before_publication(change):
    value = response()
    identity = "r/f/file/12-24"
    value["citations"] = {identity: [[0, 12]]}
    value["augmented_context"] = {
        "paragraphs": {
            identity: {
                "id": identity,
                "text": "Cited text",
                "augmentation_type": "neighbouring_paragraphs",
                **change,
            }
        }
    }
    with pytest.raises(ValueError):
        answer_sources(value)


@pytest.mark.parametrize(
    "change",
    [
        {"top_k": True},
        {"top_k": 0},
        {"top_k": 51},
        {"page": -1},
        {"page": True},
        {"page": 1},
        {"url": "https://evil.invalid"},
        {"filter_expression": []},
        {"filter_expression": {"operator": "and"}},
        {"filter_expression": {"field": []}},
        {"filter_expression": {"field": {}, "operator": "xor"}},
        {"filter_expression": {"field": {}, "token": "never-forward"}},
        {"filter_expression": {"field": {"value": "x" * 8192}}},
        {"prompt": "not-an-object"},
        {"prompt": {"user": ""}},
        {"prompt": {"secret_env": "NO"}},
        {"rephrase": "yes"},
    ],
)
def test_ask_request_rejects_invalid_or_unselected_arguments(change):
    with pytest.raises((ValueError, TypeError)):
        request_body("ask", {"query": "Policy?", **change})


def test_filters_prompts_rephrasing_and_search_compatibility():
    expression = {"field": {"prop": "resource", "id": "selected"}}
    assert request_body("query", {"query": "Policy", "page": 2}) == {
        "query": "Policy",
        "top_k": 10,
        "page_number": 2,
    }
    body = request_body(
        "ask",
        {
            "query": "Policy",
            "filter_expression": expression,
            "prompt": {"user": "Use this context: {context}. Question: {question}"},
            "rephrase": False,
        },
    )
    assert body["filter_expression"] == expression and body["citations"] == "default"
    assert body["rephrase"] is False
    with pytest.raises(ValueError):
        request_body("query", {"query": "Policy", "prompt": {"user": "ignored"}})
    nested = {"and": []}
    for _ in range(10):
        nested = {"and": [nested]}
    with pytest.raises(ValueError, match="nesting"):
        request_body("ask", {"query": "Policy", "filter_expression": {"field": nested}})
    config = {
        "type": "nuclia",
        "url": "https://example.org",
        "knowledge_box": "private",
        "token_env": "NUCLIA_TOKEN",
    }
    assert validate_enterprise(config)["actions"] == ["query"]
    assert validate_enterprise({**config, "actions": ["ask"]})["actions"] == ["ask"]


@pytest.mark.parametrize(
    "change",
    [
        {"status": "error", "error": "PRIVATE_DETAIL"},
        {"status": "unknown"},
        {"answer": None},
        {"answer": "x" * 64001},
        {"retrieval_results": {}},
        {"citations": []},
        {"citations": {"missing": []}},
        {"status": "no_context"},
    ],
)
def test_invalid_generation_fails_without_partial_evidence(change):
    with pytest.raises(ValueError) as error:
        answer_sources({**response(), **change})
    assert "PRIVATE_DETAIL" not in str(error.value)


def test_only_cited_paragraphs_become_evidence_and_no_context_is_explicit():
    result = answer_sources(response())
    assert len(result["sources"]) == 1
    assert result["sources"][0]["original_page"] == 4
    assert result["sources"][0]["text"] == "Thirty days."
    result = answer_sources(
        {
            "answer": "No source found.",
            "status": "no_context",
            "retrieval_results": {"resources": {}},
            "citations": {},
        }
    )
    assert result["status"] == "no_context" and result["sources"] == []


@pytest.mark.parametrize(
    "mode", ["cancel", "completed", "changed", "citation_limit", "artifact_limit"]
)
def test_late_result_never_publishes_after_execution_fence_changes(tmp_path, mode):
    studio = StudioService(tmp_path)
    token = None
    try:
        record = studio.catalog.save(
            "connectors",
            {
                "name": "Knowledge",
                "data": {
                    "type": "nuclia",
                    "url": "https://example.org",
                    "knowledge_box": "private",
                    "token_env": "NUCLIA_TOKEN",
                    "actions": ["ask"],
                    "enabled": True,
                },
            },
        )
        run = {
            "id": "nuclia-run",
            "status": "running",
            "owner_id": None,
            "kind": "tool",
            "provider": "local-tool",
            "created_at": "2026-10-21T12:00:00+00:00",
        }
        if mode == "cancel":
            run["cancel_requested"] = True
        if mode == "completed":
            run["status"] = "completed"
        if mode == "citation_limit":
            run["citations"] = [{}] * 40
        if mode == "artifact_limit":
            run["structured_content"] = [{}] * 40
        studio._write(studio._path("runs", run["id"]), run)
        token = current_run_id.set(run["id"])
        expected = deepcopy(record["data"])
        if mode == "changed":
            studio.catalog.save(
                "connectors", {**record, "data": {**record["data"], "enabled": False}}
            )
        with pytest.raises(ValueError):
            publish_answer(studio, answer_sources(response()), None, expected, record["id"])
        assert not studio.catalog.list("artifacts")
    finally:
        if token is not None:
            current_run_id.reset(token)
        studio.close()


def test_private_revision_pins_and_run_provenance(tmp_path):
    studio = StudioService(tmp_path)
    token = None
    try:
        studio._write(
            studio._path("runs", "nuclia-run"),
            {
                "id": "nuclia-run",
                "status": "running",
                "owner_id": None,
                "kind": "tool",
                "provider": "local-tool",
                "created_at": "2026-10-21T12:00:00+00:00",
            },
        )
        token = current_run_id.set("nuclia-run")
        result = publish_answer(
            studio, answer_sources(response()), None, {"knowledge_box": "private"}
        )
        run = studio.run("nuclia-run")
        assert len(result["artifacts"]) == len(run["structured_content"]) == 2
        citation = run["citations"][0]
        saved = studio.catalog.get("artifacts", citation["artifact_id"])
        assert saved["generated_by_run"] == "nuclia-run"
        assert b"Thirty days." in studio.artifacts.get(saved)
        assert result["sources"][0]["citation_ref"] == "[source:1]"
        assert citation["page"] == 1 and citation["original_page"] == 4
        studio.catalog.save(
            "artifacts",
            {
                **saved,
                "data": {
                    "filename": "replacement.txt",
                    "content_base64": base64.b64encode(b"Replacement").decode(),
                },
            },
        )
        pinned = studio.artifacts.revision(saved["id"], citation["artifact_revision"])
        assert b"Thirty days." in studio.artifacts.get(pinned)
    finally:
        if token is not None:
            current_run_id.reset(token)
        studio.close()


def test_account_revocation_blocks_source_publication(team):
    studio, policy, _, _, _ = team
    policy.bindings.pop("alice")
    with pytest.raises(PermissionError):
        publish_answer(studio, answer_sources(response()), "alice", {"knowledge_box": "private"})
    assert not studio.catalog.list("artifacts")


def test_source_snapshots_use_existing_private_download_authorization(team):
    studio, _, _, request, _ = team
    studio._write(
        studio._path("runs", "nuclia-private"),
        {
            "id": "nuclia-private",
            "status": "running",
            "owner_id": "alice",
            "requester_nexus_id": "alice",
            "kind": "tool",
            "provider": "local-tool",
            "created_at": "2026-10-21T12:00:00+00:00",
        },
    )
    token = current_run_id.set("nuclia-private")
    try:
        result = publish_answer(
            studio, answer_sources(response()), "alice", {"knowledge_box": "private"}
        )
    finally:
        current_run_id.reset(token)
    source = result["sources"][0]
    path = f"/api/catalog/artifacts/{source['artifact_id']}/preview?revision=1&page=1"
    assert request(path, user="alice")[0] == 200
    assert request(path, user="bob")[0] in {403, 404}
    assert request(path, user="viewer")[0] in {403, 404}
    assert request(path, user="owner")[0] == 200
    assert request(path)[0] in {401, 403}

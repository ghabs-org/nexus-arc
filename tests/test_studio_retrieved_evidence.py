"""Opt-in full-retrieval evidence keeps uncited text private and old captures readable."""

import base64
import copy
import hashlib
import json

import pytest

from nexus.core import studio_backup as backup
from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_enterprise import validate_enterprise
from nexus.core.studio_nuclia import answer_sources, publish_answer
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor
from nexus.core.studio_nuclia_interop import POLICY
from nexus.core.studio_retrieval_evidence import promotion_context
from nexus.core.studio_state import StudioSQLState
from tests.test_studio_nuclia import response
from tests.test_studio_nuclia_descriptors import descriptor, import_request
from tests.test_studio_retrieval_evidence import generated
from tests.test_studio_team import team as team

UNCITED = "PRIVATE_UNCITED_CONTEXT"


def full_response():
    value = response()
    value["retrieval_results"]["resources"]["r"]["fields"]["f/file"]["paragraphs"]["extra"] = {
        "id": "r/f/file/12-24",
        "text": UNCITED,
    }
    return value


def captured(studio, *, actor=None, capture=True, scope="retrieved", value=None):
    run = {
        "id": "full-retrieval",
        "kind": "tool",
        "status": "running",
        "provider": "local-tool",
        "owner_id": actor,
        "requester_nexus_id": actor,
        "created_at": "2026-10-25T12:00:00+01:00",
    }
    studio._write(studio._path("runs", run["id"]), run)
    token = current_run_id.set(run["id"])
    try:
        result = publish_answer(
            studio,
            answer_sources(value or full_response(), capture_retrieval=True),
            actor,
            {
                "knowledge_box": "private",
                "capture_evaluation": capture,
                "evaluation_context": scope,
            },
            question="Policy?",
        )
    finally:
        current_run_id.reset(token)
    studio._update_run(run["id"], status="completed")
    return run, result


def read(studio, reference):
    record = studio.artifacts.revision(reference["id"], reference["revision"])
    return json.loads(studio.artifacts.get(record))


def test_uncited_context_enters_only_explicit_private_v2_capture(tmp_path):
    studio = StudioService(tmp_path)
    try:
        run, result = captured(studio)
        document = read(studio, result["evaluation_evidence"])
        assert document["format"] == "nexus-retrieval-evidence/v2"
        assert document["context_scope"] == "retrieved-paragraphs-only"
        assert document["contexts"] == ["Thirty days.", "Uncited text.", UNCITED]
        assert [source["cited"] for source in document["sources"]] == [True, False, False]
        assert len(result["sources"]) == 1 and len(result["artifacts"]) == 3
        assert UNCITED not in json.dumps(result)
        assert UNCITED not in json.dumps(studio.run(run["id"]))
        assert UNCITED not in json.dumps(studio.records("execution-events", limit=None))
        assert not studio.records("invocation-traces", limit=None)
        # Selecting the legacy scope writes its original format even if the parser retained context.
        _, cited = captured(studio, scope="cited")
        old = read(studio, cited["evaluation_evidence"])
        assert old["format"] == "nexus-retrieval-evidence/v1"
        assert old["contexts"] == ["Thirty days."]
        _, disabled = captured(studio, capture=False)
        assert "evaluation_evidence" not in disabled and UNCITED not in json.dumps(disabled)
    finally:
        studio.close()


def test_retrieved_scope_excludes_augmented_only_blocks_without_hiding_citations(tmp_path):
    studio = StudioService(tmp_path)
    value = full_response()
    value["citations"] = {"r/a/title": [[0, 12]]}
    value["augmented_context"] = {
        "fields": {
            "r/a/title": {
                "id": "r/a/title",
                "text": "Augmented title",
                "augmentation_type": "hierarchy",
            }
        }
    }
    try:
        _, result = captured(studio, value=value)
        report = read(studio, result["evaluation_evidence"])
        assert "Augmented title" not in report["contexts"]
        assert not any(source["cited"] for source in report["sources"])
        assert result["sources"][0]["text"] == "Augmented title"
    finally:
        studio.close()


def test_missing_retrieval_never_falls_back_to_cited_context(tmp_path):
    studio = StudioService(tmp_path)
    try:
        with pytest.raises(ValueError, match="unavailable"):
            publish_answer(
                studio,
                answer_sources(response()),
                None,
                {
                    "knowledge_box": "private",
                    "capture_evaluation": True,
                    "evaluation_context": "retrieved",
                },
                question="Policy?",
            )
        assert not studio.catalog.list("artifacts")
    finally:
        studio.close()


@pytest.mark.parametrize("kind", ["count", "bytes", "identity", "field", "digest"])
def test_capture_bounds_and_identities_fail_before_publication(tmp_path, kind):
    studio = StudioService(tmp_path)
    parsed = answer_sources(full_response(), capture_retrieval=True)
    sources = parsed["_retrieved_sources"]
    if kind == "count":
        parsed["_retrieved_sources"] = [{**sources[0], "paragraph_id": str(i)} for i in range(21)]
    elif kind == "bytes":
        text = "x" * 16000
        parsed["_retrieved_sources"] = [
            {
                **sources[0],
                "paragraph_id": str(i),
                "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
            for i in range(5)
        ]
    else:
        sources[1].update(
            {"paragraph_id": "https://invalid.example/?token=PRIVATE_SECRET"}
            if kind == "identity"
            else {"field_id": "../PRIVATE_SECRET"}
            if kind == "field"
            else {"sha256": "PRIVATE_SECRET"}
        )
    try:
        with pytest.raises(ValueError) as error:
            publish_answer(
                studio,
                parsed,
                None,
                {
                    "knowledge_box": "private",
                    "capture_evaluation": True,
                    "evaluation_context": "retrieved",
                },
                question="Policy?",
            )
        assert "PRIVATE_SECRET" not in str(error.value)
        assert not studio.catalog.list("artifacts")
    finally:
        studio.close()


@pytest.mark.parametrize("scope", [None, True, "all", {}, []])
def test_invalid_scope_is_rejected_by_configuration(scope):
    with pytest.raises(ValueError, match="cited or retrieved"):
        validate_enterprise(
            {
                "type": "nuclia",
                "url": "https://example.org",
                "token_env": "KEY",
                "knowledge_box": "selected",
                "evaluation_context": scope,
            }
        )


def test_private_full_context_download_and_promotion_are_owner_scoped(team):
    studio, _, _, request, _ = team
    run, result = captured(studio, actor="alice")
    ref = result["evaluation_evidence"]
    selector = {"artifact_id": ref["id"], "revision": ref["revision"]}
    path = f"/api/catalog/artifacts/{ref['id']}/versions/{ref['revision']}"
    assert request(path, user="alice")[0] == 200
    assert request(path, user="bob")[0] in {403, 404}
    assert request(path)[0] in {401, 403}
    assert (
        UNCITED
        in promotion_context(studio, selector, run["id"], {"nexus_id": "alice", "role": "builder"})[
            "contexts"
        ]
    )
    with pytest.raises(PermissionError):
        promotion_context(studio, selector, run["id"], {"nexus_id": "bob", "role": "builder"})


@pytest.mark.parametrize("mutation", ["scope", "alignment", "marker", "digest", "format"])
def test_v2_reader_rejects_bad_scope_alignment_or_source_metadata(tmp_path, mutation):
    studio = StudioService(tmp_path)
    try:
        run, result = captured(studio)
        ref = result["evaluation_evidence"]
        document = read(studio, ref)
        if mutation == "scope":
            document["context_scope"] = "cited-paragraphs-only"
        elif mutation == "alignment":
            document["contexts"].pop()
        elif mutation == "marker":
            document["sources"][0]["cited"] = 1
        elif mutation == "format":
            document["format"] = {"invalid": True}
        else:
            document["sources"][0]["sha256"] = "invalid"
        updated = studio.catalog.save(
            "artifacts",
            {
                **studio.catalog.get("artifacts", ref["id"]),
                "data": {
                    "filename": "retrieval-evidence.json",
                    "content_base64": base64.b64encode(json.dumps(document).encode()).decode(),
                },
            },
        )
        # Simulate a new writer producing a malformed report with valid server provenance.
        studio._write(
            studio.catalog._path("artifacts", updated["id"]),
            {**updated, "generated_by_run": run["id"]},
        )
        with pytest.raises(ValueError):
            promotion_context(
                studio,
                {"artifact_id": updated["id"], "revision": updated["revision"]},
                run["id"],
                None,
            )
        assert (
            UNCITED
            in promotion_context(
                studio, {"artifact_id": ref["id"], "revision": ref["revision"]}, run["id"], None
            )["contexts"]
        )
    finally:
        studio.close()


@pytest.mark.parametrize("sql", [False, True])
def test_v1_and_v2_survive_stopped_backup_restore_and_promotion(tmp_path, sql):
    root = tmp_path / "source"
    url = f"sqlite:///{tmp_path / 'state.sqlite'}"
    options = {"url": url, "namespace": "source"} if sql else None
    studio = StudioService(root, state_options=options)
    try:
        old_run, old = generated(studio)
        new_run, new = captured(studio)
    finally:
        studio.close()
    source_state = StudioSQLState(url, "source") if sql else None
    target_state = StudioSQLState(url, "restored") if sql else None
    try:
        backup.create_snapshot(root, tmp_path / "backup", sql_state=source_state, quiesced=True)
        restored = backup.restore_snapshot(
            tmp_path / "backup", tmp_path / "restored", sql_state=target_state, quiesced=True
        )
        studio = StudioService(
            restored["root"],
            state_dir=restored["state_dir"],
            state_options={"url": url, "namespace": "restored"} if sql else None,
        )
        try:
            for run, result, expected in (
                (old_run, old, ["Thirty days."]),
                (new_run, new, ["Thirty days.", "Uncited text.", UNCITED]),
            ):
                ref = result["evaluation_evidence"]
                context = promotion_context(
                    studio, {"artifact_id": ref["id"], "revision": ref["revision"]}, run["id"], None
                )
                assert context["contexts"] == expected
            assert not studio.records("invocation-traces", limit=None)
        finally:
            studio.close()
    finally:
        for state in (source_state, target_state):
            if state:
                state.engine.dispose()


def evaluation_source():
    source = descriptor()
    source["tool_config"]["remi_publish_topic"] = (
        "evaluation/{interaction_id}/{tool_name}/{learning_id}"
    )
    return source


@pytest.mark.parametrize(
    "topic",
    [
        "{identity.token}",
        "{tool_name!r}",
        "{tool_name:20}",
        "{",
        "PRIVATE_SECRET\n",
        "x" * 1001,
        {},
        1,
    ],
)
def test_topic_adaptation_rejects_unsafe_or_unknown_expressions_without_echo(topic):
    source = evaluation_source()
    source["tool_config"]["remi_publish_topic"] = topic
    with pytest.raises(ValueError) as error:
        convert_tool_descriptor(source, policy=POLICY, preserve_evaluation=True)
    assert "PRIVATE_SECRET" not in str(error.value)


def test_evaluation_descriptor_review_and_native_source_roundtrip(tmp_path):
    source = evaluation_source()
    before = copy.deepcopy(source)
    with pytest.raises(ValueError):
        convert_tool_descriptor(source, policy=POLICY)
    converted = convert_tool_descriptor(source, policy=POLICY, preserve_evaluation=True)
    assert source == before
    assert converted["connector"]["capture_evaluation"] is True
    assert converted["connector"]["evaluation_context"] == "retrieved"
    assert converted["connector"]["enabled"] is False
    assert "including uncited paragraphs" in json.dumps(converted["adaptations"])
    studio = StudioService(tmp_path)
    try:
        studio.catalog.save(
            "connectors", {"id": "knowledge", "name": "Knowledge", "data": converted["connector"]}
        )
        request = import_request(source)
        request["bindings"]["nuclia"]["CompanyKnowledge"]["preserve_evaluation"] = True
        report = studio.configuration.convert_interop(request, None)
        assert report["valid"], report
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        exported = studio.configuration.export_interop({"document": report["document"]}, None)
        assert exported["valid"]
        restored = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
        assert restored["valid"] and restored["document"] == report["document"]
    finally:
        studio.close()


@pytest.mark.parametrize("topic", [None, ""])
def test_no_topic_enables_no_extra_capture(topic):
    source = evaluation_source()
    source["tool_config"]["remi_publish_topic"] = topic
    assert (
        "capture_evaluation"
        not in convert_tool_descriptor(source, policy=POLICY, preserve_evaluation=True)["connector"]
    )

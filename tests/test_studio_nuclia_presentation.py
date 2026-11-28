"""Presentation preserves private provenance, output compatibility and publication fences."""

import copy
import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_nuclia import answer_sources, publish_answer, request_body
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor
from nexus.core.studio_nuclia_interop import POLICY
from nexus.core.studio_nuclia_presentation import validate_answer_options
from tests.test_studio_nuclia import response
from tests.test_studio_nuclia_descriptors import descriptor, import_request


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path / "workspace")
    try:
        yield service
    finally:
        service.close()


def content(studio, artifact):
    return studio.artifacts.get(
        studio.artifacts.revision(artifact["id"], artifact["revision"])
    ).decode()


@pytest.mark.parametrize("artifact", [False, True])
@pytest.mark.parametrize("links", [False, True])
@pytest.mark.parametrize("include", [False, True])
def test_answer_modes_preserve_plain_output_and_revision_links(studio, artifact, links, include):
    result = publish_answer(
        studio,
        answer_sources(response()),
        None,
        {
            "knowledge_box": "private",
            "answer_options": {
                "output_response_as_artifact": artifact,
                "inline_citation_links": links,
                "include_citations_in_tool_response": include,
                "output_filename_base": "policy-notes.md",
                "artifact_description_query_max_length": 6,
            },
        },
        question="Return policy?",
    )
    assert result["output"] == "Thirty days."
    assert len(result["artifacts"]) == (2 if artifact else 1)
    assert ("response_artifact" in result) is artifact
    assert ("response_content" in result) is not artifact
    assert ("citations" in result) is include
    source = result["sources"][0]
    pinned = (
        f"/api/catalog/artifacts/{source['artifact_id']}/versions/{source['artifact_revision']}"
    )
    body = content(studio, result["response_artifact"]) if artifact else result["response_content"]
    assert "**Sources:**" in body and pinned in body and "source page 4" in body
    assert (f"[[1]]({pinned})" in body) is links
    assert " [1]" in body if not links else " [[1]]" in body
    assert "Bearer" not in body and "http" not in body
    if artifact:
        saved = studio.catalog.get("artifacts", result["response_artifact"]["id"])
        assert saved["data"]["filename"] == "policy-notes.md"
        assert saved["description"] == "Nuclia answer for: Return"
    if include:
        assert result["citations"][0]["url"] == pinned
        assert result["citations"][0]["artifact_revision"] == source["artifact_revision"]


def test_evidence_reference_remains_correct_after_answer_file(studio):
    result = publish_answer(
        studio,
        answer_sources(response()),
        None,
        {
            "knowledge_box": "private",
            "capture_evaluation": True,
            "answer_options": {},
        },
        question="Policy?",
    )
    assert len(result["artifacts"]) == 3
    assert result["artifacts"][-1] == result["response_artifact"]
    evidence = json.loads(content(studio, result["evaluation_evidence"]))
    assert evidence["format"] == "nexus-retrieval-evidence/v1"
    assert evidence["answer"] == "Thirty days."
    assert evidence["contexts"] == ["Thirty days."]


@pytest.mark.parametrize("mode", ["default", "none", "llm_footnotes"])
def test_citation_modes_are_fixed_and_search_rejects_them(mode):
    assert request_body("ask", {"query": "Policy?", "citations": mode})["citations"] == mode
    with pytest.raises(ValueError, match="cannot be overridden"):
        request_body(
            "ask",
            {"query": "Policy?", "citations": "none" if mode != "none" else "default"},
            ask_settings={"citations": mode},
        )
    with pytest.raises(ValueError, match="Unsupported"):
        request_body("query", {"query": "Policy?", "citations": mode})


@pytest.mark.parametrize("mode", [True, None, [], {}, "PRIVATE_VALUE"])
def test_invalid_citation_modes_reject_without_echo(mode):
    with pytest.raises(ValueError) as error:
        request_body("ask", {"query": "Policy?", "citations": mode})
    assert "PRIVATE_VALUE" not in str(error.value)


def footnoted():
    value = response()
    value["answer"] = "Thirty days.[^policy]"
    value["citations"] = {}
    value["citation_footnote_to_context"] = {
        "policy": "r/f/file/0-12",
        "uncited": "PROVIDER_URL_NOT_PERSISTED",
    }
    return value


def test_footnotes_pin_only_referenced_blocks_and_render_private_links(studio):
    parsed = answer_sources(footnoted(), citation_mode="llm_footnotes")
    assert len(parsed["sources"]) == 1
    assert parsed["footnote_sources"] == {"policy": "r/f/file/0-12"}
    assert "PROVIDER_URL_NOT_PERSISTED" not in json.dumps(parsed)
    result = publish_answer(
        studio,
        parsed,
        None,
        {"knowledge_box": "private", "answer_options": {"output_response_as_artifact": False}},
    )
    assert result["output"] == "Thirty days.[^policy]"
    assert "[^policy]" not in result["response_content"]
    assert " [[1]](/api/catalog/artifacts/" in result["response_content"]


@pytest.mark.parametrize(
    "mapping",
    [
        None,
        [],
        {"policy": None},
        {"policy": "missing"},
        {"policy": "https://untrusted.invalid/?token=SECRET"},
        {"bad.key": "r/f/file/0-12"},
        {},
    ],
)
def test_bad_footnote_mappings_reject_without_publication(studio, mapping):
    value = footnoted()
    value["citation_footnote_to_context"] = mapping
    with pytest.raises(ValueError) as error:
        answer_sources(value, citation_mode="llm_footnotes")
    assert "SECRET" not in str(error.value)
    assert not studio.catalog.list("artifacts")


def test_none_mode_does_not_snapshot_uncited_retrieval(studio):
    value = response()
    with pytest.raises(ValueError, match="disabled"):
        answer_sources(value, citation_mode="none")
    value["citations"] = {}
    parsed = answer_sources(value, citation_mode="none")
    assert not parsed["sources"]
    result = publish_answer(
        studio,
        parsed,
        None,
        {"knowledge_box": "private", "answer_options": {"output_response_as_artifact": False}},
    )
    assert result["response_content"] == "Thirty days."
    assert result["artifacts"] == []


@pytest.mark.parametrize(
    "positions",
    [None, "PRIVATE_VALUE", [[0]], [[True, 2]], [[2, 1]], [[0, 13]], [[-1, 2]], [[0, 1]] * 101],
)
def test_bad_positions_fail_before_any_file_is_saved(studio, positions):
    value = response()
    value["citations"]["r/f/file/0-12"] = positions
    with pytest.raises(ValueError, match="positions"):
        publish_answer(
            studio, answer_sources(value), None, {"knowledge_box": "private", "answer_options": {}}
        )
    assert not studio.catalog.list("artifacts")


@pytest.mark.parametrize("artifact", [False, True])
def test_run_capacity_counts_the_selected_answer_mode(studio, artifact):
    studio._write(
        studio._path("runs", "run"),
        {
            "id": "run",
            "status": "running",
            "owner_id": None,
            "kind": "tool",
            "provider": "local-tool",
            "created_at": "2026-10-25T00:00:00+00:00",
            "structured_content": [{}] * 39,
        },
    )
    token = current_run_id.set("run")
    try:
        if artifact:
            with pytest.raises(ValueError, match="artifact limit"):
                publish_answer(
                    studio,
                    answer_sources(response()),
                    None,
                    {"knowledge_box": "private", "answer_options": {}},
                )
            assert not studio.catalog.list("artifacts")
        else:
            result = publish_answer(
                studio,
                answer_sources(response()),
                None,
                {
                    "knowledge_box": "private",
                    "answer_options": {"output_response_as_artifact": False},
                },
            )
            assert len(result["artifacts"]) == 1
            assert len(studio.run("run")["structured_content"]) == 40
            assert len(studio.run("run")["citations"]) == 1
    finally:
        current_run_id.reset(token)


@pytest.mark.parametrize(
    "options",
    [
        None,
        {"unknown": "PRIVATE_VALUE"},
        {"output_response_as_artifact": 1},
        {"inline_citation_links": "yes"},
        {"include_citations_in_tool_response": None},
        {"output_filename_base": "../PRIVATE_VALUE"},
        {"output_filename_base": "CON.md"},
        {"output_filename_base": "bad\nname"},
        {"output_filename_base": "x" * 101},
        {"artifact_description_query_max_length": True},
        {"artifact_description_query_max_length": 0},
        {"artifact_description_query_max_length": 1001},
    ],
)
def test_bad_options_reject_without_echo(options):
    with pytest.raises(ValueError) as error:
        validate_answer_options(options)
    assert "PRIVATE_VALUE" not in str(error.value)


@pytest.mark.parametrize("sql", [False, True])
def test_presentation_import_export_restart_and_legacy_rejection(tmp_path, sql):
    state = (
        {"url": f"sqlite:///{tmp_path / 'state.sqlite'}", "namespace": "presentation"}
        if sql
        else None
    )
    studio = StudioService(tmp_path / "workspace", state_options=state)
    source = descriptor()
    source["tool_config"].update(
        output_response_as_artifact=False,
        output_filename_base="policy.md",
        artifact_description_query_max_length=10,
        inline_citation_links=False,
        include_citations_in_tool_response=True,
        citations="llm_footnotes",
    )
    request = import_request(source)
    request["bindings"]["nuclia"]["CompanyKnowledge"]["preserve_presentation"] = True
    before = copy.deepcopy(source)
    try:
        with pytest.raises(ValueError):
            convert_tool_descriptor(source, policy=POLICY)
        converted = convert_tool_descriptor(source, policy=POLICY, preserve_presentation=True)
        assert source == before
        studio.catalog.save(
            "connectors", {"id": "knowledge", "name": "Knowledge", "data": converted["connector"]}
        )
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
    studio = StudioService(tmp_path / "workspace", state_options=state)
    try:
        saved = studio.catalog.get("connectors", "knowledge")["data"]
        assert saved["answer_options"]["output_filename_base"] == "policy"
        assert saved["answer_options"]["output_response_as_artifact"] is False
        assert saved["ask_settings"]["citations"] == "llm_footnotes"
        assert saved["enabled"] is False
        assert studio.configuration.convert_interop(request, None)["valid"]
    finally:
        studio.close()

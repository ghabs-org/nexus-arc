"""Per-invocation answer names stay local and never change reviewed configuration."""

import asyncio
import copy
import json

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_nuclia import response


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("FILENAME_FIXTURE_TOKEN", "synthetic-filename-key")
    studio = StudioService(tmp_path)
    requests = []

    def request(method, url, headers, body):
        requests.append(copy.deepcopy(body))
        return response()

    studio.connections.enterprise.request_client = request
    try:
        yield studio, requests
    finally:
        studio.close()


def connector(studio, **options):
    return studio.catalog.save(
        "connectors",
        {
            "name": "Knowledge",
            "data": {
                "type": "nuclia",
                "url": "https://example.org",
                "knowledge_box": "selected",
                "token_env": "FILENAME_FIXTURE_TOKEN",
                "actions": ["query", "ask"],
                "enabled": True,
                **options,
            },
        },
    )


def call(studio, **arguments):
    return asyncio.run(
        studio.connections.execute("knowledge", {"action": "ask", "arguments": arguments})
    )


@pytest.mark.parametrize("options", [{}, {"answer_options": {"output_filename_base": "saved"}}])
@pytest.mark.parametrize("name", ["meeting-notes.md", "quarterly.MD"])
def test_runtime_name_changes_only_one_private_answer(service, options, name):
    studio, requests = service
    saved = connector(studio, **options)
    result = call(studio, query="Policy?", output_filename_base=name)
    assert result["artifacts"][-1]["filename"] == name[:-3] + ".md"
    assert result["output"] == "Thirty days."
    source = result["sources"][0]
    record = studio.artifacts.revision(source["artifact_id"], source["artifact_revision"])
    assert studio.artifacts.get(record).decode().endswith("Thirty days.")
    assert requests == [{"query": "Policy?", "top_k": 10, "citations": "default"}]
    assert studio.catalog.get("connectors", "knowledge") == saved
    answer = result["artifacts"][-1]
    content = studio.artifacts.get(
        studio.artifacts.revision(answer["id"], answer["revision"])
    ).decode()
    if not options:
        assert content == "Nuclia answer status: success\n\nThirty days."
    else:
        assert "**Sources:**" in content
    restored = call(studio, query="Policy?")
    assert restored["artifacts"][-1]["filename"] == ("saved.md" if options else "nuclia-answer.md")
    assert restored["artifacts"][-1]["id"] != result["artifacts"][-1]["id"]


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../escape",
        "/tmp/escape",
        "https://private.invalid",
        "a/b",
        "a\\b",
        "a\nname",
        "a\x00name",
        "CON",
        "NUL.txt",
        "LPT1",
        "a..b",
        "a.",
        ".md",
        "x" * 101,
        True,
        7,
        {},
        [],
    ],
)
def test_invalid_runtime_names_fail_before_provider_or_artifact_writes(service, name):
    studio, requests = service
    saved = connector(studio, answer_options={})
    with pytest.raises(ValueError, match="Integration action failed"):
        call(studio, query="Policy?", output_filename_base=name)
    assert requests == [] and studio.catalog.list("artifacts") == []
    assert studio.catalog.get("connectors", "knowledge") == saved


@pytest.mark.parametrize(
    "options",
    [{}, {"answer_options": {}}, {"answer_options": {"output_response_as_artifact": False}}],
)
def test_nullable_name_retains_default_output(service, options):
    studio, requests = service
    connector(studio, **options)
    result = call(studio, query="Policy?", output_filename_base=None)
    assert "output_filename_base" not in requests[0]
    assert len(result["artifacts"]) == (
        1 if options.get("answer_options", {}).get("output_response_as_artifact") is False else 2
    )


@pytest.mark.parametrize("action", ["ask", "query"])
def test_filename_cannot_enable_inline_file_or_modify_search(service, action):
    studio, requests = service
    connector(studio, answer_options={"output_response_as_artifact": False})
    with pytest.raises(ValueError):
        asyncio.run(
            studio.connections.execute(
                "knowledge",
                {
                    "action": action,
                    "arguments": {"query": "Policy?", "output_filename_base": "notes"},
                },
            )
        )
    assert not requests and not studio.catalog.list("artifacts")


def test_local_name_and_declared_parameter_remain_separate(service):
    studio, requests = service
    connector(
        studio,
        template={
            "version": 1,
            "parameters": [{"name": "output_filename_base", "type": "string", "required": True}],
            "query_prompt": "{{output_filename_base}}: {{query}}",
        },
    )
    result = call(
        studio,
        query="Policy?",
        parameters={"output_filename_base": "Template data"},
        output_filename_base="private-answer",
    )
    assert requests[0]["query"] == "Template data: Policy?"
    assert "output_filename_base" not in requests[0]
    assert result["artifacts"][-1]["filename"] == "private-answer.md"


@pytest.mark.parametrize(
    "inline,actions,advertised",
    [(False, ["ask"], True), (True, ["ask"], False), (False, ["query"], False)],
)
def test_agent_description_advertises_only_available_filename_argument(
    service, inline, actions, advertised
):
    from nexus.tools import tool_registry

    studio, _ = service
    connector(studio, answer_options={"output_response_as_artifact": not inline}, actions=actions)
    name = next(name for name in studio.operations.tools if name.endswith(":knowledge"))
    assert ("arguments.output_filename_base" in tool_registry.get(name).description) is advertised
    assert "FILENAME_FIXTURE_TOKEN" not in tool_registry.get(name).description


def test_connection_change_after_request_blocks_custom_filename_publication(service):
    studio, requests = service
    saved = connector(studio, answer_options={})

    def changed(*args):
        requests.append(args[3])
        studio.catalog.save("connectors", {**saved, "data": {**saved["data"], "enabled": False}})
        return response()

    studio.connections.enterprise.request_client = changed
    with pytest.raises(ValueError):
        call(studio, query="Policy?", output_filename_base="stopped-answer")
    assert len(requests) == 1 and not studio.catalog.list("artifacts")


def test_custom_name_preserves_evidence_reference_and_configured_default(service):
    studio, _ = service
    connector(studio, answer_options={}, capture_evaluation=True, evaluation_context="retrieved")
    result = call(studio, query="Policy?", output_filename_base="evidence-answer")
    assert result["response_artifact"]["filename"] == "evidence-answer.md"
    file = result["evaluation_evidence"]
    content = studio.artifacts.get(studio.artifacts.revision(file["id"], file["revision"]))
    evidence = json.loads(content)
    assert evidence["format"] == "nexus-retrieval-evidence/v2"
    assert evidence["contexts"] == ["Thirty days.", "Uncited text."]

"""Explicit Nuclia answers with bounded filters and private source snapshots."""

import base64
import hashlib
import json
import re
from datetime import UTC, datetime

from nexus.core.studio_catalog import _text
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_nuclia_presentation import (
    footnote_citations,
    prepare_segments,
    presented_answer,
    validate_answer_options,
    validate_citation_mode,
)
from nexus.core.studio_nuclia_retrieval import validate_rag_strategies


def request_body(action, arguments, template=None, *, context=None, ask_settings=None):
    if "audit_metadata" in arguments:
        raise ValueError("Runtime metadata cannot override reviewed Nuclia audit templates")
    if action == "ask" and "rag_strategies" in arguments:
        arguments = {
            **arguments,
            "rag_strategies": validate_rag_strategies(arguments["rag_strategies"]),
        }
    if action == "ask" and ask_settings is not None:
        if "rag_strategies" in ask_settings:
            ask_settings = {
                **ask_settings,
                "rag_strategies": validate_rag_strategies(ask_settings["rag_strategies"]),
            }
        for key, value in ask_settings.items():
            if key in arguments and arguments[key] != value:
                raise ValueError("Configured Nuclia ask settings cannot be overridden")
        arguments = {**ask_settings, **arguments}
    if template is not None:
        from nexus.core.studio_nuclia_templates import prepare_arguments

        arguments = prepare_arguments(template, arguments, context=context)
    allowed = {"query", "top_k", "page", "filter_expression", "audit_metadata"}
    if action == "ask":
        allowed |= {"prompt", "rephrase", "prefer_markdown", "rag_strategies", "citations"}
    if set(arguments) - allowed:
        raise ValueError(
            "Unsupported Nuclia arguments: " + ", ".join(sorted(set(arguments) - allowed))
        )
    limit, page = arguments.get("top_k", 10), arguments.get("page", 0)
    if (
        type(limit) is not int
        or not 1 <= limit <= 50
        or type(page) is not int
        or not 0 <= page <= 100
    ):
        raise ValueError("Nuclia top_k must be 1–50 and page 0–100")
    if action == "ask" and page:
        raise ValueError("Nuclia answer generation does not accept search pagination")
    body = {"query": _text(arguments.get("query"), "Search query", 4000), "top_k": limit}
    if "audit_metadata" in arguments:
        from nexus.core.studio_nuclia_templates import validate_audit_values

        body["audit_metadata"] = validate_audit_values(arguments["audit_metadata"])
    if page:
        body["page_number"] = page
    if "filter_expression" in arguments:
        expression = arguments["filter_expression"]
        if (
            not isinstance(expression, dict)
            or not expression
            or set(expression) - {"field", "paragraph", "operator"}
        ):
            raise ValueError("Nuclia filter expression uses field, paragraph and optional operator")
        if not any(key in expression for key in ("field", "paragraph")) or expression.get(
            "operator", "and"
        ) not in {"and", "or"}:
            raise ValueError(
                "Nuclia filters require a field or paragraph expression and and/or operator"
            )

        def bounded(value, depth=0):
            if depth > 8:
                raise ValueError("Nuclia filter exceeds nesting limit")
            if isinstance(value, dict):
                for item in value.values():
                    bounded(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    bounded(item, depth + 1)

        if any(
            not isinstance(expression[key], dict)
            for key in ("field", "paragraph")
            if key in expression
        ):
            raise ValueError("Nuclia field and paragraph filters must be objects")
        bounded(expression)
        if len(json.dumps(expression, allow_nan=False).encode()) > 8192:
            raise ValueError("Nuclia filter exceeds 8 KiB")
        body["filter_expression"] = expression
    if action == "ask":
        body["citations"] = validate_citation_mode(arguments.get("citations", "default"))
        if "rag_strategies" in arguments:
            body["rag_strategies"] = validate_rag_strategies(arguments["rag_strategies"])
        if "prompt" in arguments:
            prompt = arguments["prompt"]
            if not isinstance(prompt, dict) or not prompt or set(prompt) - {"user", "system"}:
                raise ValueError("Nuclia prompt uses explicit user/system text")
            body["prompt"] = {
                key: _text(value, "Nuclia prompt", 4000) for key, value in prompt.items()
            }
        if "rephrase" in arguments:
            if type(arguments["rephrase"]) is not bool:
                raise ValueError("Nuclia rephrase must be true or false")
            body["rephrase"] = arguments["rephrase"]
        if "prefer_markdown" in arguments:
            if type(arguments["prefer_markdown"]) is not bool:
                raise ValueError("Nuclia prefer_markdown must be true or false")
            body["prefer_markdown"] = arguments["prefer_markdown"]
    return body


def answer_sources(value, *, citation_mode="default", capture_retrieval=False):
    citation_mode = validate_citation_mode(citation_mode)
    if (
        not isinstance(value, dict)
        or value.get("status") not in {"success", "no_context", "no_retrieval_data"}
        or value.get("error")
    ):
        raise ValueError("Nuclia answer generation failed or returned an unknown status")
    answer = value.get("answer")
    if not isinstance(answer, str) or len(answer.encode()) > 64000:
        raise ValueError("Nuclia returned an invalid answer")
    retrieval, citations = value.get("retrieval_results"), value.get("citations", {})
    if (
        not isinstance(retrieval, dict)
        or not isinstance(retrieval.get("resources"), dict)
        or not isinstance(citations, dict)
    ):
        raise ValueError("Nuclia returned invalid retrieval or citation data")
    citations, footnotes = footnote_citations(value, citations, citation_mode, answer)
    sources = {}
    for resource_id, resource in retrieval["resources"].items():
        if not isinstance(resource, dict) or not isinstance(resource.get("fields"), dict):
            raise ValueError("Nuclia returned invalid source fields")
        for field_id, field in resource["fields"].items():
            if not isinstance(field, dict) or not isinstance(field.get("paragraphs"), dict):
                raise ValueError("Nuclia returned invalid source paragraphs")
            for paragraph_id, paragraph in field["paragraphs"].items():
                if not isinstance(paragraph, dict):
                    raise ValueError("Nuclia returned an invalid source paragraph")
                identity = paragraph.get("id", paragraph_id)
                if not isinstance(identity, str) or identity in sources:
                    raise ValueError("Nuclia returned an ambiguous paragraph identity")
                text = _text(paragraph.get("text"), "Source paragraph", 16000)
                position = paragraph.get("position") or {}
                if not isinstance(position, dict):
                    raise ValueError("Nuclia returned an invalid paragraph position")
                page = position.get("page_number")
                if page is not None and (type(page) is not int or page < 0):
                    raise ValueError("Nuclia returned an invalid source page")
                sources[identity] = {
                    "resource_id": resource_id,
                    "field_id": field_id,
                    "paragraph_id": identity,
                    "original_page": page,
                    "text": text,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                }
    retrieved = list(sources.values()) if capture_retrieval else None
    augmented = value.get("augmented_context")
    if augmented is not None:
        if not isinstance(augmented, dict):
            raise ValueError("Nuclia returned invalid augmented context")
        for collection in ("paragraphs", "fields"):
            blocks = augmented.get(collection, {})
            if not isinstance(blocks, dict) or len(blocks) > 100:
                raise ValueError("Nuclia returned invalid augmented text blocks")
            for identity, block in blocks.items():
                if (
                    not isinstance(identity, str)
                    or not isinstance(block, dict)
                    or block.get("id") != identity
                    or not re.fullmatch(
                        r"[A-Za-z0-9_-]{1,128}/[aftlc]/[A-Za-z0-9:_-]{1,128}(?:/[A-Za-z0-9:_-]{1,128}){0,5}",
                        identity,
                    )
                ):
                    raise ValueError("Nuclia returned an invalid augmented text identity")
                text = _text(block.get("text"), "Augmented source text", 16000)
                position = block.get("position") or {}
                if not isinstance(position, dict):
                    raise ValueError("Nuclia returned an invalid augmented source position")
                page = position.get("page_number")
                if page is not None and (type(page) is not int or page < 0):
                    raise ValueError("Nuclia returned an invalid augmented source page")
                kind = block.get("augmentation_type")
                if kind not in {
                    "neighbouring_paragraphs",
                    "conversation",
                    "hierarchy",
                    "full_resource",
                    "field_extension",
                    "metadata_extension",
                }:
                    raise ValueError("Nuclia returned an unsupported augmentation type")
                if identity in sources:
                    raise ValueError("Nuclia returned an ambiguous augmented source identity")
                parts = identity.split("/")
                sources[identity] = {
                    "resource_id": parts[0],
                    "field_id": "/".join(parts[1:3]),
                    "paragraph_id": identity,
                    "original_page": page,
                    "text": text,
                    "sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "augmentation_type": kind,
                }
    if set(citations) - sources.keys():
        raise ValueError("Nuclia cited paragraphs absent from its retrieval results")
    # Only cited paragraphs become snapshots. Uncited retrieval is not mislabeled as evidence.
    selected = [sources[key] for key in citations]
    if len(selected) > 20:
        raise ValueError("Nuclia returned more than twenty cited paragraphs; narrow the query")
    if value["status"] != "success" and citations:
        raise ValueError("Nuclia no-context result cannot include cited evidence")
    learning_id = value.get("learning_id")
    if learning_id is not None and (
        not isinstance(learning_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", learning_id)
    ):
        raise ValueError("Nuclia returned an invalid learning identifier")
    return {
        "output": answer,
        "status": value["status"],
        "sources": selected,
        "provider_citations": citations,
        "untrusted": True,
        **({"_retrieved_sources": retrieved} if retrieved is not None else {}),
        **({"citation_mode": citation_mode} if citation_mode != "default" else {}),
        **({"footnote_sources": footnotes} if citation_mode == "llm_footnotes" else {}),
        **({"learning_id": learning_id} if learning_id is not None else {}),
    }


def publish_answer(studio, result, identity, data, connector_id=None, *, question=None):
    """Publish through existing artifacts while holding the final execution fence."""
    actor = None
    if studio.team:
        studio.team.assert_execution(identity)
        actor = {"nexus_id": identity, "role": studio.team.bindings[identity]}
    run_id = current_run_id.get()
    retrieved = result.get("_retrieved_sources")
    result = {key: value for key, value in result.items() if key != "_retrieved_sources"}
    options = validate_answer_options(data["answer_options"]) if "answer_options" in data else None
    segments = prepare_segments(result) if options is not None else None
    answer_file = options is None or options["output_response_as_artifact"]
    evidence = None
    if data.get("capture_evaluation"):
        from nexus.core.studio_retrieval_evidence import (
            evidence_document,
            validate_evaluation_context,
        )

        scope = validate_evaluation_context(data.get("evaluation_context", "cited"))
        if scope == "retrieved" and retrieved is None:
            raise ValueError(
                "Original retrieval context is unavailable; ask again with capture enabled"
            )
        evidence = evidence_document(
            result,
            question,
            connector_id,
            run_id,
            retrieved_sources=retrieved if scope == "retrieved" else None,
        )
    with studio.lock:
        if studio.team:
            studio.team.assert_execution(identity)
        if connector_id and studio.catalog.get("connectors", connector_id)["data"] != data:
            raise ValueError("Nuclia connection changed before result publication")
        if run_id:
            run = studio.collaboration.owned(studio.run(run_id), actor)
            if run["status"] != "running" or run.get("cancel_requested"):
                raise ValueError("Nuclia execution stopped before result publication")
            if len(run.get("citations", [])) + len(result["sources"]) > 40:
                raise ValueError("Nuclia result would exceed the run citation limit")
            if (
                len(run.get("structured_content", []))
                + len(result["sources"])
                + int(answer_file)
                + bool(evidence)
                > 40
            ):
                raise ValueError("Nuclia result would exceed the run artifact limit")
        files = []
        for index, source in enumerate(result["sources"], 1):
            content = (
                f"Nuclia knowledge box: {data['knowledge_box']}\n"
                f"Resource: {source['resource_id']}\nField: {source['field_id']}\n"
                f"Paragraph: {source['paragraph_id']}\nOriginal provider page: {source['original_page']}\n\n"
                + source["text"]
            )
            files.append(
                {
                    "filename": f"nuclia-source-{index}-{source['sha256'][:12]}.txt",
                    "content_base64": base64.b64encode(content.encode()).decode(),
                }
            )
        if options is None:
            files.append(
                {
                    "filename": "nuclia-answer.md",
                    "content_base64": base64.b64encode(
                        (
                            f"Nuclia answer status: {result['status']}\n\n" + result["output"]
                        ).encode()
                    ).decode(),
                }
            )
        if evidence is not None:
            files.append(
                {
                    "filename": "retrieval-evidence.json",
                    "content_base64": base64.b64encode(
                        json.dumps(evidence, ensure_ascii=False).encode()
                    ).decode(),
                }
            )
        saved = studio.data_tools.publish(
            {"stdout": result["output"], "files": files}, actor, run_id
        )
        evidence_artifact = saved["artifacts"][-1] if evidence is not None else None
        citations = list(studio.run(run_id).get("citations", [])) if run_id else []
        for source, artifact in zip(
            result["sources"], saved["artifacts"][: len(result["sources"])], strict=True
        ):
            citation = {
                "kind": "document",
                "artifact_id": artifact["id"],
                "artifact_revision": artifact["revision"],
                "filename": artifact["filename"],
                "page": 1,
                "excerpt": source["text"][:4000],
                "sha256": source["sha256"],
                "retrieved_at": datetime.now(UTC).isoformat(),
                "provider": "nuclia",
                "resource_id": source["resource_id"],
                "field_id": source["field_id"],
                "paragraph_id": source["paragraph_id"],
                "original_page": source["original_page"],
                **(
                    {"augmentation_type": source["augmentation_type"]}
                    if "augmentation_type" in source
                    else {}
                ),
            }
            if run_id and connector_id and "download_original" in data.get("actions", []):
                from nexus.core.studio_nuclia_documents import original_reference

                reference = original_reference(data, connector_id, source)
                if reference:
                    citation["original_document"] = reference
                    citation["source_run_id"] = run_id
                    citation["source_index"] = len(citations)
            citations.append(citation)
            source.update(
                artifact_id=artifact["id"],
                artifact_revision=artifact["revision"],
                citation_ref=f"[source:{len(citations)}]",
            )
        if run_id:
            studio._update_run(run_id, citations=citations)
        presentation = {}
        if options is not None:
            body, compact = presented_answer(result, segments, options)
            if answer_file:
                prefix = (question or "")[: options["artifact_description_query_max_length"]]
                answer_saved = studio.data_tools.publish(
                    {
                        "stdout": result["output"],
                        "files": [
                            {
                                "filename": options["output_filename_base"] + ".md",
                                "description": "Nuclia answer for: " + prefix,
                                "content_base64": base64.b64encode(body.encode()).decode(),
                            }
                        ],
                    },
                    actor,
                    run_id,
                )
                saved["artifacts"].extend(answer_saved["artifacts"])
                presentation["response_artifact"] = answer_saved["artifacts"][0]
            else:
                presentation["response_content"] = body
            if options["include_citations_in_tool_response"]:
                presentation["citations"] = compact
        if evidence is not None:
            artifact = evidence_artifact
            if run_id:
                studio.execution.record(
                    run_id,
                    "retrieval",
                    provider="nuclia",
                    status=result["status"],
                    artifact_id=artifact["id"],
                    artifact_revision=artifact["revision"],
                )
            return {
                **result,
                **presentation,
                "artifacts": saved["artifacts"],
                "evaluation_evidence": artifact,
            }
        return {**result, **presentation, "artifacts": saved["artifacts"]}

"""Explicit Nuclia answers with bounded filters and private source snapshots."""

import base64
import hashlib
import json
import re
from datetime import UTC, datetime

from nexus.core.studio_catalog import _text
from nexus.core.studio_data_tools import current_run_id


def request_body(action, arguments, template=None, *, context=None):
    if template is not None:
        from nexus.core.studio_nuclia_templates import prepare_arguments

        arguments = prepare_arguments(template, arguments, context=context)
    allowed = {"query", "top_k", "page", "filter_expression"}
    if action == "ask":
        allowed |= {"prompt", "rephrase"}
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
        body["citations"] = "default"
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
    return body


def answer_sources(value):
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
        **({"learning_id": learning_id} if learning_id is not None else {}),
    }


def publish_answer(studio, result, identity, data, connector_id=None, *, question=None):
    """Publish through existing artifacts while holding the final execution fence."""
    actor = None
    if studio.team:
        studio.team.assert_execution(identity)
        actor = {"nexus_id": identity, "role": studio.team.bindings[identity]}
    run_id = current_run_id.get()
    evidence = None
    if data.get("capture_evaluation"):
        from nexus.core.studio_retrieval_evidence import evidence_document

        evidence = evidence_document(result, question, connector_id, run_id)
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
                len(run.get("structured_content", [])) + len(result["sources"]) + 1 + bool(evidence)
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
        files.append(
            {
                "filename": "nuclia-answer.md",
                "content_base64": base64.b64encode(
                    (f"Nuclia answer status: {result['status']}\n\n" + result["output"]).encode()
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
        if evidence is not None:
            artifact = saved["artifacts"][-1]
            if run_id:
                studio.execution.record(
                    run_id,
                    "retrieval",
                    provider="nuclia",
                    status=result["status"],
                    artifact_id=artifact["id"],
                    artifact_revision=artifact["revision"],
                )
            return {**result, "artifacts": saved["artifacts"], "evaluation_evidence": artifact}
        return {**result, "artifacts": saved["artifacts"]}

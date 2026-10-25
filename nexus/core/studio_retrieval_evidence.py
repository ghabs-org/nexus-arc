"""Private, revision-pinned retrieval evidence consumed by native evaluations."""

import hashlib
import json
import re

from nexus.core.studio_catalog import _text

FORMAT = "nexus-retrieval-evidence/v1"
RETRIEVED_FORMAT = "nexus-retrieval-evidence/v2"


def validate_evaluation_context(value):
    if not isinstance(value, str) or value not in {"cited", "retrieved"}:
        raise ValueError("Evaluation context must be cited or retrieved")
    return value


def validate_contexts(values):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError("Evaluation context must contain up to twenty text passages")
    for value in values:
        _text(value, "Evaluation context passage", 16000)
    if sum(len(value.encode()) for value in values) > 64000:
        raise ValueError("Evaluation context exceeds 64000 bytes; narrow the retrieval")
    if len(json.dumps(values, ensure_ascii=False).encode()) > 96000:
        raise ValueError("Encoded evaluation context exceeds 96000 bytes")
    return list(values)


def validate_retrieved_sources(sources):
    if not isinstance(sources, list) or len(sources) > 20:
        raise ValueError("Retrieved evidence requires up to twenty original paragraphs")
    identities = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Retrieved evidence contains an invalid source")
        resource, field, paragraph = (
            source.get(key) for key in ("resource_id", "field_id", "paragraph_id")
        )
        if (
            not isinstance(resource, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", resource)
            or not isinstance(field, str)
            or not re.fullmatch(r"[aftlc]/[A-Za-z0-9:_-]{1,128}", field)
            or not isinstance(paragraph, str)
            or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,512}", paragraph)
            or "://" in paragraph
            or paragraph in identities
        ):
            raise ValueError("Retrieved evidence source identities are invalid or ambiguous")
        identities.add(paragraph)
        text = _text(source.get("text"), "Retrieved evidence paragraph", 16000)
        page = source.get("original_page")
        if source.get("sha256") != hashlib.sha256(text.encode()).hexdigest() or (
            page is not None and (type(page) is not int or page < 0)
        ):
            raise ValueError("Retrieved evidence source digest/page is invalid")
    return sources


def evidence_document(result, question, connector_id, run_id, *, retrieved_sources=None):
    """Store selected evidence only; raw provider metadata and credentials stay out."""
    sources = (
        result["sources"]
        if retrieved_sources is None
        else validate_retrieved_sources(retrieved_sources)
    )
    contexts = validate_contexts([source["text"] for source in sources])
    cited = {source["paragraph_id"] for source in result["sources"]}
    document = {
        "format": FORMAT if retrieved_sources is None else RETRIEVED_FORMAT,
        "provider": "nuclia",
        "connector_id": connector_id,
        "run_id": run_id,
        "learning_id": result.get("learning_id"),
        "question": _text(question, "Retrieval question", 4000),
        "answer": result["output"],
        "status": result["status"],
        "contexts": contexts,
        "sources": [
            {
                **{
                    key: source[key]
                    for key in (
                        "resource_id",
                        "field_id",
                        "paragraph_id",
                        "sha256",
                        "original_page",
                        "augmentation_type",
                    )
                    if key in source
                },
                **(
                    {"cited": source["paragraph_id"] in cited}
                    if retrieved_sources is not None
                    else {}
                ),
            }
            for source in sources
        ],
        "context_scope": "cited-text-blocks-only"
        if any("augmentation_type" in source for source in result["sources"])
        else "cited-paragraphs-only",
    }
    if retrieved_sources is not None:
        document["context_scope"] = "retrieved-paragraphs-only"
    if len(json.dumps(document, ensure_ascii=False).encode()) > 160000:
        raise ValueError("Retrieval evidence exceeds 160000 bytes; narrow the retrieval")
    return document


def promotion_context(studio, reference, run_id, actor):
    """Resolve an owned, generated artifact revision before copying dataset context."""
    if (
        not isinstance(reference, dict)
        or set(reference) != {"artifact_id", "revision"}
        or not isinstance(reference.get("artifact_id"), str)
        or type(reference.get("revision")) is not int
        or reference["revision"] < 1
    ):
        raise ValueError("Select an evidence artifact and positive revision")
    studio.collaboration.owned(studio.run(run_id), actor)
    current = studio.collaboration.owned(
        studio.catalog.get("artifacts", reference["artifact_id"], archived=True), actor
    )
    pinned = studio.collaboration.owned(
        studio.artifacts.revision(current["id"], reference["revision"]), actor
    )
    if pinned.get("generated_by_run") != run_id:
        raise ValueError("Evidence must be generated by the rated run")
    content = studio.artifacts.get(pinned)
    if len(content) > 160000:
        raise ValueError("Retrieval evidence exceeds its bounded format")
    try:
        document = json.loads(content)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("Select a valid retrieval evidence document") from None
    if (
        not isinstance(document, dict)
        or document.get("format") not in (FORMAT, RETRIEVED_FORMAT)
        or document.get("run_id") != run_id
    ):
        raise ValueError("Select a retrieval evidence document from the rated run")
    contexts = validate_contexts(document.get("contexts"))
    if document["format"] == RETRIEVED_FORMAT:
        sources = document.get("sources")
        if (
            document.get("context_scope") != "retrieved-paragraphs-only"
            or not isinstance(sources, list)
            or len(sources) != len(contexts)
            or any(
                not isinstance(source, dict) or type(source.get("cited")) is not bool
                for source in sources
            )
        ):
            raise ValueError("Retrieved evidence scope/source alignment is invalid")
        validate_retrieved_sources(
            [{**source, "text": text} for source, text in zip(sources, contexts, strict=True)]
        )
    return {
        "contexts": contexts,
        "context_source": {"artifact_id": current["id"], "revision": reference["revision"]},
    }

"""Reviewed answer presentation using private Nexus source revisions only."""

import re

DEFAULTS = {
    "output_response_as_artifact": True,
    "output_filename_base": "nuclia-answer",
    "artifact_description_query_max_length": 150,
    "inline_citation_links": True,
    "include_citations_in_tool_response": False,
}
FOOTNOTE = re.compile(r"\[\^([A-Za-z0-9][A-Za-z0-9_-]{0,63})\]")


def validate_citation_mode(value):
    if not isinstance(value, str) or value not in {"default", "none", "llm_footnotes"}:
        raise ValueError("Nuclia citation mode must be default, none or llm_footnotes")
    return value


def validate_filename_base(name):
    """Normalize one local Markdown basename without exposing rejected values."""
    if not isinstance(name, str):
        raise ValueError("Nuclia answer filename requires a safe basename")
    name = name[:-3] if name.lower().endswith(".md") else name
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name)
        or ".." in name
        or name.endswith(".")
        or re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name.split(".")[0], re.I)
    ):
        raise ValueError("Nuclia answer filename requires a safe basename")
    return name


def prepare_answer_filename(action, arguments, options=None):
    """Separate local presentation data from provider query/template arguments."""
    if "output_filename_base" not in arguments:
        return arguments, None
    name = arguments["output_filename_base"]
    if (
        action != "ask"
        or name is not None
        and options is not None
        and not options.get("output_response_as_artifact", True)
    ):
        raise ValueError("An answer filename requires the selected ask action and file output")
    return (
        {key: value for key, value in arguments.items() if key != "output_filename_base"},
        validate_filename_base(name) if name is not None else None,
    )


def validate_answer_options(value):
    if not isinstance(value, dict) or set(value) - DEFAULTS.keys():
        raise ValueError("Use supported Nuclia answer presentation options only")
    result = {**DEFAULTS, **value}
    for key in (
        "output_response_as_artifact",
        "inline_citation_links",
        "include_citations_in_tool_response",
    ):
        if type(result[key]) is not bool:
            raise ValueError("Nuclia answer presentation flags must be boolean")
    result["output_filename_base"] = validate_filename_base(result["output_filename_base"])
    count = result["artifact_description_query_max_length"]
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("Nuclia description query prefix must be 1–1000 characters")
    return result


def footnote_citations(value, citations, mode, answer):
    """Validate provider footnote IDs; never accept URLs as citation destinations."""
    if mode == "none":
        if citations or value.get("citation_footnote_to_context"):
            raise ValueError("Nuclia returned citations with citation mode disabled")
        return citations, {}
    if mode != "llm_footnotes":
        return citations, {}
    mapping = value.get("citation_footnote_to_context", {})
    if (
        not isinstance(mapping, dict)
        or len(mapping) > 100
        or any(
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", key)
            or not isinstance(target, str)
            or len(target) > 1000
            for key, target in mapping.items()
        )
    ):
        raise ValueError("Nuclia returned an invalid footnote-to-context mapping")
    referenced = re.findall(r"\[\^([^\]\r\n]*)\]", answer)
    if len(referenced) > 2000 or set(referenced) - mapping.keys():
        raise ValueError("Nuclia returned unknown or excessive footnote references")
    selected = {key: mapping[key] for key in dict.fromkeys(referenced)}
    citations = dict(citations)
    for target in selected.values():
        citations.setdefault(target, [])
    return citations, selected


def prepare_segments(result):
    """Validate all markers before saving files; integer segments index owned sources."""
    answer = result["output"]
    segments, cursor = [], 0
    if result.get("citation_mode") == "llm_footnotes":
        sources = {source["paragraph_id"]: index for index, source in enumerate(result["sources"])}
        for match in FOOTNOTE.finditer(answer):
            segments.extend(
                [answer[cursor : match.start()], sources[result["footnote_sources"][match[1]]]]
            )
            cursor = match.end()
    else:
        insertions = {}
        for index, source in enumerate(result["sources"]):
            positions = result["provider_citations"].get(source["paragraph_id"], [])
            if not isinstance(positions, list) or len(positions) > 100:
                raise ValueError("Nuclia returned invalid citation positions")
            for pair in positions:
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or any(type(item) is not int for item in pair)
                    or not 0 <= pair[0] <= pair[1] <= len(answer)
                ):
                    raise ValueError("Nuclia returned invalid citation positions")
                insertions.setdefault(pair[1], set()).add(index)
        for position, indices in sorted(insertions.items()):
            segments.append(answer[cursor:position])
            segments.extend(sorted(indices))
            cursor = position
    segments.append(answer[cursor:])
    return segments


def presented_answer(result, segments, options):
    citations = []
    for index, source in enumerate(result["sources"]):
        citations.append(
            {
                "number": index + 1,
                "artifact_id": source["artifact_id"],
                "artifact_revision": source["artifact_revision"],
                "original_page": source["original_page"],
                "url": f"/api/catalog/artifacts/{source['artifact_id']}/versions/{source['artifact_revision']}",
            }
        )
    parts = []
    for segment in segments:
        if isinstance(segment, str):
            parts.append(segment)
        else:
            entry = citations[segment]
            label = str(entry["number"])
            parts.append(
                f" [[{label}]]({entry['url']})"
                if options["inline_citation_links"]
                else f" [{label}]"
            )
    body = "".join(parts)
    if citations:
        body += "\n\n**Sources:**\n" + "\n".join(
            f"{entry['number']}. [Source {entry['number']}]({entry['url']})"
            + (
                f" (source page {entry['original_page']})"
                if entry["original_page"] is not None
                else ""
            )
            for entry in citations
        )
    return body, citations

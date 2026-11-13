"""Bounded protocol output projection; never expose artifact storage references."""

import base64
import json

from nexus.core.studio_artifacts import content_type

INLINE_LIMIT = 16 * 1024 * 1024
OUTPUT_MODES = [
    "text/plain",
    "text/markdown",
    "application/json",
    "application/octet-stream",
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
]


def validate_modes(configuration):
    from nexus.core.studio_a2a_server import A2AError

    modes = configuration.get("acceptedOutputModes", [])
    if (
        not isinstance(modes, list)
        or len(modes) > 20
        or any(not isinstance(mode, str) or mode not in OUTPUT_MODES for mode in modes)
    ):
        raise A2AError(-32005, "Select supported output MIME types from the agent card")


def artifacts(studio, run, entrypoint, version):
    """Return only explicitly published, same-owner output revisions.

    Do not infer files from input IDs, task prose, arbitrary JSON, paths or URLs.
    A missing or changed output revision fails closed instead of leaking the
    current revision or silently discarding part of the result.
    """
    from nexus.core.studio_a2a_server import A2AError

    identity = run["id"]
    result = []
    if run.get("output"):
        result.append(
            {
                "artifactId": identity + "-output",
                "parts": [
                    {"text": run["output"], **({"kind": "text"} if version == "0.3" else {})}
                ],
            }
        )
    parts, seen, total = [], set(), 0
    content = run.get("structured_content", [])
    if not isinstance(content, list) or len(content) > 40:
        raise A2AError(-32006, "Invalid structured task output")
    for item in content:
        if not isinstance(item, dict):
            raise A2AError(-32006, "Invalid structured task output")
        kind = item.get("type")
        if kind in {"text", "markdown"}:
            if not isinstance(item.get("text"), str):
                raise A2AError(-32006, "Invalid structured text output")
            parts.append(
                {
                    "text": item["text"],
                    **(
                        {"kind": "text"}
                        if version == "0.3"
                        else {"mediaType": "text/markdown" if kind == "markdown" else "text/plain"}
                    ),
                }
            )
        elif kind == "json":
            try:
                encoded = json.dumps(item["data"], allow_nan=False)
                if len(encoded.encode()) > 128000:
                    raise ValueError
                value = json.loads(encoded)
            except (KeyError, ValueError, TypeError, OverflowError, RecursionError):
                raise A2AError(-32006, "Invalid structured JSON output") from None
            # Legacy DataPart is an object; 1.0 Part.data supports any JSON value.
            if version == "0.3":
                parts.append(
                    {"kind": "data", "data": value if isinstance(value, dict) else {"value": value}}
                )
            else:
                parts.append({"data": value, "mediaType": "application/json"})
        elif kind == "artifact":
            key = (item.get("artifact_id"), item.get("revision"))
            if not isinstance(key[0], str) or type(key[1]) is not int or key[1] < 1:
                raise A2AError(-32006, "Invalid output artifact reference")
            if key in seen:
                continue
            seen.add(key)
            try:
                artifact = studio.artifacts.revision(*key)
                if (artifact.get("owner_id") or "desktop") != (
                    entrypoint.get("owner_id") or "desktop"
                ):
                    raise PermissionError
                # Also check the current record: reassignment must revoke access.
                current = studio.catalog.get("artifacts", key[0], archived=True)
                if current.get("owner_id") != artifact.get("owner_id"):
                    raise PermissionError
                if artifact["data"].get("size", 0) > INLINE_LIMIT - total:
                    raise ValueError
                data = studio.artifacts.get(artifact)
                total += len(data)
                if total > INLINE_LIMIT:
                    raise ValueError
                encoded = base64.b64encode(data).decode()
                mime = content_type(data)
                filename = artifact["data"]["filename"]
            except (KeyError, ValueError, FileNotFoundError, PermissionError):
                raise A2AError(
                    -32006, "Output artifact unavailable or exceeds delivery limit"
                ) from None
            part = (
                {"kind": "file", "file": {"name": filename, "mimeType": mime, "bytes": encoded}}
                if version == "0.3"
                else {"raw": encoded, "filename": filename, "mediaType": mime}
            )
            result.append(
                {
                    "artifactId": identity + "-" + key[0] + "-r" + str(key[1]),
                    "name": filename,
                    "parts": [part],
                }
            )
        else:
            raise A2AError(-32006, "Unsupported structured task output")
    if parts:
        if len(json.dumps(parts, ensure_ascii=False).encode()) > 1024 * 1024:
            raise A2AError(-32006, "Structured task output exceeds delivery limit")
        result.insert(1 if result else 0, {"artifactId": identity + "-content", "parts": parts})
    return result

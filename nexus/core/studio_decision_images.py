"""Owned revision-pinned images for explicitly selected decision nodes."""

import base64
import hashlib
import io
import json
import re
import warnings

from nexus.adapters.decisions.errors import DecisionCallError

DEFAULT_IMAGES, DEFAULT_BYTES = 5, 20 * 1024 * 1024


def limits(config):
    result = []
    for key, default, ceiling in (
        ("max_images", DEFAULT_IMAGES, 128),
        ("max_image_bytes", DEFAULT_BYTES, 64 * 1024 * 1024),
    ):
        value = config.get(key, default)
        if type(value) is not int or not 0 <= value <= ceiling:
            raise ValueError(f"{key} must be an integer between zero and {ceiling}")
        result.append(value or default)
    return tuple(result)


def capable(config):
    return config.get("backend") == "openai" or (
        config.get("backend") == "openrouter"
        and config.get("model") == "openai/gpt-6-luna-decisions"
    )


def pin(studio, payload):
    payload.pop("_decision_image_revisions", None)
    if payload.get("kind") != "workflow":
        return

    def images(value):
        if isinstance(value, dict):
            return bool(value.get("images")) or any(images(v) for v in value.values())
        return isinstance(value, list) and any(images(v) for v in value)

    if not images(payload.get("workflow_snapshot", {})) and not images(
        payload.get("workflow_snapshots", {})
    ):
        return
    owner = payload.get("requester_nexus_id")
    records = [
        r for r in studio.catalog.list("artifacts") if not studio.team or r.get("owner_id") == owner
    ]
    if len(records) > 10000:
        raise ValueError("Decision image revision snapshot exceeds 10000 artifacts")
    payload["_decision_image_revisions"] = {
        r["id"]: {"revision": r["revision"], "filename": r["data"]["filename"], "name": r["name"]}
        for r in records
    }


def normalize_artifact_reference(reference):
    explicit = None
    if isinstance(reference, dict):
        allowed = {"id", "artifact_id", "name", "filename", "uri", "revision", "version"}
        if not reference or set(reference) - allowed:
            raise ValueError("Image references must name owned artifacts")
        targets = [
            reference[k]
            for k in ("id", "artifact_id", "name", "filename", "uri")
            if k in reference
        ]
        if len(targets) != 1 or not isinstance(targets[0], str):
            raise ValueError("Image references must name owned artifacts")
        revs = [reference[k] for k in ("revision", "version") if k in reference]
        if len(revs) > 1 or (revs and (type(revs[0]) is not int or revs[0] < 1)):
            raise ValueError("Use artifact ID or filename with optional :revision")
        if revs:
            explicit = revs[0]
        reference = targets[0]
    if not isinstance(reference, str) or not 1 <= len(reference) <= 500:
        raise ValueError("Image references must name owned artifacts")
    if any(ord(c) < 32 or c == "\\" for c in reference) or ".." in reference:
        raise ValueError("Use artifact ID or filename with optional :revision")
    text = reference.removeprefix("artifact://")
    for prefix in ("workspace/", "session/", "user/", "artifacts/"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    if "#" in text:
        text, frag = text.split("#", 1)
        m = re.fullmatch(r"v?([1-9][0-9]*)", frag)
        if not m or (explicit is not None and explicit != int(m[1])):
            raise ValueError("Use artifact ID or filename with optional :revision")
        explicit = int(m[1])
    if "?" in text:
        text, query = text.split("?", 1)
        m = re.fullmatch(r"(?:version|revision)=([1-9][0-9]*)", query)
        if not m or (explicit is not None and explicit != int(m[1])):
            raise ValueError("Use artifact ID or filename with optional :revision")
        explicit = int(m[1])
    match = re.fullmatch(r"([^/:@#?]+?)(?:[:@]([1-9][0-9]*))?", text)
    if not match:
        raise ValueError("Use artifact ID or filename with optional :revision")
    if match[2]:
        rev = int(match[2])
        if explicit is not None and explicit != rev:
            raise ValueError("Use artifact ID or filename with optional :revision")
        explicit = rev
    return match[1], explicit


def prepare(studio, references, config, payload, actor, *, run_id=None):
    count, maximum = limits(config)
    if not isinstance(references, list) or len(references) > 128:
        raise ValueError("Decision images require at most 128 entries")
    if references and not capable(config):
        raise ValueError("The selected decision model does not support images")
    flattened = []
    for value in references:
        if value is None or value == "" or value == []:
            continue
        flattened.extend(value if isinstance(value, list) else [value])
    if len(flattened) > count:
        raise DecisionCallError("input_too_large")
    result, total = [], 0
    for reference in flattened:
        name, explicit = normalize_artifact_reference(reference)
        pinned = payload.get("_decision_image_revisions", {})
        candidates = [
            identity
            for identity, row in pinned.items()
            if name in {identity, row["name"], row["filename"]}
        ]
        dynamic = False
        if not candidates and run_id:
            generated = [
                row
                for row in studio.catalog.list("artifacts")
                if row.get("generated_by_run") == run_id
                and name in {row["id"], row["name"], row["data"]["filename"]}
                and (not studio.team or row.get("owner_id") == studio.workbench.owner(actor))
            ]
            candidates = [row["id"] for row in generated]
            if not candidates:
                candidates = sorted(
                    {
                        row["artifact_id"]
                        for row in studio.records("decision-image-pins", limit=None)
                        if row.get("run_id") == run_id
                        and name in {row["artifact_id"], row.get("filename")}
                        and (
                            not studio.team or row.get("owner_id") == studio.workbench.owner(actor)
                        )
                    }
                )
            dynamic = True
        if len(candidates) != 1:
            raise ValueError(
                "Image artifact must exist uniquely in queued revisions or this run's generated files"
            )
        identity = candidates[0]
        current = studio.catalog.get("artifacts", identity, archived=True)
        revision = explicit or (current["revision"] if dynamic else pinned[identity]["revision"])
        if dynamic:
            pin_id = hashlib.sha256(json.dumps([run_id, identity, explicit]).encode()).hexdigest()
            with studio.lock:
                path = studio._path("decision-image-pins", pin_id)
                if path.exists():
                    revision = studio.workbench.visible(studio._read(path), actor, own=True)[
                        "revision"
                    ]
                else:
                    studio._write(
                        path,
                        {
                            "id": pin_id,
                            "run_id": run_id,
                            "owner_id": studio.workbench.owner(actor),
                            "artifact_id": identity,
                            "filename": current["data"]["filename"],
                            "revision": revision,
                        },
                    )
        record = studio.artifacts.revision(identity, revision)
        if dynamic and record.get("generated_by_run") != run_id:
            raise PermissionError("Generated image revision belongs to another run")
        studio.workbench.visible(current, actor, own=True)
        studio.workbench.visible(record, actor, own=True)
        if record["data"].get("size", maximum + 1) > maximum:
            raise DecisionCallError("input_too_large")
        content = studio.artifacts.get(record)
        if len(content) > maximum:
            raise DecisionCallError("input_too_large")
        try:
            from PIL import Image
        except ImportError:
            raise ValueError("Install nexus-arc[studio-images] for decision images") from None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(content)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"}:
                        raise ValueError("Decision artifacts must be PNG, JPEG or WebP images")
                    mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[
                        image.format
                    ]
                    width, height = image.size
                    if max(width, height) > 8000:
                        if width * height > 40_000_000:
                            raise DecisionCallError("input_too_large")
                        image.thumbnail((8000, 8000))
                        output = io.BytesIO()
                        image.save(output, format="JPEG" if image.format == "JPEG" else "PNG")
                        content = output.getvalue()
                        mime = "image/jpeg" if image.format == "JPEG" else "image/png"
                    else:
                        image.verify()
        except DecisionCallError:
            raise
        except Exception:
            raise ValueError(
                "Decision image is invalid, unsupported or cannot be decoded"
            ) from None
        studio.workbench.visible(
            studio.catalog.get("artifacts", identity, archived=True), actor, own=True
        )
        if studio.team:
            studio.team.assert_execution(payload.get("requester_nexus_id"))
        total += len(content)
        if total > maximum:
            raise DecisionCallError("input_too_large")
        result.append("data:" + mime + ";base64," + base64.b64encode(content).decode())
    return result

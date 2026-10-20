"""Bounded A2A inputs and private, idempotent rich-result ingestion."""

import base64
import binascii
import hashlib
import json
from pathlib import PurePath

from nexus.core.auth.delegated_credentials import scope_key
from nexus.core.studio_artifacts import content_type

FILE_LIMIT = 16 * 1024 * 1024
TOTAL_LIMIT = 16 * 1024 * 1024
RESPONSE_LIMIT = 24 * 1024 * 1024
TEXT_LIMIT = 262144


def json_value(value, limit=64000):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(text.encode()) > limit:
            raise ValueError
        return json.loads(text)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("Remote structured content exceeds limits or is not finite JSON") from None


def filename(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or PurePath(value).name != value
        or value in {".", ".."}
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Remote file names must be short names without directories")
    return value


def binding(profile):
    return scope_key(profile["id"], profile.get("owner_id"), profile["data"])


class StudioExternalContent:
    def __init__(self, external):
        self.external, self.studio = external, external.studio

    def attachment(self, ref, actor):
        if (
            not isinstance(ref, dict)
            or set(ref) != {"id", "revision"}
            or not isinstance(ref.get("id"), str)
            or type(ref.get("revision")) is not int
            or ref["revision"] < 1
        ):
            raise ValueError("External attachments require pinned artifact ID and revision")
        current = self.studio.catalog.get("artifacts", ref["id"], archived=True)
        record = self.studio.artifacts.revision(ref["id"], ref["revision"])
        if self.studio.team and any(r.get("owner_id") != actor for r in (current, record)):
            raise PermissionError("Only your own artifacts can be sent to an external agent")
        if record["data"].get("size", 0) > FILE_LIMIT:
            raise ValueError("External input file exceeds 16 MiB")
        return record

    def prepare(self, payload):
        actor = payload.get("requester_nexus_id")
        profile = self.external.profile(payload["external_agent_id"])
        if self.studio.team:
            principal = self.studio.workbench.actor(payload)
            if not self.studio.team.visible(profile, principal):
                raise PermissionError("External agent connection belongs to another user")
        refs = payload.get("artifact_refs", [])
        if not isinstance(refs, list) or len(refs) > 5:
            raise ValueError("Send up to five external attachments")
        refs = [dict(ref) if isinstance(ref, dict) else ref for ref in refs]
        total = 0
        for ref in refs:
            record = self.attachment(ref, actor)
            total += record["data"].get("size", 0)
        if total > TOTAL_LIMIT:
            raise ValueError("External input files exceed 16 MiB combined")
        payload["artifact_refs"] = refs
        if "external_input" in payload:
            value = json_value(payload["external_input"])
            if profile["data"]["protocol_version"] == "0.3" and not isinstance(value, dict):
                raise ValueError("A2A 0.3 structured input must be a JSON object")
            payload["external_input"] = value
        # Always replace caller-supplied binding with the server's reviewed one.
        payload["external_binding"] = binding(profile)
        return payload

    def parts(self, payload, profile, card, actor):
        if payload.get("external_binding") and payload["external_binding"] != binding(profile):
            raise ValueError("External connection changed while queued; review before sending")
        version = profile["data"]["protocol_version"]
        parts = [{"text": payload["task"], **({"kind": "text"} if version == "0.3" else {})}]
        modes = card.get("input_modes")

        def accepts(mime):
            if not isinstance(modes, list) or not any(
                m in {mime, "*/*", mime.split("/")[0] + "/*"} for m in modes
            ):
                raise ValueError("Remote agent does not advertise input mode: " + mime)

        if "external_input" in payload:
            accepts("application/json")
            value = json_value(payload["external_input"])
            parts.append(
                {
                    "data": value,
                    **({"kind": "data"} if version == "0.3" else {"mediaType": "application/json"}),
                }
            )
        total = 0
        for ref in payload.get("artifact_refs", []):
            record = self.attachment(ref, actor)
            data = self.studio.artifacts.get(record)
            total += len(data)
            if len(data) > FILE_LIMIT or total > TOTAL_LIMIT:
                raise ValueError("External input files exceed 16 MiB combined")
            mime = content_type(data)
            accepts(mime)
            name = filename(record["data"]["filename"])
            encoded = base64.b64encode(data).decode()
            parts.append(
                {"kind": "file", "file": {"name": name, "mimeType": mime, "bytes": encoded}}
                if version == "0.3"
                else {"raw": encoded, "filename": name, "mediaType": mime}
            )
        return parts

    def plan(self, objects, profile, actor):
        """Validate the complete response before creating any local artifacts."""
        if not isinstance(objects, list) or len(objects) > 40:
            raise ValueError("Remote response must contain at most forty artifacts/messages")
        planned, text, total, count = [], [], 0, 0
        version = profile["data"]["protocol_version"]
        for obj in objects:
            if (
                not isinstance(obj, dict)
                or not isinstance(obj.get("parts"), list)
                or not obj["parts"]
            ):
                raise ValueError("Remote artifacts require message parts")
            for part in obj["parts"]:
                count += 1
                if (
                    count > 40
                    or not isinstance(part, dict)
                    or sum(k in part for k in ("text", "data", "raw", "url", "file")) != 1
                ):
                    raise ValueError("Remote response contains invalid or too many parts")
                if "text" in part:
                    if not isinstance(part["text"], str):
                        raise ValueError("Remote text part must be a string")
                    text.append(part["text"])
                    planned.append({"type": "text", "text": part["text"]})
                elif "data" in part:
                    value = json_value(part["data"], 128000)
                    if version == "0.3" and not isinstance(value, dict):
                        raise ValueError("Legacy remote DataPart must be an object")
                    planned.append({"type": "json", "data": value})
                else:
                    encoded, url = part.get("raw"), part.get("url")
                    name = part.get("filename", "attachment.bin")
                    if version == "0.3":
                        file = part.get("file")
                        if (
                            not isinstance(file, dict)
                            or sum(k in file for k in ("bytes", "uri")) != 1
                        ):
                            raise ValueError("Legacy remote file requires bytes or URI")
                        encoded, url, name = (
                            file.get("bytes"),
                            file.get("uri"),
                            file.get("name", name),
                        )
                    elif "file" in part:
                        raise ValueError("A2A 1.0 requires raw or URL file parts")
                    name = filename(name)
                    if encoded is not None:
                        if not isinstance(encoded, str) or len(encoded) > 4 * (
                            (FILE_LIMIT + 2) // 3
                        ):
                            raise ValueError("Remote inline file exceeds 16 MiB")
                        try:
                            data = base64.b64decode(encoded, validate=True)
                        except (ValueError, binascii.Error):
                            raise ValueError("Remote inline file has invalid base64") from None
                    elif url is not None:
                        data = self.external.download(url, profile, actor, TOTAL_LIMIT - total)
                    else:
                        raise ValueError("Unsupported remote file part")
                    total += len(data)
                    if len(data) > FILE_LIMIT or total > TOTAL_LIMIT:
                        raise ValueError("Remote file output exceeds 16 MiB combined")
                    planned.append({"type": "file", "filename": name, "bytes": data})
        output = "\n\n".join(text)
        metadata = [{k: v for k, v in part.items() if k != "bytes"} for part in planned]
        if (
            len(output.encode()) > TEXT_LIMIT
            or len(json.dumps(metadata, ensure_ascii=False).encode()) > TEXT_LIMIT
        ):
            raise ValueError("Remote structured output exceeds 256 KiB")
        if not output:
            output = "\n".join(
                json.dumps(p["data"], ensure_ascii=False) for p in planned if p["type"] == "json"
            )
        fallback = (
            "Remote task returned file artifacts"
            if any(p["type"] == "file" for p in planned)
            else "Remote task completed without text artifacts"
        )
        return output or fallback, planned

    def publish(self, run_id, planned, actor):
        parts = []
        if self.studio.team:
            self.studio.team.assert_execution(actor)
        for index, item in enumerate(planned):
            if item["type"] != "file":
                parts.append(item)
                continue
            identity = "remote-" + scope_key(
                run_id, index, item["filename"], hashlib.sha256(item["bytes"]).hexdigest()
            )
            path = self.studio.catalog._path("artifacts", identity)
            if path.exists():
                record = self.studio.catalog.get("artifacts", identity, archived=True)
                if (
                    record.get("owner_id") != actor
                    or self.studio.artifacts.get(record) != item["bytes"]
                ):
                    raise PermissionError("Remote output artifact publication conflict")
            else:
                record = self.studio.catalog.save(
                    "artifacts",
                    {
                        "id": identity,
                        "name": item["filename"],
                        "requester_nexus_id": actor,
                        "description": "File returned by an explicitly selected external agent",
                        "data": {
                            "filename": item["filename"],
                            "content_base64": base64.b64encode(item["bytes"]).decode(),
                        },
                    },
                )
                self.studio.execution.record(
                    run_id,
                    "artifact",
                    status="created",
                    artifact_id=identity,
                    artifact_revision=record["revision"],
                )
            parts.append(
                {
                    "type": "artifact",
                    "artifact_id": identity,
                    "revision": record["revision"],
                    "filename": item["filename"],
                }
            )
        return parts

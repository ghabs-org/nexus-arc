"""Opaque, owner-bound choices for files attached to a cited conversation field."""

import json
import re
from urllib.parse import quote, urlencode

from nexus.core.studio_configuration import digest
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_remote import remote_headers

COLLECTION = "source-attachment-selections"


def _message(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode()) <= 128
        or value in {"0", ".", ".."}
        or "/" in value
        or "\\" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Nuclia returned an invalid conversation message identifier")
    return value


def _field(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value):
        raise ValueError("Nuclia returned an invalid attached field identifier")
    return value


def _filename(value, fallback):
    if not isinstance(value, str) or "://" in value:
        return fallback
    value = value.replace("\\", "/").split("/")[-1]
    value = "".join(char for char in value if ord(char) >= 32 and ord(char) != 127).strip()
    return value[:160] if value and value not in {".", ".."} else fallback


def _binding(source, arguments, identity):
    return {
        "owner_id": identity,
        "source": {key: arguments[key] for key in ("source_run_id", "source_index")},
        "connection": source["original_document"],
        "resource_id": source["resource_id"],
        "field_id": source["field_id"],
    }


def attachment_path(studio, source, arguments, identity):
    if source["field_id"].startswith("f/"):
        return "/file/" + quote(source["field_id"][2:], safe="") + "/download/field"
    path = studio._path(COLLECTION, arguments["attachment_id"])
    if not path.exists():
        raise ValueError("Attachment selection is unavailable; list the files again")
    item = studio._read(path)
    if (
        item.get("id") != arguments["attachment_id"]
        or digest({key: value for key, value in item.items() if key != "id"})
        != arguments["attachment_id"]
    ):
        raise ValueError("Saved attachment selection changed; list the files again")
    binding = _binding(source, arguments, identity)
    if item.get("version") != 1 or any(item.get(key) != value for key, value in binding.items()):
        raise PermissionError("Attachment selection belongs to another source or account")
    target = item["target"]
    if target["kind"] == "file":
        return "/file/" + quote(_field(target["field_id"]), safe="") + "/download/field"
    if (
        target["kind"] != "conversation"
        or type(target.get("index")) is not int
        or not 0 <= target["index"] < 50
    ):
        raise ValueError("Saved attachment selection is invalid")
    return (
        "/conversation/"
        + quote(source["field_id"][2:], safe="")
        + "/download/field/"
        + quote(_message(target["message_id"]), safe="")
        + "/"
        + str(target["index"])
    )


def _choices(response, field, page):
    """Keep filenames and canonical IDs only; never retain message text or URLs."""
    if (
        not isinstance(response, dict)
        or response.get("field_type") != "conversation"
        or response.get("field_id") != field
    ):
        raise ValueError("Nuclia returned a different conversation field")
    value = response.get("value")
    if (
        not isinstance(value, dict)
        or value.get("page") != page
        or type(value.get("page")) is not int
    ):
        raise ValueError("Nuclia returned an invalid conversation page")
    if (
        type(value.get("pages")) is not int
        or not page <= value["pages"] <= 1000
        or type(value.get("total")) is not int
        or not 0 <= value["total"] <= 51200
    ):
        raise ValueError("Nuclia conversation counters exceed supported bounds")
    messages = value.get("messages")
    messages = [] if messages is None else messages
    if not isinstance(messages, list) or len(messages) > 2048 or len(messages) > value["total"]:
        raise ValueError("Nuclia returned an invalid conversation message list")
    choices, seen, unsupported = [], set(), 0
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("content"), dict):
            raise ValueError("Nuclia returned invalid conversation content")
        identity = _message(message.get("ident"))
        if identity in seen:
            raise ValueError("Nuclia returned ambiguous conversation message identifiers")
        seen.add(identity)
        content = message["content"]
        for key in ("attachments", "attachments_fields"):
            attachments = content.get(key)
            attachments = [] if attachments is None else attachments
            if not isinstance(attachments, list) or len(attachments) > 50:
                raise ValueError("Nuclia returned too many or invalid message attachments")
            for index, attachment in enumerate(attachments):
                if not isinstance(attachment, dict):
                    raise ValueError("Nuclia returned an invalid attachment")
                if key == "attachments_fields":
                    if (
                        attachment.get("field_type") != "file"
                        or attachment.get("split") is not None
                    ):
                        unsupported += 1
                        continue
                    field_id = _field(attachment.get("field_id"))
                    target = {"kind": "file", "field_id": field_id}
                    filename = "File field " + field_id
                else:
                    target = {"kind": "conversation", "message_id": identity, "index": index}
                    filename = _filename(attachment.get("filename"), f"Attachment {index + 1}")
                choices.append({"message_id": identity, "filename": filename, "target": target})
                if len(choices) > 10000:
                    raise ValueError("Conversation attachment count exceeds supported bounds")
    return choices, value["pages"], unsupported


def list_attachments(studio, data, identity, arguments, connector_id):
    from nexus.core.studio_nuclia_documents import resolve

    run_id = current_run_id.get()
    with studio.lock:
        actor, source, connector = resolve(studio, arguments, identity)
        if connector["id"] != connector_id or connector["data"] != data:
            raise ValueError("Original-file connection changed")

        def fence():
            checked_actor, checked_source, checked_connector = resolve(studio, arguments, identity)
            if checked_source != source or checked_connector["data"] != data:
                raise ValueError("Saved source changed before attachment publication")
            if run_id:
                run = studio.collaboration.owned(studio.run(run_id), checked_actor)
                if run["status"] != "running" or run.get("cancel_requested"):
                    raise ValueError("Attachment listing stopped before publication")

        fence()
    page, offset = arguments.get("page", 1), arguments.get("offset", 0)
    headers = remote_headers(
        studio, {**data, "auth": {"type": "bearer", "token_env": data["token_env"]}}, identity
    )
    field = source["field_id"][2:]
    response = studio.research.request(
        "GET",
        data["url"]
        + "/api/v1/kb/"
        + quote(data["knowledge_box"], safe="")
        + "/resource/"
        + quote(source["resource_id"], safe="")
        + "/conversation/"
        + quote(field, safe="")
        + "?"
        + urlencode({"show": "value", "page": page}),
        {"X-NUCLIA-SERVICEACCOUNT": headers["Authorization"]},
        private=data.get("allow_private", False),
        binary=True,
        response_limit=256 * 1024,
        timeout=60,
    )
    raw = response.get("content")
    if (
        not isinstance(raw, bytes)
        or len(raw) > 256 * 1024
        or response.get("content_type") != "application/json"
    ):
        raise ValueError("Nuclia conversation metadata is invalid or exceeds 256 KiB")
    try:
        choices, pages, unsupported = _choices(json.loads(raw), field, page)
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("Nuclia returned invalid conversation metadata JSON") from None
    if offset > len(choices):
        raise ValueError("Attachment offset exceeds the available choices")
    selected = []
    with studio.lock:
        fence()
        for choice in choices[offset : offset + 100]:
            item = {"version": 1, **_binding(source, arguments, identity), **choice, "page": page}
            key = digest(item)
            studio._write(studio._path(COLLECTION, key), {**item, "id": key})
            selected.append(
                {
                    "attachment_id": key,
                    "filename": choice["filename"],
                    "message_id": choice["message_id"],
                }
            )
    return {
        "version": 1,
        "attachments": selected,
        "page": page,
        "pages": pages,
        "offset": offset,
        "attachment_count": len(choices),
        "unsupported_references": unsupported,
        "next_offset": offset + 100 if len(choices) > offset + 100 else None,
        "next_page": page + 1 if page < pages else None,
        "remote_revision_pinned": False,
    }

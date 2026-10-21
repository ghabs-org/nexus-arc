"""Owned cited-file downloads through Nexus jobs, credentials and private artifacts.

Provider URLs and ephemeral tokens are never accepted from a citation or client.
The canonical read endpoint uses the selected connection's service account.
"""

import base64
import copy
import hashlib
import re
from datetime import UTC, datetime
from urllib.parse import quote

from nexus.core.studio_configuration import digest
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_remote import remote_headers

EXTENSIONS = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/json": ".json",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
}


def original_reference(data, connector_id, source):
    if (
        not isinstance(source.get("resource_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source["resource_id"])
        or not isinstance(source.get("field_id"), str)
        or not re.fullmatch(r"f/[A-Za-z0-9:_-]{1,128}", source["field_id"])
    ):
        return None
    return {"connector_id": connector_id, "configuration_digest": digest(data)}


def actor_for(studio, identity):
    if not studio.team:
        return None
    studio.team.assert_execution(identity)
    return {"nexus_id": identity, "role": studio.team.bindings[identity]}


def resolve(studio, arguments, identity):
    if (
        not isinstance(arguments, dict)
        or set(arguments) != {"source_run_id", "source_index"}
        or not isinstance(arguments["source_run_id"], str)
        or type(arguments["source_index"]) is not int
        or not 0 <= arguments["source_index"] < 40
    ):
        raise ValueError("Select a saved run and citation index")
    actor = actor_for(studio, identity)
    run = studio.collaboration.owned(studio.run(arguments["source_run_id"]), actor)
    if run["status"] != "completed":
        raise ValueError("Open original files from a completed run")
    citations = run.get("citations", [])
    index = arguments["source_index"]
    if index >= len(citations):
        raise ValueError("Saved citation is unavailable")
    source = citations[index]
    reference = source.get("original_document")
    if not isinstance(reference, dict) or not isinstance(reference.get("connector_id"), str):
        raise ValueError("This citation has no enabled original-file connection")
    connector = studio.catalog.get("connectors", reference["connector_id"])
    data = connector["data"]
    if (
        data.get("type") != "nuclia"
        or not data.get("enabled")
        or "download_original" not in data.get("actions", [])
    ):
        raise PermissionError("Original-file access is disabled")
    studio.resource_lifecycle.assert_launch("connectors:" + connector["id"])
    if reference != original_reference(data, connector["id"], source):
        raise ValueError("Source connection changed; ask again before opening the original")
    return actor, copy.deepcopy(source), connector


def queue_original(studio, arguments, actor):
    identity = actor["nexus_id"] if actor else None
    _, _, connector = resolve(studio, arguments, identity)
    name = (
        "connector:"
        + hashlib.sha256(str(studio.root).encode()).hexdigest()[:8]
        + ":"
        + connector["id"]
    )
    if name not in studio.operations.tools:
        raise ValueError("Original-file connection is unavailable")
    return studio.start_run(
        {
            "kind": "tool",
            "tool": name,
            "kwargs": {"action": "download_original", "arguments": arguments},
            "requester_nexus_id": identity,
        }
    )


def download_original(studio, data, identity, arguments, connector_id):
    with studio.lock:
        actor, source, connector = resolve(studio, arguments, identity)
        if connector["id"] != connector_id or connector["data"] != data:
            raise ValueError("Original-file connection changed")
        key = digest(
            {"owner": identity, "source": arguments, "connection": source["original_document"]}
        )
        receipt_path = studio._path("source-originals", key)
        run_id = current_run_id.get()

        def fence():
            checked_actor, checked_source, checked_connector = resolve(studio, arguments, identity)
            if checked_source != source or checked_connector["data"] != data:
                raise ValueError("Saved source changed before publication")
            if run_id:
                run = studio.collaboration.owned(studio.run(run_id), checked_actor)
                if run["status"] != "running" or run.get("cancel_requested"):
                    raise ValueError("Original-file request stopped before publication")
                if len(run.get("structured_content", [])) >= 40:
                    raise ValueError("Original file would exceed the run artifact limit")

        def reuse():
            if not receipt_path.exists():
                return None
            fence()
            receipt = studio._read(receipt_path)
            artifact = receipt["artifacts"][0]
            studio.collaboration.owned(
                studio.catalog.get("artifacts", artifact["id"], archived=True), actor
            )
            studio.collaboration.owned(
                studio.artifacts.revision(artifact["id"], artifact["revision"]), actor
            )
            if run_id:
                run = studio.run(run_id)
                studio._update_run(
                    run_id,
                    content_version=1,
                    structured_content=[
                        *run.get("structured_content", []),
                        {
                            "type": "artifact",
                            "artifact_id": artifact["id"],
                            "revision": artifact["revision"],
                            "filename": artifact["filename"],
                        },
                    ],
                )
                studio.execution.record(
                    run_id,
                    "artifact",
                    status="referenced",
                    artifact_id=artifact["id"],
                    artifact_revision=artifact["revision"],
                )
            return {**receipt, "cached": True}

        cached = reuse()
        if cached:
            return cached
        fence()
    headers = remote_headers(
        studio, {**data, "auth": {"type": "bearer", "token_env": data["token_env"]}}, identity
    )
    url = (
        data["url"]
        + "/api/v1/kb/"
        + quote(data["knowledge_box"], safe="")
        + "/resource/"
        + quote(source["resource_id"], safe="")
        + "/file/"
        + quote(source["field_id"][2:], safe="")
        + "/download/field"
    )
    downloaded = studio.research.request(
        "GET",
        url,
        {"X-NUCLIA-SERVICEACCOUNT": headers["Authorization"]},
        private=data.get("allow_private", False),
        binary=True,
        response_limit=16 * 1024 * 1024,
        timeout=60,
    )
    content, kind = downloaded.get("content"), downloaded.get("content_type")
    if (
        not isinstance(content, bytes)
        or not content
        or len(content) > 16 * 1024 * 1024
        or not isinstance(kind, str)
    ):
        raise ValueError("Original file response is invalid or exceeds 16 MiB")
    sha256 = hashlib.sha256(content).hexdigest()
    filename = "nuclia-original-" + sha256[:12] + EXTENSIONS.get(kind, ".bin")
    with studio.lock:
        fence()
        # Concurrent read jobs may finish together; publish only one local snapshot.
        cached = reuse()
        if cached:
            return cached
        saved = studio.data_tools.publish(
            {
                "stdout": "Saved original document snapshot; remote historical revision is not guaranteed.",
                "files": [
                    {"filename": filename, "content_base64": base64.b64encode(content).decode()}
                ],
            },
            actor,
            run_id,
        )
        result = {
            **saved,
            "version": 1,
            "owner_id": identity,
            "source_run_id": arguments["source_run_id"],
            "source_index": arguments["source_index"],
            "sha256": sha256,
            "content_type": kind,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "remote_revision_pinned": False,
            "cached": False,
        }
        studio._write(receipt_path, result)
        return result

"""Private, idempotent copies of currently authorized conversation snapshots."""

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime

from nexus.core.studio_knowledge import owner


def continue_snapshot(studio, data, actor=None):
    fields = {"snapshot", "visibility", "request_id", "requester_nexus_id", "_role"}
    if not isinstance(data, dict) or set(data) - fields:
        raise ValueError("Choose a snapshot, visibility and unique request ID")
    if data.get("visibility") not in {"private", "public"}:
        raise ValueError("Choose private or public snapshot visibility")
    try:
        request_id = str(uuid.UUID(data["request_id"]))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("Continuation requires a UUID request ID") from None
    if studio.team:
        studio.team.authorize(actor, "/api/runs", {"kind": "chat"})
        studio.team.assert_execution(actor["nexus_id"])
    public = data["visibility"] == "public"
    with studio.lock:
        snapshot = studio.collaboration.get_share(data.get("snapshot"), actor, public)
        identity = hashlib.sha256(
            f"{owner(actor)}:{snapshot['id']}:{request_id}".encode()
        ).hexdigest()[:32]
        path = studio._path("sessions", identity)
        if path.exists():
            session = studio.collaboration.owned(studio.session(identity), actor)
            return {
                "session_id": session["id"],
                "artifact_refs": session.get("snapshot_artifacts", []),
            }
        messages = []
        for message in snapshot["messages"]:
            if message.get("role") not in {"user", "assistant"} or not isinstance(
                message.get("content"), str
            ):
                raise ValueError("Snapshot contains unsupported conversation messages")
            messages.append({"role": message["role"], "content": message["content"]})
        if not messages or len(messages) > 2000 or len(json.dumps(messages).encode()) > 1024 * 1024:
            raise ValueError("Snapshot conversation exceeds the private-copy limit")
        # Authorization is the snapshot grant, not source-file ownership. Only
        # files explicitly included in this immutable snapshot may be copied.
        prepared, total = [], 0
        for ref in snapshot.get("artifacts", []):
            record = studio.artifacts.revision(ref["id"], ref["revision"])
            content = studio.artifacts.get(record)
            total += len(content)
            if total > 32 * 1024 * 1024:
                raise ValueError("Snapshot files exceed the 32 MiB private-copy limit")
            prepared.append((ref, content, hashlib.sha256(content).hexdigest()))
        copied = []
        for ref, content, digest in prepared:
            file_id = hashlib.sha256(
                f"{identity}:{ref['id']}:{ref['revision']}".encode()
            ).hexdigest()[:32]
            if studio.catalog._path("artifacts", file_id).exists():
                record = studio.collaboration.owned(studio.catalog.get("artifacts", file_id), actor)
                if (
                    record["revision"] != 1
                    or record["data"].get("sha256") != digest
                    or record["data"]["filename"] != ref["filename"]
                ):
                    raise ValueError("A partial private copy changed; use a new request ID")
            else:
                record = studio.catalog.save(
                    "artifacts",
                    {
                        "id": file_id,
                        "name": ref["filename"],
                        "requester_nexus_id": actor["nexus_id"] if actor else None,
                        "description": "Private copy from a shared conversation snapshot",
                        "data": {
                            "filename": ref["filename"],
                            "content_base64": base64.b64encode(content).decode(),
                        },
                    },
                )
            copied.append({"id": record["id"], "revision": 1, "filename": ref["filename"]})
        # Time may have advanced during blob IO. Never publish a conversation
        # after its source grant has expired or been revoked.
        studio.collaboration.get_share(data["snapshot"], actor, public)
        now = datetime.now(UTC).isoformat()
        session = {
            "id": identity,
            "owner_id": actor["nexus_id"] if actor else None,
            "title": "Continued: " + str(snapshot["title"])[:180],
            "messages": messages,
            "created_at": now,
            "updated_at": now,
            "snapshot_artifacts": copied,
            "continued_snapshot_id": snapshot["id"],
            "provider": "codex",
            "agent_id": None,
            "knowledge_project_id": None,
        }
        studio._write(path, session)
        return {"session_id": identity, "artifact_refs": copied}

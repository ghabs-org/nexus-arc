"""Revocable read-only snapshots and owner-scoped response feedback."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from nexus.core.studio_catalog import _text
from nexus.core.studio_knowledge import owner


class StudioCollaboration:
    def __init__(self, studio):
        self.studio = studio

    def owned(self, record, actor):
        if self.studio.team and not self.studio.team.visible(record, actor):
            raise PermissionError("Conversation or run belongs to another account")
        return record

    def feedback(self, data, actor):
        run = self.owned(self.studio.run(data.get("run_id")), actor)
        if run["status"] != "completed":
            raise ValueError("Rate a completed response")
        rating = data.get("rating")
        comment = data.get("comment", "")
        if type(rating) is not int or rating not in {-1, 1}:
            raise ValueError("Rating must be -1 or 1")
        if not isinstance(comment, str) or len(comment) > 2000:
            raise ValueError("Comment must be text up to 2000 characters")
        identity = hashlib.sha256(f"{owner(actor)}:{run['id']}".encode()).hexdigest()
        with self.studio.lock:
            path = self.studio._path("response-feedback", identity)
            prior = self.studio._read(path) if path.exists() else {}
            if data.get("revision", 0) != prior.get("revision", 0):
                raise ValueError("Feedback changed; reopen its latest revision")
            record = {
                "id": identity,
                "version": 1,
                "owner_id": owner(actor),
                "run_id": run["id"],
                "rating": rating,
                "comment": comment,
                "task": run["task"],
                "revision": prior.get("revision", 0) + 1,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self.studio._write(path, record)
            return record

    def list_feedback(self, actor):
        return [
            record
            for record in self.studio.records("response-feedback", limit=None)
            if record["owner_id"] == owner(actor)
        ]

    def promote(self, data, actor):
        record = self.studio._read(self.studio._path("response-feedback", data.get("feedback_id")))
        if record["owner_id"] != owner(actor):
            raise PermissionError("Feedback belongs to another account")
        expected = _text(data.get("expected"), "Expected answer phrase", 1000)
        case = {"input": record["task"], "contains": [expected]}
        if "evidence" in data:
            from nexus.core.studio_retrieval_evidence import promotion_context

            case.update(promotion_context(self.studio, data["evidence"], record["run_id"], actor))
        if "judge" in data:
            case["judge"] = data["judge"]
        return self.studio.catalog.save(
            "evaluations",
            {
                "name": _text(data.get("name"), "Evaluation name", 200),
                "requester_nexus_id": actor["nexus_id"] if actor else None,
                "data": {
                    "target_kind": data.get("target_kind"),
                    "target_id": data.get("target_id"),
                    "cases": [case],
                },
            },
        )

    def sharing_enabled(self):
        path = self.studio._path("system", "public-sharing")
        return path.exists() and self.studio._read(path).get("enabled") is True

    def configure(self, data, actor):
        if self.studio.team and (not actor or actor["role"] != "owner"):
            raise PermissionError("Public sharing configuration requires owner role")
        if type(data.get("enabled")) is not bool:
            raise ValueError("Enabled must be true or false")
        record = {"enabled": data["enabled"]}
        with self.studio.lock:
            self.studio._write(self.studio._path("system", "public-sharing"), record)
            if not data["enabled"]:
                for snapshot in self.studio.records("conversation-shares", limit=None):
                    if snapshot.get("public") and not snapshot.get("revoked"):
                        snapshot["revoked"] = True
                        self.studio._write(
                            self.studio._path("conversation-shares", snapshot["id"]), snapshot
                        )
        return record

    def share(self, data, actor):
        session = self.owned(self.studio.session(data.get("session_id")), actor)
        public = data.get("public", False)
        if type(public) is not bool:
            raise ValueError("Public must be true or false")
        if public and not self.sharing_enabled():
            raise ValueError("Owner must enable public sharing first")
        recipients = data.get("recipients", [])
        if (
            not isinstance(recipients, list)
            or len(recipients) > 50
            or any(
                not isinstance(r, str) or not self.studio.team or r not in self.studio.team.bindings
                for r in recipients
            )
        ):
            raise ValueError("Recipients must be existing Nexus workspace accounts")
        ttl = data.get("expires_hours", 168)
        if type(ttl) is not int or not 1 <= ttl <= 8760:
            raise ValueError("Snapshot expiry must be between one hour and one year")
        identities = data.get("artifact_ids", [])
        if not isinstance(identities, list) or len(identities) > 10:
            raise ValueError("Choose up to ten artifacts")
        artifacts = []
        for identity in dict.fromkeys(identities):
            artifact = self.owned(self.studio.catalog.get("artifacts", identity), actor)
            artifacts.append(
                {
                    "id": identity,
                    "revision": artifact["revision"],
                    "filename": artifact["data"]["filename"],
                }
            )
        token = secrets.token_urlsafe(32)
        record = {
            "id": uuid.uuid4().hex,
            "version": 1,
            "owner_id": owner(actor),
            "session_id": session["id"],
            "title": session["title"],
            "public": public,
            "recipients": list(dict.fromkeys(recipients)),
            "revoked": False,
            "token_hash": hashlib.sha256(token.encode()).hexdigest() if public else None,
            "expires_at": (datetime.now(UTC) + timedelta(hours=ttl)).isoformat(),
            "messages": [{"role": m["role"], "content": m["content"]} for m in session["messages"]],
            "artifacts": artifacts,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self.studio.lock:
            self.studio._write(self.studio._path("conversation-shares", record["id"]), record)
        result = {k: v for k, v in record.items() if k != "token_hash"}
        if public:
            result["token"] = token
        return result

    def get_share(self, identity, actor=None, public=False):
        if public:
            if not self.sharing_enabled() or not isinstance(identity, str) or len(identity) != 43:
                raise FileNotFoundError("Shared snapshot not found")
            digest = hashlib.sha256(identity.encode()).hexdigest()
            record = next(
                (
                    r
                    for r in self.studio.records("conversation-shares", limit=None)
                    if r.get("public") and secrets.compare_digest(r.get("token_hash", ""), digest)
                ),
                None,
            )
            if record is None:
                raise FileNotFoundError("Shared snapshot not found")
        else:
            record = self.studio._read(self.studio._path("conversation-shares", identity))
            principal = owner(actor)
            if principal != record["owner_id"] and principal not in record["recipients"]:
                raise PermissionError("Snapshot has not been shared with this account")
        if record["revoked"] or datetime.fromisoformat(record["expires_at"]) <= datetime.now(UTC):
            raise FileNotFoundError("Shared snapshot expired or revoked")
        return {
            k: v
            for k, v in record.items()
            if k not in {"token_hash", "owner_id", "recipients", "session_id"}
        }

    def shared_artifact(self, identity, artifact_id, actor=None, public=False):
        import base64

        snapshot = self.get_share(identity, actor, public)
        ref = next((item for item in snapshot["artifacts"] if item["id"] == artifact_id), None)
        if ref is None:
            raise FileNotFoundError("File is not included in this snapshot")
        record = self.studio.artifacts.revision(artifact_id, ref["revision"])
        return {
            "filename": ref["filename"],
            "content_base64": base64.b64encode(self.studio.artifacts.get(record)).decode(),
        }

    def revoke(self, identity, actor):
        with self.studio.lock:
            path = self.studio._path("conversation-shares", identity)
            record = self.studio._read(path)
            if record["owner_id"] != owner(actor):
                raise PermissionError("Only snapshot creator may revoke it")
            record["revoked"] = True
            self.studio._write(path, record)
        return {"id": identity, "revoked": True}

"""Private, per-run A2A notification hints; recovery uses authoritative GetTask only."""

import hashlib
import json
import secrets
import time
import uuid
from urllib.parse import urlparse

from nexus.core.auth.delegated_credentials import scope_key, seal, unseal

COLLECTION = "external-callbacks"
TERMINAL = {"completed", "failed", "rejected", "canceled", "cancelled"}


def origin(value):
    parsed = urlparse(value or "")
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ValueError("Callback origin requires HTTPS or explicit loopback HTTP")
    return f"{parsed.scheme}://{parsed.netloc}"


class StudioExternalCallbacks:
    def __init__(self, external):
        self.external = external
        self.studio = external.studio

    def path(self, identity):
        return self.studio._path(COLLECTION, identity)

    def scope(self, row):
        return scope_key(
            COLLECTION,
            str(self.studio.root),
            row["id"],
            row["run_id"],
            row["owner_id"],
            row["binding"],
        )

    def actor(self, row):
        return self.studio.workbench.actor({"requester_nexus_id": row["owner_id"]})

    def check(self, row, now):
        if row.get("revoked") or row["expires_at"] <= now:
            raise PermissionError("Callback credential expired or revoked")
        self.actor(row)
        self.external.assert_binding(
            self.external.profile(row["agent_id"]), {"profile_binding": row["binding"]}
        )

    def create(self, record, callback_origin):
        identity, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        row = {
            "id": identity,
            "run_id": record["id"],
            "owner_id": record["owner_id"],
            "agent_id": record["agent_id"],
            "binding": record["profile_binding"],
            "expires_at": time.time() + 86400,
            "status": "listening",
            "seen": [],
        }
        url = origin(callback_origin) + "/api/a2a-callbacks/" + identity
        row["envelope"] = seal(self.scope(row), {"token": token})
        with self.studio.lock:
            self.studio._write(self.path(identity), row)
            record["callback_id"] = identity
            self.studio._write(self.studio._path("external-runs", record["id"]), record)
        return {
            "id": identity,
            "url": url,
            "authentication": {
                **(
                    {"scheme": "Bearer"}
                    if record["card"]["protocol_version"] == "1.0"
                    else {"schemes": ["Bearer"]}
                ),
                "credentials": token,
            },
        }

    def status(self, run_id, actor):
        record = self.studio.workbench.visible(
            self.studio._read(self.studio._path("external-runs", run_id)), actor, own=True
        )
        with self.studio.lock:
            try:
                row = self.studio._read(self.path(record["callback_id"]))
            except (KeyError, FileNotFoundError):
                return {"enabled": False, "status": "off"}
            status = row["status"]
            try:
                self.check(row, time.time())
            except (PermissionError, ValueError, FileNotFoundError):
                status = (
                    "revoked"
                    if row.get("revoked")
                    else "expired"
                    if row["expires_at"] <= time.time()
                    else "unavailable"
                )
            return {
                "enabled": status not in {"revoked", "expired", "unavailable"},
                **{k: row.get(k) for k in ("expires_at", "received_at", "checked_at", "attempts")},
                "status": status,
            }

    def revoke(self, run_id, actor):
        with self.studio.lock:
            record = self.studio.workbench.visible(
                self.studio._read(self.studio._path("external-runs", run_id)), actor, own=True
            )
            if record.get("callback_id"):
                try:
                    row = self.studio._read(self.path(record["callback_id"]))
                except FileNotFoundError:
                    return {"enabled": False, "status": "revoked"}
                row.update(revoked=True, envelope=None, pending=None, flight=None, status="revoked")
                self.studio._write(self.path(row["id"]), row)
        return {"enabled": False, "status": "revoked"}

    def receive(self, identity, authorization, payload, delivery_id=None):
        # Decode only task identity; never persist or execute callback-supplied artifacts.
        try:
            encoded = json.dumps(
                payload, allow_nan=False, sort_keys=True, separators=(",", ":")
            ).encode()
        except (TypeError, ValueError, RecursionError):
            raise ValueError("Invalid callback JSON") from None
        if len(encoded) > 24 * 1024 * 1024 or not isinstance(payload, dict):
            raise ValueError("Invalid callback size or object")
        with self.studio.lock:
            try:
                row = self.studio._read(self.path(identity))
                self.check(row, time.time())
                token = unseal(self.scope(row), row["envelope"])["token"]
                if (
                    not isinstance(authorization, str)
                    or len(authorization) > 512
                    or not secrets.compare_digest(authorization, "Bearer " + token)
                ):
                    raise PermissionError
            except (FileNotFoundError, ValueError, KeyError, PermissionError):
                raise PermissionError("Invalid callback credential") from None
            wrappers = [
                key
                for key in ("task", "statusUpdate", "artifactUpdate", "message")
                if key in payload
            ]
            event = (
                payload[wrappers[0]] if len(wrappers) == 1 else payload if not wrappers else None
            )
            if not isinstance(event, dict):
                raise ValueError("Expected one A2A callback event")
            task = (
                event.get("id")
                if "status" in event and "taskId" not in event
                else event.get("taskId")
            )
            context = event.get("contextId")
            if (
                not isinstance(task, str)
                or not 1 <= len(task) <= 200
                or (context is not None and (not isinstance(context, str) or len(context) > 200))
            ):
                raise ValueError("Callback requires a bounded task identity")
            record = self.studio._read(self.studio._path("external-runs", row["run_id"]))
            if record.get("task_id") and (
                task != record["task_id"]
                or (
                    record.get("context_id")
                    and context is not None
                    and context != record["context_id"]
                )
            ):
                raise ValueError("Callback task or context does not match")
            digest = hashlib.sha256(encoded).hexdigest()
            delivery_id = digest if delivery_id is None else delivery_id
            if not isinstance(delivery_id, str) or not 1 <= len(delivery_id) <= 200:
                raise ValueError("Invalid delivery identifier")
            old = next((item for item in row["seen"] if item[0] == delivery_id), None)
            if old:
                if old[1] != digest:
                    raise ValueError("Delivery identifier reused with different content")
                return {"accepted": True, "duplicate": True}
            row["seen"] = (row["seen"] + [[delivery_id, digest]])[-128:]
            row.update(
                pending={"digest": digest, "task_id": task, "context_id": context},
                received_at=time.time(),
                attempts=0,
                next_due=0,
                status="pending",
            )
            self.studio._write(self.path(identity), row)
            return {"accepted": True, "duplicate": False}

    def tick(self, now=None):
        now = time.time() if now is None else now
        claim = None
        with self.studio.lock:
            for row in self.studio.records(COLLECTION, limit=None):
                if row["expires_at"] <= now and row.get("envelope"):
                    row.update(envelope=None, revoked=True, pending=None, status="expired")
                    self.studio._write(self.path(row["id"]), row)
                if (
                    not row.get("pending")
                    or row.get("next_due", 0) > now
                    or (row.get("flight") and row["flight"]["until"] > now)
                ):
                    continue
                try:
                    self.check(row, now)
                    record = self.studio._read(self.studio._path("external-runs", row["run_id"]))
                    run = self.studio.run(row["run_id"])
                    if (
                        run.get("cancel_requested")
                        or run["status"] == "cancelled"
                        or record["status"] in TERMINAL
                    ):
                        row.update(pending=None, status="ignored")
                    elif (
                        run["status"] in {"queued", "running"}
                        or record["status"] == "continuation-queued"
                    ):
                        row.update(next_due=now + 2)
                    elif not record.get("task_id"):
                        row.update(pending=None, status="awaiting-acknowledgement")
                    elif row["pending"]["task_id"] != record["task_id"] or (
                        record.get("context_id")
                        and row["pending"].get("context_id") not in {None, record["context_id"]}
                    ):
                        row.update(pending=None, status="identity-mismatch")
                    elif row.get("attempts", 0) >= 3:
                        row.update(pending=None, status="read-failed")
                    else:
                        flight = {"id": uuid.uuid4().hex, "until": now + 60}
                        row.update(
                            flight=flight, attempts=row.get("attempts", 0) + 1, status="reading"
                        )
                        claim = (dict(row), self.actor(row))
                except (PermissionError, ValueError, FileNotFoundError):
                    row.update(pending=None, status="unavailable")
                self.studio._write(self.path(row["id"]), row)
                if claim:
                    break
        if not claim:
            return
        row, actor = claim
        try:
            self.external.refresh_task(row["run_id"], actor, callback=row)
            success = True
        except Exception:
            success = False  # Never persist provider errors, which may contain credentials.
        with self.studio.lock:
            current = self.studio._read(self.path(row["id"]))
            if current.get("revoked") or current.get("flight") != row["flight"]:
                return
            current.update(flight=None, checked_at=now)
            if current.get("pending") != row["pending"]:
                current.update(status="pending", next_due=0)
            elif success:
                current.update(pending=None, status="verified")
            elif current["attempts"] >= 3:
                current.update(pending=None, status="read-failed")
            else:
                current.update(status="retrying", next_due=now + 5 * current["attempts"])
            self.studio._write(self.path(row["id"]), current)

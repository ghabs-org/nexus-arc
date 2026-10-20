"""Opt-in authenticated A2A snapshots on Studio's existing state/scheduler.

Exact callback URLs require entrypoint-owner configuration. Delivery is at least
once, bounded to three attempts; receivers deduplicate X-Nexus-Delivery-ID.
No task or tool is resubmitted by this service. Transient states may coalesce.
"""

import json
import os
import re
import time
import uuid
from urllib.parse import urlparse

from nexus.core.auth.delegated_credentials import scope_key, seal, unseal
from nexus.core.studio_a2a_server import A2AError
from nexus.core.studio_remote import validate_destination, validate_remote


def settings(value):
    if not isinstance(value, dict) or set(value) - {"destinations", "allow_private"}:
        raise ValueError("A2A push settings require preapproved callback destinations")
    destinations = value.get("destinations", [])
    private = value.get("allow_private", False)
    if not isinstance(destinations, list) or len(destinations) > 10:
        raise ValueError("Approve at most ten exact callback URLs")
    for url in destinations:
        validate_remote({"url": url, "allow_private": private})
        if url.endswith("/"):
            raise ValueError("Callback destinations must not end in a slash")
        if urlparse(url).scheme != "https" and urlparse(url).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("Plain HTTP callbacks require loopback and explicit private allowance")
    if type(private) is not bool:
        raise ValueError("Private allowance must be a boolean")
    return {"destinations": list(dict.fromkeys(destinations)), "allow_private": private}


class StudioA2APush:
    def __init__(self, studio):
        self.studio = studio

    def health(self, identity, actor, *, limit=50, after=None):
        """Read-only, owner-scoped projection; never decrypt delivery payloads."""
        record = self.studio.catalog.get("entrypoints", identity, archived=True)
        self.studio.workbench.visible(record, actor, own=True)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Delivery health limit must be between 1 and 100")
        with self.studio.lock:
            rows = sorted(
                [
                    row
                    for row in self.studio.records("a2a-push", limit=None)
                    if row["entrypoint_id"] == identity
                    and row.get("owner_id") == record.get("owner_id")
                ],
                key=lambda row: row["id"],
            )
            offset = 0
            if after:
                found = next((i for i, row in enumerate(rows) if row["id"] == after), None)
                if found is None:
                    raise ValueError("Invalid delivery health cursor")
                offset = found + 1
            ready = self.enabled(record)
            items = []
            for row in rows[offset : offset + limit]:
                status = row["status"]
                if (
                    record.get("archived")
                    or not record["data"].get("enabled")
                    or not record["data"].get("a2a_enabled")
                ):
                    status = "disabled"
                elif row["binding"] != self.binding(record):
                    status = "configuration_changed"
                elif not ready:
                    status = "unavailable"
                items.append(
                    {
                        "id": row["id"],
                        "task_id": row["task_id"],
                        "config_id": row["config_id"],
                        "status": status,
                        "last_status": row["status"],
                        "attempt": (row.get("flight") or {}).get(
                            "attempt", row.get("attempts", 0 if row["status"] == "pending" else None)
                        ),
                        "max_attempts": 3,
                        "checked_at": row.get("checked_at"),
                        "next_due": row.get("next_due") if row.get("flight") else None,
                    }
                )
            return {
                "version": 1,
                "entrypoint_id": identity,
                "ready": ready
                and record["data"].get("enabled", False)
                and record["data"].get("a2a_enabled", False)
                and not record.get("archived", False),
                "items": items,
                "total": len(rows),
                "next_cursor": items[-1]["id"] if items and offset + limit < len(rows) else None,
            }

    @staticmethod
    def binding(record):
        return scope_key(
            record["id"],
            record.get("owner_id"),
            {k: record["data"].get(k) for k in ("target_kind", "target_id", "a2a_push")},
        )

    def enabled(self, record):
        if not record["data"].get("a2a_push", {}).get("destinations"):
            return False
        try:
            import httpx  # noqa: F401 -- optional capability readiness

            seal("a2a-push-readiness", {})
            return True
        except (ImportError, RuntimeError, ValueError):
            return False

    def validate(self, record, configuration, version, *, sending=False):
        if not self.enabled(record):
            raise A2AError(-32003, "Push requires preapproved entrypoint callback destinations")
        if not isinstance(configuration, dict):
            raise A2AError(-32602, "Push configuration must be an object")
        value = dict(configuration)
        if version == "0.3":
            value = dict(value.get("pushNotificationConfig", value))
        if set(value) - {"id", "taskId", "url", "token", "authentication", "tenant"}:
            raise A2AError(-32602, "Unsupported push configuration fields")
        if sending and value.get("taskId"):
            raise A2AError(-32602, "SendMessage push configuration must not name a task")
        if value.get("tenant"):
            raise A2AError(-32004, "Use the entrypoint URL to select the agent")
        url = value.get("url")
        policy = record["data"]["a2a_push"]
        if url not in policy["destinations"]:
            raise A2AError(-32602, "Callback URL is not preapproved for this entrypoint")
        auth = value.get("authentication")
        if isinstance(auth, dict) and version == "0.3" and "schemes" in auth:
            auth = {
                "scheme": "Bearer" if auth["schemes"] == ["Bearer"] else "",
                "credentials": auth.get("credentials"),
            }
        if (
            not isinstance(auth, dict)
            or set(auth) != {"scheme", "credentials"}
            or str(auth.get("scheme", "")).lower() != "bearer"
        ):
            raise A2AError(-32602, "Callback authentication requires Bearer credentials")
        credential = auth.get("credentials")
        if not isinstance(credential, str) or not re.fullmatch(
            r"[A-Za-z0-9._~+/=-]{1,4096}", credential
        ):
            raise A2AError(-32602, "Invalid callback bearer credential")
        identifier = value.get("id") or uuid.uuid4().hex
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", identifier):
            raise A2AError(-32602, "Invalid push configuration ID")
        token = value.get("token", "")
        if (
            not isinstance(token, str)
            or len(token) > 256
            or any(not 32 <= ord(c) <= 126 for c in token)
        ):
            raise A2AError(-32602, "Invalid callback correlation token")
        normalized = {
            "id": identifier,
            "url": url,
            "authentication": {"scheme": "Bearer", "credentials": credential},
        }
        if token:
            normalized["token"] = token
        # Fail before task dispatch if encryption is unavailable.
        seal("a2a-push-readiness", {})
        return normalized

    def key(self, record, task, identifier):
        return scope_key(
            str(self.studio.root),
            self.studio.team.project_key if self.studio.team else "desktop",
            record["id"],
            record.get("owner_id"),
            task,
            identifier,
        )

    @staticmethod
    def public(row, config, version):
        value = {k: v for k, v in config.items() if k != "authentication"}
        value["authentication"] = (
            {"scheme": "Bearer"} if version == "1.0" else {"schemes": ["Bearer"]}
        )
        if version == "0.3":
            return {"taskId": row["task_id"], "pushNotificationConfig": value}
        return {**value, "taskId": row["task_id"]}

    def create(self, record, task, config, version):
        self.studio.a2a.task(record, task, version)
        key = self.key(record, task, config["id"])
        path = self.studio._path("a2a-push", key)
        with self.studio.lock:
            if path.exists():
                row = self.studio._read(path)
                existing = unseal(key, row["envelope"])
                if row["binding"] != self.binding(record) or existing != config:
                    raise A2AError(
                        -32602, "Push ID already configured; delete it before replacement"
                    )
                return self.public(row, existing, version)
            rows = [r for r in self.studio.records("a2a-push", limit=None) if r["task_id"] == task]
            if len(rows) >= 10:
                raise A2AError(-32602, "A task supports at most ten callback configurations")
            row = {
                "id": key,
                "entrypoint_id": record["id"],
                "owner_id": record.get("owner_id"),
                "task_id": task,
                "config_id": config["id"],
                "binding": self.binding(record),
                "envelope": seal(key, config),
                "status": "pending",
            }
            self.studio._write(path, row)
            return self.public(row, config, version)

    def invoke(self, record, operation, params, version):
        if not self.enabled(record):
            raise A2AError(-32003, "Push requires preapproved entrypoint callback destinations")
        task = params.get("taskId") if version == "1.0" else params.get("taskId", params.get("id"))
        self.studio.a2a.task(record, task, version)
        if operation == "push-create":
            config = self.validate(record, params, version)
            return self.create(record, task, config, version)
        if operation == "push-list":
            size = params.get("pageSize", 50)
            token = params.get("pageToken", "0") or "0"
            if (
                type(size) is not int
                or not 1 <= size <= 100
                or not isinstance(token, str)
                or not re.fullmatch(r"[0-9]{1,7}", token)
            ):
                raise A2AError(-32602, "Invalid callback page size or token")
            rows = sorted(
                [
                    r
                    for r in self.studio.records("a2a-push", limit=None)
                    if r["entrypoint_id"] == record["id"]
                    and r["task_id"] == task
                    and r["binding"] == self.binding(record)
                ],
                key=lambda r: r["config_id"],
            )
            offset = int(token)
            values = [
                self.public(r, unseal(r["id"], r["envelope"]), version)
                for r in rows[offset : offset + size]
            ]
            return (
                values
                if version == "0.3"
                else {
                    "configs": values,
                    "nextPageToken": str(offset + size) if offset + size < len(rows) else "",
                }
            )
        identifier = (
            params.get("id") if version == "1.0" else params.get("pushNotificationConfigId")
        )
        key = self.key(record, task, identifier)
        path = self.studio._path("a2a-push", key)
        with self.studio.lock:
            if not path.exists():
                raise A2AError(-32001, "Push configuration not found")
            row = self.studio._read(path)
            if row["binding"] != self.binding(record):
                raise A2AError(-32001, "Push configuration not found")
            if operation == "push-delete":
                path.unlink()
                return {}
            return self.public(row, unseal(key, row["envelope"]), version)

    def tick(self, now=None):
        """Claim one delivery per scheduler tick; commit claim before HTTP I/O."""
        now = time.time() if now is None else now
        claim = None
        with self.studio.lock:
            rows = sorted(
                self.studio.records("a2a-push", limit=None), key=lambda r: r.get("checked_at", 0)
            )
            for row in rows:
                if row.get("next_due", 0) > now:
                    continue
                path = self.studio._path("a2a-push", row["id"])
                try:
                    record = self.studio.catalog.get("entrypoints", row["entrypoint_id"])
                    self.studio.a2a.authorize(
                        record["id"], os.getenv(record["data"]["token_env"], "")
                    )
                    if self.binding(record) != row["binding"] or not self.enabled(record):
                        raise PermissionError
                    config = unseal(row["id"], row["envelope"])
                    task = self.studio.a2a.task(record, row["task_id"], "1.0")
                    digest = scope_key(task)
                    flight = row.get("flight")
                    if not flight and digest == row.get("last_digest"):
                        continue
                    if not flight:
                        flight = {
                            "id": uuid.uuid4().hex,
                            "digest": digest,
                            "attempt": 0,
                            "payload": seal(row["id"], {"task": task}),
                        }
                    flight["attempt"] += 1
                    if flight["attempt"] > 3:
                        row.update(
                            status="failed",
                            last_digest=flight["digest"],
                            flight=None,
                            checked_at=now,
                        )
                        self.studio._write(path, row)
                        continue
                    receipt = uuid.uuid4().hex
                    row.update(
                        status="delivering",
                        attempts=flight["attempt"],
                        receipt=receipt,
                        flight=flight,
                        next_due=now + 60,
                        checked_at=now,
                    )
                    self.studio._write(path, row)
                    claim = (row, record, config, receipt)
                    break
                except (ValueError, PermissionError, FileNotFoundError, RuntimeError):
                    row.update(status="blocked", next_due=now + 60, checked_at=now)
                    self.studio._write(path, row)
        if claim is None:
            return
        row, record, config, receipt = claim
        status = "failed"
        try:
            import httpx

            # Recheck revocation after committing the claim and before dispatch.
            current = self.studio.a2a.authorize(
                record["id"], os.getenv(record["data"]["token_env"], "")
            )
            if self.binding(current) != row["binding"]:
                raise PermissionError
            validate_destination(config["url"], record["data"]["a2a_push"]["allow_private"])
            payload = unseal(row["id"], row["flight"]["payload"])
            headers = {
                "Authorization": "Bearer " + config["authentication"]["credentials"],
                "Content-Type": "application/a2a+json",
                "X-Nexus-Delivery-ID": row["flight"]["id"],
                "A2A-Version": "1.0",
            }
            if config.get("token"):
                headers["X-A2A-Notification-Token"] = config["token"]
            # DNS and decryption may have yielded to deletion/re-registration.
            # A replacement configuration must never authorize an older claim.
            current = self.studio.a2a.authorize(
                record["id"], os.getenv(record["data"]["token_env"], "")
            )
            with self.studio.lock:
                path = self.studio._path("a2a-push", row["id"])
                if (
                    self.binding(current) != row["binding"]
                    or not path.exists()
                    or self.studio._read(path).get("receipt") != receipt
                ):
                    return
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                with client.stream(
                    "POST", config["url"], headers=headers, content=json.dumps(payload).encode()
                ) as response:
                    status = "delivered" if 200 <= response.status_code < 300 else "retrying"
        except Exception:
            status = "retrying"
        with self.studio.lock:
            path = self.studio._path("a2a-push", row["id"])
            if not path.exists():
                return
            current = self.studio._read(path)
            if current.get("receipt") != receipt:
                return
            if status == "delivered" or row["flight"]["attempt"] >= 3:
                current.update(
                    status=status if status == "delivered" else "failed",
                    last_digest=row["flight"]["digest"],
                    flight=None,
                    next_due=now,
                )
            else:
                current.update(
                    status="retrying", next_due=now + 5 * 2 ** (row["flight"]["attempt"] - 1)
                )
            self.studio._write(path, current)

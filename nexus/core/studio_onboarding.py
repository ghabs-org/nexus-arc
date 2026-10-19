"""Read-only connection checks, bound to the reviewed configuration revision."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime


class StudioOnboarding:
    def __init__(self, studio):
        self.studio = studio

    @staticmethod
    def fingerprint(record):
        return hashlib.sha256(
            json.dumps(
                {
                    "id": record["id"],
                    "revision": record["revision"],
                    "data": record["data"],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def owner(self, actor):
        if self.studio.team and (not actor or actor["role"] != "owner"):
            raise PermissionError("Connection setup requires the owner role")
        return self.studio.workbench.owner(actor)

    def probe(self, identity, payload, actor):
        self.owner(actor)
        record = self.studio.catalog.get("connectors", identity)
        if payload.get("revision") != record["revision"]:
            raise ValueError("Connection settings changed. Review them again")
        kind = record["data"]["type"]
        action = (
            "discover"
            if kind == "mcp"
            else "list_issues"
            if kind in {"github", "gitlab"}
            else "status"
        )
        # Never accept a browser-selected action or arguments during onboarding.
        self.studio.operations.call_connector(
            identity, {"action": action}, self.owner(actor), probe=True
        )
        with self.studio.lock:
            latest = self.studio.catalog.get("connectors", identity)
            if self.fingerprint(record) != self.fingerprint(latest):
                raise ValueError("Connection changed during the check. Check it again")
            result = {
                "id": identity,
                "owner_id": self.owner(actor),
                "digest": self.fingerprint(record),
                "revision": record["revision"],
                "checked_at": datetime.now(UTC).isoformat(),
                "authorization_verified": kind not in {"telegram", "slack", "discord", "openclaw"},
                "message": "Credentials are configured; messaging authorization is checked on use. No message was sent."
                if kind in {"telegram", "slack", "discord", "openclaw"}
                else "Read-only connection check succeeded. No write action was attempted.",
            }
            self.studio._write(self.studio._path("connection-checks", identity), result)
            return result

    def enable(self, identity, payload, actor):
        owner = self.owner(actor)
        with self.studio.lock:
            record = self.studio.catalog.get("connectors", identity)
            check = self.studio._read(self.studio._path("connection-checks", identity))
            if (
                check.get("owner_id") != owner
                or payload.get("digest") != check["digest"]
                or check["digest"] != self.fingerprint(record)
            ):
                raise ValueError(
                    "Run the connection check for these settings and your account again"
                )
            if payload.get("confirm") is not True:
                raise ValueError("Confirm enabling this connection")
            return self.studio.catalog.save(
                "connectors",
                {
                    "id": identity,
                    "name": record["name"],
                    "description": record["description"],
                    "revision": record["revision"],
                    "requester_nexus_id": owner,
                    "data": {**record["data"], "enabled": True},
                },
            )

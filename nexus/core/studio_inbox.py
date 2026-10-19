"""Live, owner-scoped review queue; decisions use existing guarded operations."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime


class StudioInbox:
    def __init__(self, studio):
        self.studio = studio

    def items(self, actor):
        workbench = self.studio.workbench
        workbench.owner(actor)
        items = []

        def add(kind, record, title, actions, link, detail):
            owner = record.get("owner_id", record.get("requester_nexus_id"))
            permitted = not self.studio.team or (
                owner == actor["nexus_id"] and actor["role"] != "viewer"
            )
            digest = hashlib.sha256(
                json.dumps(detail, sort_keys=True, default=str).encode()
            ).hexdigest()
            items.append(
                {
                    "id": kind + "-" + record["id"],
                    "kind": kind,
                    "source_id": record["id"],
                    "title": title,
                    "owner_id": owner,
                    "status": record["status"],
                    "updated_at": record.get("updated_at", record.get("created_at", "")),
                    "digest": digest,
                    "actions": actions if permitted else [],
                    "link": link,
                    "detail": detail,
                }
            )

        for run in workbench.records("runs", actor):
            if run["status"] == "waiting" and run.get("awaiting_approval"):
                paths = [self.studio._path(kind, run["id"]) for kind in ("graph-runs", "yaml-runs")]
                saved = next((self.studio._read(path) for path in paths if path.exists()), {})
                add(
                    "gate",
                    run,
                    run["task"],
                    ["approve", "reject"],
                    "#activity",
                    {
                        "run": run,
                        "workflow": saved.get("document", saved.get("workflow", saved)),
                    },
                )
            elif run["status"] == "waiting" and run["kind"] == "external":
                add("remote", run, run["task"], [], "#externalagents", {"run": run})
            elif run["status"] in {"failed", "interrupted"} and run["kind"] == "workflow":
                add("recovery", run, run["task"], [], "#workbench/checkpoints", {"run": run})
        for effect in workbench.records("integration-effects", actor):
            if effect["status"] not in {"completed", "reviewed-retry"}:
                run = self.studio.run(effect["run_id"])
                actions = [] if run["status"] in {"running", "queued"} else ["completed", "retry"]
                add(
                    "effect",
                    effect,
                    effect["connector_id"] + " · " + effect["action"],
                    actions,
                    "#workbench/controls",
                    {"effect": effect, "run": run},
                )
        for campaign in workbench.records("campaigns", actor):
            if campaign["status"] == "draft" and campaign.get("validation", {}).get("passed"):
                add(
                    "campaign",
                    campaign,
                    campaign["name"],
                    ["approve", "reject"],
                    "#workbench/campaigns",
                    {"campaign": campaign},
                )
            elif campaign["status"] in {"approved", "uncertain"}:
                add(
                    "campaign",
                    campaign,
                    campaign["name"],
                    [],
                    "#workbench/campaigns",
                    {"campaign": campaign},
                )
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def review(self, identity, payload, actor):
        with self.studio.lock:
            item = next((item for item in self.items(actor) if item["id"] == identity), None)
            if item is None:
                raise FileNotFoundError("Review item was resolved or is not visible")
            if payload.get("digest") != item["digest"]:
                raise ValueError("Review state changed. Open and review it again")
            action = payload.get("decision")
            if action not in item["actions"]:
                raise PermissionError("This decision is not available for your account")
            if payload.get("confirm") is not True:
                raise ValueError("Review the details and confirm this decision")
            notes = payload.get("notes", "")
            if not isinstance(notes, str) or len(notes) > 2000:
                raise ValueError("Review notes must be text up to 2000 characters")
            if self.studio.team:
                self.studio.team.assert_execution(actor["nexus_id"])
            if item["kind"] == "gate":
                result = self.studio.graph.resume(item["source_id"], action)
            elif item["kind"] == "campaign":
                result = self.studio.workbench.campaigns.approve(
                    item["source_id"],
                    {
                        "revision": item["detail"]["campaign"]["revision"],
                        "notes": notes,
                    },
                    actor,
                    action == "approve",
                )
            else:
                from nexus.core.studio_nodes import StudioNodeTypes

                result = StudioNodeTypes(self.studio).resolve(
                    item["source_id"],
                    {"outcome": action, "confirm": True, "output": notes},
                    actor,
                )
            record = {
                "id": uuid.uuid4().hex,
                "owner_id": item["owner_id"],
                "reviewer": actor["nexus_id"] if actor else "desktop",
                "item_id": identity,
                "decision": action,
                "digest": item["digest"],
                "notes": notes,
                "created_at": datetime.now(UTC).isoformat(),
            }
            self.studio._write(self.studio._path("review-decisions", record["id"]), record)
            return result

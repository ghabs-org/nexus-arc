"""Calendar lifecycle over the existing catalog, workspace lock and job queue."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from nexus.core.studio_calendar import next_occurrence
from nexus.core.studio_operations import _timestamp


def cadence(data):
    return hashlib.sha256(
        json.dumps(
            {
                **{key: data.get(key) for key in ("run_at", "cron", "interval_seconds")},
                "timezone": data.get("timezone", "UTC") if data.get("cron") else None,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


class StudioSchedules:
    def __init__(self, studio):
        self.studio = studio

    def state(self, item):
        path = self.studio._path("schedule-state", item["id"])
        state = self.studio._read(path) if path.exists() else {}
        signature = state.get("cadence")
        if state and signature is None:
            # Old records are upgraded from their immutable catalog revision.
            prior = (
                item
                if state.get("resource_revision") == item["revision"]
                else next(
                    (
                        v
                        for v in self.studio.catalog.history("schedules", item["id"])
                        if v["revision"] == state.get("resource_revision")
                    ),
                    None,
                )
            )
            signature = cadence(prior["data"]) if prior else cadence(item["data"])
            if prior is None:
                # Missing history cannot justify replaying an old date. Keep the
                # recorded progress and expose the uncertainty for review.
                state["legacy_timing_unverified"] = True
        if not state or signature != cadence(item["data"]):
            state = {
                "id": item["id"],
                "next_run_at": item["data"]["run_at"],
                # Retain the no-overlap guard even when cadence changes.
                **({"run_id": state["run_id"]} if state.get("run_id") else {}),
            }
        return {
            **state,
            "version": 1,
            "resource_revision": item["revision"],
            "cadence": cadence(item["data"]),
        }

    def details(self, identity, now=None):
        now = now or datetime.now(UTC)
        with self.studio.lock:
            item = self.studio.catalog.get("schedules", identity, archived=True)
            state = self.state(item)
            data = item["data"]
            upcoming, occurrence = [], state.get("next_run_at")
            if occurrence:
                upcoming.append(occurrence)
                for _ in range(4):
                    after = max(now, _timestamp(upcoming[-1]))
                    if data.get("cron"):
                        upcoming.append(
                            next_occurrence(data["cron"], data["timezone"], after).isoformat()
                        )
                    elif data["interval_seconds"]:
                        upcoming.append(
                            (after + timedelta(seconds=data["interval_seconds"])).isoformat()
                        )
                    else:
                        break
            events = [
                r
                for r in self.studio.records("schedule-occurrences", limit=None)
                if r["schedule_id"] == identity
            ]
            events.sort(key=lambda r: (r["claimed_at"], r["id"]), reverse=True)
            for event in events[:100]:
                if event.get("run_id"):
                    try:
                        event["status"] = self.studio.run(event["run_id"])["status"]
                    except FileNotFoundError:
                        event["status"] = "run-unavailable"
            return {
                "version": 1,
                "schedule": item,
                "state": state,
                "upcoming": upcoming,
                "occurrences": events[:100],
                "older_occurrences": max(0, len(events) - 100),
            }

    def enabled(self, identity, payload):
        if type(payload.get("enabled")) is not bool:
            raise ValueError("Enabled must be true or false")
        with self.studio.lock:
            item = self.studio.catalog.get("schedules", identity)
            if type(payload.get("revision")) is not int or payload["revision"] != item["revision"]:
                raise ValueError("Schedule changed; reopen its current revision")
            normalized = self.state(item)
            saved = self.studio.catalog.save(
                "schedules",
                {
                    "id": identity,
                    "revision": item["revision"],
                    "name": item["name"],
                    "description": item["description"],
                    "data": {**item["data"], "enabled": payload["enabled"]},
                },
            )
            self.studio._write(self.studio._path("schedule-state", identity), normalized)
            return saved

    def tick(self, now=None):
        now = now or datetime.now(UTC)
        if not isinstance(now, datetime) or now.utcoffset() is None:
            raise ValueError("Schedule dates must include a timezone")
        now = now.astimezone(UTC)
        for candidate in self.studio.catalog.list("schedules"):
            with self.studio.lock:
                # The listing can predate a pause/edit in another worker.
                item = self.studio.catalog.get("schedules", candidate["id"], archived=True)
                data = item["data"]
                if item["archived"] or not data["enabled"]:
                    continue
                try:
                    self.studio.resource_lifecycle.assert_launch("schedules:" + item["id"])
                except (ValueError, FileNotFoundError):
                    # Preserve the due time until its reviewed build can activate.
                    continue
                state = self.state(item)
                if not state.get("next_run_at") or _timestamp(state["next_run_at"]) > now:
                    continue
                if state.get("run_id"):
                    if self.studio.run(state["run_id"])["status"] in {
                        "queued",
                        "running",
                        "waiting",
                    }:
                        continue
                due = state["next_run_at"]
                if data.get("cron"):
                    next_run = next_occurrence(data["cron"], data["timezone"], now).isoformat()
                else:
                    next_run = (
                        (now + timedelta(seconds=data["interval_seconds"])).isoformat()
                        if data["interval_seconds"]
                        else None
                    )
                occurrence = {
                    "version": 1,
                    "id": uuid.uuid4().hex,
                    "schedule_id": item["id"],
                    "schedule_revision": item["revision"],
                    "owner_id": item.get("owner_id"),
                    "due_at": due,
                    "claimed_at": now.isoformat(),
                    "status": "claimed",
                }
                path = self.studio._path("schedule-state", item["id"])
                state.update(
                    next_run_at=next_run,
                    claimed_at=now.isoformat(),
                    updated_at=now.isoformat(),
                    last_error=None,
                )
                # SQL holds state, occurrence and enqueue in one workspace transaction.
                # File state claims before dispatch; a crash never auto-replays an
                # unconfirmed external effect. Its claimed event remains inspectable.
                self.studio._write(path, state)
                event_path = self.studio._path("schedule-occurrences", occurrence["id"])
                self.studio._write(event_path, occurrence)
                try:
                    run = self.studio.operations.launch_target(
                        data, data["task"], item.get("owner_id")
                    )
                    state["run_id"] = occurrence["run_id"] = run["id"]
                    occurrence["status"] = "queued"
                except (ValueError, OSError, PermissionError):
                    state["last_error"] = (
                        "Schedule dispatch failed; review target, prompt values and account access"
                    )
                    occurrence["status"] = "dispatch-failed"
                self.studio._write(event_path, occurrence)
                self.studio._write(path, state)

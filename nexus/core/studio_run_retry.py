"""Explicit Activity retries over saved requests and existing workflow checkpoints."""

from __future__ import annotations

import copy
import hashlib
import json

REPEATABLE = {"chat", "build", "design", "tool"}


class StudioRunRetry:
    def __init__(self, studio):
        self.studio = studio

    def remember(self, run_id, payload, session_id):
        if not self.studio.sql_state and payload.get("kind", "chat") in REPEATABLE:
            self.studio._write(
                self.studio._path("run-requests", run_id),
                {
                    "payload": payload,
                    "session_id": session_id,
                },
            )

    def request(self, run_id):
        if self.studio.sql_state:
            return self.studio.sql_state.job_request(run_id)
        path = self.studio._path("run-requests", run_id)
        return self.studio._read(path) if path.exists() else None

    def workflow_state(self, run_id):
        for collection in ("graph-runs", "yaml-runs"):
            path = self.studio._path(collection, run_id)
            if path.exists():
                return path, self.studio._read(path)
        return None, None

    def describe(self, run_id, actor=None):
        studio = self.studio
        with studio.lock:
            run = studio.collaboration.owned(studio.run(run_id), actor)
            path, state = self.workflow_state(run_id)
            request = self.request(run_id) if state is None else None
            blocked = ""
            if run["status"] not in {"failed", "interrupted"}:
                blocked = "Only failed or interrupted runs can be retried."
            elif run.get("cancel_requested") or run.get("cancellation_cleanup"):
                blocked = "Use the existing cancellation cleanup controls for this run."
            elif studio.team and (not actor or actor["nexus_id"] != run.get("requester_nexus_id")):
                blocked = "Only the account that started this run can retry it."
            elif studio.team and actor["role"] == "viewer":
                blocked = "Execution access is required to retry this run."
            elif state is None and (run["kind"] not in REPEATABLE or request is None):
                blocked = (
                    "No resumable checkpoint or saved retry request is available for this run."
                )
            elif (
                run["kind"] == "chat"
                and request
                and request.get("session_id")
                and studio._path("sessions", request["session_id"]).exists()
                and studio.session(request["session_id"]).get("archived")
            ):
                blocked = "Restore this archived conversation before retrying."

            latest = {}
            expected_owner = run.get("requester_nexus_id") or "desktop"
            traces = [
                row
                for row in studio.records("node-traces", limit=None)
                if row.get("run_id") == run_id
                and (row.get("owner_id") or "desktop") == expected_owner
            ]
            trace_ids = {row["id"]: row["node_id"] for row in traces}
            # Canonical events retain retry ordering even when legacy trace rows are overwritten.
            rows = [
                row
                for row in studio.records("execution-events", limit=None)
                if row.get("run_id") == run_id
                and row.get("kind") == "node"
                and (row.get("owner_id") or "desktop") == expected_owner
            ]
            for row in sorted(rows, key=lambda item: item.get("sequence", 0)):
                if row.get("node_id"):
                    identity = trace_ids.get(row.get("source_id"), row["node_id"])
                    if identity != row["node_id"]:
                        latest.pop(row["node_id"], None)
                    latest[identity] = row
            for row in traces:
                latest.setdefault(row["node_id"], {**row, "phase": row.get("kind")})
            steps = []
            if state is not None:
                for identity, row in latest.items():
                    if row.get("status") not in {"failed", "interrupted", "cancelled"}:
                        continue
                    if state.get("nodes", {}).get(identity) in {"completed", "skipped"}:
                        continue
                    steps.append(
                        {
                            "id": identity,
                            "name": row.get("node_name") or identity,
                            "kind": row.get("phase", "step"),
                        }
                    )
                if not steps:
                    steps.append(
                        {
                            "id": "__workflow__",
                            "name": "Continue saved workflow",
                            "kind": "workflow",
                        }
                    )
            elif run["kind"] in REPEATABLE and request:
                steps.append(
                    {
                        "id": "__run__",
                        "name": {
                            "build": "Generate build",
                            "chat": "Answer message",
                            "design": "Generate draft",
                            "tool": "Run tool",
                        }[run["kind"]],
                        "kind": run["kind"],
                    }
                )

            effects = [
                row
                for row in studio.records("integration-effects", limit=None)
                if row.get("run_id") == run_id
                and (row.get("owner_id") or "desktop") == expected_owner
                and row.get("status") not in {"completed", "reviewed-retry"}
            ]
            if effects:
                blocked = (
                    "Review the uncertain external action under Workspace tools before retrying."
                )
            # Continuing a failed parallel branch can also revisit unfinished sibling tools.
            unfinished_tools = state is not None and any(
                item.get("phase") == "tool"
                and state.get("nodes", {}).get(key) not in {"completed", "skipped"}
                for key, item in latest.items()
            )
            for step in steps:
                step["requires_effect_review"] = step["kind"] == "tool" or bool(unfinished_tools)
            digest = hashlib.sha256(
                json.dumps(
                    {
                        "run": [run["id"], run["status"], run.get("updated_at")],
                        "state": state,
                        "request": request,
                        "steps": steps,
                        "effects": effects,
                        "attempts": run.get("step_retries", []),
                    },
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            return {
                "version": 1,
                "run_id": run_id,
                "digest": digest,
                "can_retry": not blocked and bool(steps),
                "blocked_reason": blocked,
                "steps": steps,
                "continues_workflow": state is not None,
            }

    @staticmethod
    def reset_failure(state, selected):
        """Clear failed control receipts, retaining every acknowledged output and approval."""

        def related(key):
            return (
                selected == "__workflow__"
                or key == selected
                or key.startswith(selected + "/")
                or selected.startswith(key + "/")
            )

        if state.get("outcome", {}).get("status") == "failed" and (
            selected == "__workflow__" or selected.startswith("root/")
        ):
            state.pop("outcome", None)
        for scope, outcome in list(state.get("scope_outcomes", {}).items()):
            if outcome.get("status") == "failed" and related(scope):
                state["scope_outcomes"].pop(scope)
        for key, attempt in list(state.get("retry_attempts", {}).items()):
            if (
                related(key)
                and attempt.get("terminal") != "completed"
                and state.get("nodes", {}).get(key) != "completed"
            ):
                state["retry_attempts"].pop(key)
        for scope in list(state.get("failed_nodes", {})):
            if related(scope):
                state["failed_nodes"].pop(scope)
        for scope in list(state.get("exit_failures", {})):
            if related(scope):
                state["exit_failures"].pop(scope)

    def queue(self, run_id, payload, actor=None):
        studio = self.studio
        with studio.lock:
            if "confirm_effects" in payload and type(payload["confirm_effects"]) is not bool:
                raise ValueError("Effect review confirmation must be true or false.")
            run = studio.collaboration.owned(studio.run(run_id), actor)
            studio.workbench.visible(run, actor, own=True)
            if studio.team:
                studio.team.assert_execution(run.get("requester_nexus_id"))
            # A repeated submission returns its original receipt, never another dispatch.
            for receipt in run.get("step_retries", []):
                if receipt["digest"] == payload.get("digest") and receipt["step_id"] == payload.get(
                    "step_id"
                ):
                    return run
            plan = self.describe(run_id, actor)
            if not plan["can_retry"]:
                raise ValueError(plan["blocked_reason"])
            if payload.get("digest") != plan["digest"]:
                raise ValueError("Run changed. Reopen its details before retrying.")
            step = next(
                (item for item in plan["steps"] if item["id"] == payload.get("step_id")), None
            )
            if not step:
                raise ValueError("Choose a failed step from this run.")
            if step["requires_effect_review"] and payload.get("confirm_effects") is not True:
                raise ValueError(
                    "Check external effects and explicitly confirm that repeating this tool is safe."
                )
            if studio.team:
                studio.team.assert_execution(run.get("requester_nexus_id"))
            request = self.request(run_id)
            if run_id in studio.active_tasks or request and request.get("status") == "running":
                raise ValueError("The previous worker is finishing. Retry after it stops.")
            path, state = self.workflow_state(run_id)
            original_state = copy.deepcopy(state)
            original_run = copy.deepcopy(run)
            from nexus.core.studio import _now

            receipt = {
                "digest": plan["digest"],
                "step_id": step["id"],
                "created_at": _now(),
                "reviewed_by": actor["nexus_id"] if actor else "desktop",
                "effects_reviewed": bool(payload.get("confirm_effects")),
            }
            claim = None
            session_id = ""
            build_id = None
            session_claimed = build_claimed = False
            dispatched = False
            try:
                if state is not None:
                    self.reset_failure(state, step["id"])
                    candidates = (
                        [
                            key
                            for key in state.get("started", {})
                            if state.get("nodes", {}).get(key) not in {"completed", "skipped"}
                        ]
                        if payload.get("confirm_effects") is True
                        else []
                    )
                    # Only recorded failed tools lose uncertain-attempt receipts; containers keep their receipts.
                    for event in [
                        *studio.records("execution-events", limit=None),
                        *studio.records("node-traces", limit=None),
                    ]:
                        if (
                            event.get("run_id") == run_id
                            and (event.get("phase") == "tool" or event.get("kind") == "tool")
                            and event.get("node_id") in candidates
                        ):
                            state["started"].pop(event["node_id"], None)
                    studio._write(path, state)
                    checkpoint = studio.workbench.capture_checkpoint(run_id, actor)
                    studio._update_run(run_id, step_retries=[*run.get("step_retries", []), receipt])
                    studio.execution.record(
                        run_id, "retry", node_id=step["id"], node_name=step["name"], status="queued"
                    )
                    queued = studio.workbench.resume_checkpoint(
                        run_id, {"digest": checkpoint["digest"]}, actor
                    )
                    dispatched = True
                else:
                    saved = copy.deepcopy(request["payload"])
                    session_id = (request.get("session_id") or "") if run["kind"] == "chat" else ""
                    build_id = saved.get("build_id")
                    if saved.get("agent_id"):
                        studio.resource_lifecycle.assert_launch("agents:" + saved["agent_id"])
                    if run["kind"] == "build":
                        studio.builder.validate_request(saved)
                    if (
                        session_id
                        and studio._path("sessions", session_id).exists()
                        and studio.session(session_id).get("archived")
                    ):
                        raise ValueError("Restore this archived conversation before retrying.")
                    if (
                        studio.team
                        and run["kind"] == "tool"
                        and not studio.team.tool_allowed(saved.get("tool"), studio)
                    ):
                        raise PermissionError("Tool is not enabled for shared execution")
                    if studio.team and run["kind"] == "design":
                        studio.team.authorize(actor, "/api/catalog/prompts")
                    if session_id and (
                        session_id in studio.active_sessions
                        or any(
                            other.get("session_id") == session_id
                            and other["status"] in {"queued", "running"}
                            for other in studio.records("runs", limit=None)
                        )
                    ):
                        raise ValueError("This conversation already has a running task.")
                    if build_id and (
                        build_id in studio.active_builds
                        or any(
                            other.get("build_id") == build_id
                            and other["status"] in {"queued", "running"}
                            for other in studio.records("runs", limit=None)
                        )
                    ):
                        raise ValueError("This build already has a running task.")
                    if session_id:
                        studio.active_sessions.add(session_id)
                        session_claimed = True
                    if build_id:
                        studio.active_builds.add(build_id)
                        build_claimed = True
                    claim = studio.workbench.reserve_launch(saved)
                    studio._update_run(
                        run_id,
                        status="queued",
                        error=None,
                        step_retries=[*run.get("step_retries", []), receipt],
                    )
                    studio.execution.record(
                        run_id, "retry", node_id=step["id"], node_name=step["name"], status="queued"
                    )
                    queued = studio.run(run_id)
                    studio.executor.submit(studio._execute, run_id, saved, session_id)
                    dispatched = True
                return queued
            except BaseException:
                if dispatched:
                    raise
                studio.workbench.release_launch(claim)
                if session_claimed:
                    studio.active_sessions.discard(session_id)
                if build_claimed:
                    studio.active_builds.discard(build_id)
                if original_state is not None:
                    studio._write(path, original_state)
                studio._write(studio._path("runs", run_id), original_run)
                if original_state is not None:
                    studio.workbench.capture_checkpoint(run_id, actor)
                raise

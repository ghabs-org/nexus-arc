"""Durable, explicit-cancellation cleanup for entered external workflow scopes."""

import asyncio
from copy import deepcopy


class WorkflowCancelledError(Exception):
    """A cancelled scope has finished its exit handlers (not task interruption)."""


class CleanupEffectUncertainError(ValueError):
    """A cleanup effect was started without an acknowledged result; never replay."""


def abandon_nodes(studio, run_id, state, pending, prefix=None, status="cancelled"):
    keys = set(pending) | set(state["started"])
    for key in sorted(keys):
        if prefix and not key.startswith(prefix + "/"):
            continue
        if state["nodes"].get(key) in {"completed", "skipped", "cancelled"}:
            continue
        state["nodes"][key] = status
        studio.execution.record(
            run_id, "node", node_id=key, node_name=key.rsplit("/", 1)[-1], status=status
        )


class WorkflowCancellation:
    def __init__(self, studio, run_id, state, persist):
        self.studio, self.run_id, self.state, self.persist = studio, run_id, state, persist

    def enter(self, graph, context, scope, depth):
        if graph.get("interop_exit_handlers"):
            self.state.setdefault("exit_scopes", {}).setdefault(
                scope, {"graph": deepcopy(graph), "context": deepcopy(context), "depth": depth}
            )
            self.persist()

    def close(self, scope):
        if scope in self.state.get("exit_scopes", {}):
            self.state["exit_scopes"][scope]["closed"] = True
            self.persist()

    async def execute(self, run_graph):
        if self.studio.closing:
            raise asyncio.CancelledError
        progress = self.state.get("cancellation")
        if progress is None:
            scopes = self.state.get("exit_scopes", {})
            progress = self.state["cancellation"] = {
                "format": "nexus-workflow-cancellation/v1",
                "scopes": sorted(
                    (key for key, value in scopes.items() if not value.get("closed")),
                    key=lambda key: (-scopes[key]["depth"], key),
                ),
                "done": [],
            }
            run = self.studio.run(self.run_id)
            pending = [
                item["id"]
                for item in [*run.get("awaiting_approval", []), *run.get("awaiting_input", [])]
            ]
            abandon_nodes(
                self.studio,
                self.run_id,
                self.state,
                [*pending, *self.state.pop("cancelled_pending", [])],
            )
            self.studio._update_run(self.run_id, awaiting_approval=[], awaiting_input=[])
        self.studio._update_run(self.run_id, cancellation_cleanup=True, cleanup_status="running")
        self.persist()
        for scope in progress["scopes"]:
            if scope in progress["done"]:
                continue
            entry = self.state["exit_scopes"][scope]
            outcome = {"status": "cancelled"}
            if scope == "root":
                self.state["outcome"] = outcome
            else:
                self.state.setdefault("scope_outcomes", {})[scope] = outcome
            self.persist()
            try:
                await run_graph(entry["graph"], entry["context"], scope, entry["depth"])
            except WorkflowCancelledError:
                pass
            progress["done"].append(scope)
            self.persist()
        progress["completed"] = True
        self.persist()
        self.studio._update_run(
            self.run_id,
            cancellation_cleanup=False,
            cleanup_status="completed",
            awaiting_approval=[],
            awaiting_input=[],
        )
        raise asyncio.CancelledError


def request_cleanup(studio, run_id, enqueue=True):
    """Queue only saved exit scopes of an inactive structured workflow.

    Called under Studio's lock. Running tasks are interrupted by cancel_run;
    this path is for approval/input-paused tasks whose original executor ended.
    """
    path = studio._path("graph-runs", run_id)
    if not path.exists():
        return False
    state = studio._read(path)
    if "exit_scopes" not in state and state.get("graph", {}).get("interop_exit_handlers"):
        # Pre-feature checkpoints retain root input but not nested scope captures.
        state["exit_scopes"] = {
            "root": {
                "graph": deepcopy(state["graph"]),
                "context": {
                    "input": state["payload"].get("workflow_input", state["payload"]["task"])
                },
                "depth": 0,
            }
        }
        state.setdefault("exit_failures", {})["legacy/nested-scopes"] = (
            "LegacyScopeCapturesUnavailable"
        )
        studio._update_run(
            run_id,
            workflow_warnings=[
                {"node_id": key, "error_type": value}
                for key, value in state["exit_failures"].items()
            ],
        )
    progress = state.get("cancellation")
    if not any(not value.get("closed") for value in state.get("exit_scopes", {}).values()) and not (
        progress and not progress.get("completed")
    ):
        return False
    if progress and progress.get("current_handler") and studio.run(run_id)["status"] == "waiting":
        # Reject the entire pending cleanup handler, never approve its gates.
        state.setdefault("exit_failures", {})[progress["current_handler"]] = "CleanupRejected"
        studio.execution.record(
            run_id,
            "cleanup",
            node_id=progress["current_handler"],
            status="skipped",
            message="CleanupRejected",
        )
        run = studio.run(run_id)
        pending = [
            item["id"]
            for item in [*run.get("awaiting_approval", []), *run.get("awaiting_input", [])]
        ]
        abandon_nodes(
            studio, run_id, state, pending, prefix=progress["current_handler"], status="skipped"
        )
        studio._update_run(
            run_id,
            workflow_warnings=[
                {"node_id": key, "error_type": value}
                for key, value in state["exit_failures"].items()
            ],
        )
        progress.pop("current_handler", None)
    if progress is None:
        run = studio.run(run_id)
        state["cancelled_pending"] = [
            item["id"]
            for item in [*run.get("awaiting_approval", []), *run.get("awaiting_input", [])]
        ]
    studio._write(path, state)
    studio._update_run(
        run_id,
        status="queued",
        cancel_requested=True,
        cancellation_cleanup=True,
        cleanup_status="queued",
        awaiting_approval=[],
        awaiting_input=[],
    )
    if enqueue:
        studio.resume_workflow(run_id, {**state["payload"], "resume_graph": True})
    return True

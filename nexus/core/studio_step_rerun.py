"""Re-run one step of a finished YAML workflow run as a new run, optionally with extra input.

Earlier steps reuse the source run's saved outputs; the source run is never changed.
"""

from __future__ import annotations

MODES = {"continue", "only"}
NOTE_LIMIT = 4000


class StudioStepRerun:
    def __init__(self, studio):
        self.studio = studio

    def source(self, run_id, actor):
        studio = self.studio
        run = studio.collaboration.owned(studio.run(run_id), actor)
        path = studio._path("yaml-runs", run_id)
        state = studio._read(path) if path.exists() else None
        return run, state

    def describe(self, run_id, actor=None):
        studio = self.studio
        with studio.lock:
            run, state = self.source(run_id, actor)
            blocked = ""
            if run["kind"] != "workflow":
                blocked = "Only workflow runs have steps to re-run."
            elif run["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
                blocked = "Wait for this run to finish before re-running a step."
            elif state is None:
                blocked = (
                    "Step re-run needs a saved YAML workflow run; this run has none."
                    if not studio._path("graph-runs", run_id).exists()
                    else "Step re-run is not available for graph workflows yet."
                )
            elif studio.team and (not actor or actor["nexus_id"] != run.get("requester_nexus_id")):
                blocked = "Only the account that started this run can re-run its steps."
            elif studio.team and actor["role"] == "viewer":
                blocked = "Execution access is required to re-run a step."
            elif any(
                step.get("parallel") or step.get("parallel_with")
                for step in self.document_steps(state)
            ):
                blocked = "Step re-run is not available for workflows with parallel steps yet."
            steps = []
            if state is not None:
                for node in state.get("graph", {}).get("nodes", []):
                    steps.append(
                        {
                            "id": node["id"],
                            "name": node["name"],
                            "status": state.get("nodes", {}).get(node["id"], "not run"),
                            "has_output": node["id"] in state.get("outputs", {}),
                        }
                    )
            effects = [
                row
                for row in studio.records("integration-effects", limit=None)
                if row.get("run_id") == run_id
            ]
            return {
                "run_id": run_id,
                "can_rerun": not blocked and bool(steps),
                "blocked_reason": blocked,
                "steps": steps,
                "requires_effect_review": bool(effects),
            }

    @staticmethod
    def document_steps(state):
        snapshot = state.get("payload", {}).get("workflow_snapshot") or {}
        document = snapshot.get("document", snapshot)
        return document.get("steps", []) if isinstance(document, dict) else []

    def queue(self, run_id, data, actor=None):
        studio = self.studio
        step_id, mode = data.get("step_id"), data.get("mode", "continue")
        note = data.get("note", "")
        if mode not in MODES:
            raise ValueError("Choose to continue the workflow or re-run only this step.")
        if not isinstance(note, str) or len(note) > NOTE_LIMIT:
            raise ValueError(f"Extra input must be text up to {NOTE_LIMIT} characters.")
        if "confirm_effects" in data and type(data["confirm_effects"]) is not bool:
            raise ValueError("Effect review confirmation must be true or false.")
        plan = self.describe(run_id, actor)
        if not plan["can_rerun"]:
            raise ValueError(plan["blocked_reason"])
        index = next((i for i, s in enumerate(plan["steps"]) if s["id"] == step_id), None)
        if index is None:
            raise ValueError("Choose a step from this run.")
        # Reused outputs stand in for executed steps, so every reused step needs one.
        reused = plan["steps"][:index] + (plan["steps"][index + 1 :] if mode == "only" else [])
        missing = [step["name"] for step in reused if not step["has_output"]]
        if missing:
            raise ValueError(
                "These steps have no saved output to reuse: "
                + ", ".join(missing)
                + ". Re-run an earlier step or continue the workflow instead."
            )
        if plan["requires_effect_review"] and data.get("confirm_effects") is not True:
            raise ValueError(
                "This run recorded external actions. Confirm that repeating its steps is safe."
            )
        run, state = self.source(run_id, actor)
        saved = state["payload"]
        payload = {
            key: saved[key]
            for key in ("task", "provider", "model", "effort", "requester_nexus_id")
            if key in saved
        }
        if saved.get("artifact_refs"):
            refs = [{"id": r["id"], "revision": r["revision"]} for r in saved["artifact_refs"]]
            payload.update(artifact_refs=refs, artifact_ids=[r["id"] for r in refs])
        if actor:
            payload["requester_nexus_id"] = actor["nexus_id"]
        payload.update(kind="workflow", workflow_id=state["workflow_id"])
        queued = studio.start_run(
            payload,
            step_rerun={
                "source_run_id": run_id,
                "step_num": index + 1,
                "mode": mode,
                "note": note.strip(),
                "names": [step["name"] for step in plan["steps"]],
            },
        )
        rerun_of = {
            "run_id": run_id,
            "step_id": step_id,
            "step_name": plan["steps"][index]["name"],
            "mode": mode,
            "note": note.strip(),
        }
        studio._update_run(queued["id"], rerun_of=rerun_of)
        return {**queued, "rerun_of": rerun_of}

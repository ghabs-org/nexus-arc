"""Resumable Nexus YAML workflows using the framework's original workflow engine."""

from __future__ import annotations

import asyncio

from nexus.adapters.storage.file import FileStorage
from nexus.core.models import ApprovalGateType, StepStatus, WorkflowState
from nexus.core.studio_graph import StudioPausedError
from nexus.core.workflow import WorkflowEngine
from nexus.core.yaml_loader import YamlWorkflowLoader


class StudioLegacyWorkflows:
    def __init__(self, studio):
        self.studio = studio

    def engine(self):
        if self.studio.sql_state:
            from nexus.core.studio_state import StudioWorkflowStorage

            return WorkflowEngine(StudioWorkflowStorage(self.studio))
        return WorkflowEngine(FileStorage(self.studio.state / "workflow-runtime"))

    @staticmethod
    def gate(step):
        return f"step-{step.step_num}-{step.iteration}"

    def snapshot(self, state, workflow):
        state["nodes"] = {
            f"step-{step.step_num}": step.status.value
            for step in workflow.steps
            if step.status in {StepStatus.COMPLETED, StepStatus.SKIPPED}
        }
        state["outputs"] = {
            f"step-{step.step_num}": str(step.outputs.get("result", ""))
            for step in workflow.steps
            if step.status == StepStatus.COMPLETED
        }
        state["graph"] = {
            "nodes": [
                {"id": f"step-{step.step_num}", "name": step.name, "kind": "agent"}
                for step in workflow.steps
            ],
            "edges": [],
        }
        state["current_step"] = workflow.current_step
        state["workflow_state"] = workflow.state.value
        self.studio._write(self.studio._path("yaml-runs", state["id"]), state)

    async def execute(self, run_id, payload, document):
        identity = payload.get("_evaluation_case") or run_id
        path = self.studio._path("yaml-runs", identity)
        engine = self.engine()
        if path.exists():
            state = self.studio._read(path)
            workflow = await engine.get_workflow(state["runtime_id"])
            if workflow is None:
                raise ValueError("Saved YAML workflow runtime state is missing")
            if workflow.state == WorkflowState.PAUSED:
                if not payload.get("resume_yaml"):
                    raise StudioPausedError("YAML workflow awaits approval")
                workflow = await engine.resume_workflow(workflow.id)
            elif workflow.state == WorkflowState.FAILED and payload.get("resume_yaml"):
                workflow.state = WorkflowState.RUNNING
                current = workflow.get_step(workflow.current_step)
                if current and current.status != StepStatus.COMPLETED:
                    current.status = StepStatus.RUNNING
                    current.error = None
                    current.retry_count = 0
                await engine.storage.save_workflow(workflow)
        else:
            workflow = YamlWorkflowLoader.load_from_dict(document, workflow_id="studio-" + identity)
            self.validate_parallel(workflow, document)
            if any(
                (step.retry if step.retry is not None else step.agent.max_retries) > 3
                for step in workflow.steps
            ):
                raise ValueError("Studio YAML workflows support at most three retries per step")
            await engine.create_workflow(workflow)
            workflow = await engine.start_workflow(workflow.id)
            state = {
                "id": identity,
                "format": "yaml",
                "runtime_id": workflow.id,
                "workflow_id": payload["workflow_id"],
                "approved": [],
                "payload": {
                    key: payload[key]
                    for key in [
                        "kind",
                        "task",
                        "provider",
                        "model",
                        "effort",
                        "workflow_id",
                        "workflow_snapshot",
                        "reference_context",
                        "artifact_refs",
                        "agent_snapshots",
                        "expanded_task",
                        "requester_nexus_id",
                    ]
                    if key in payload
                },
            }
            if payload.get("_step_rerun"):
                workflow = await self.seed(engine, workflow, payload["_step_rerun"])
        self.snapshot(state, workflow)
        self.studio._update_run(
            run_id, workflow_id=workflow.id, workflow_definition_id=state["workflow_id"]
        )
        for _ in range(100):
            if workflow.state != WorkflowState.RUNNING:
                break
            step = workflow.get_step(workflow.current_step)
            if step is None or step.status not in {StepStatus.RUNNING, StepStatus.PENDING}:
                raise ValueError("YAML workflow has no runnable current step")
            reused = self.reused_output(payload, step)
            if reused is not None:
                workflow = await engine.complete_step(
                    workflow.id, step.step_num, {"result": reused}
                )
                self.snapshot(state, workflow)
                continue
            needs_approval = step.require_human_approval or any(
                gate.required and gate.gate_type != ApprovalGateType.PR_MERGE
                for gate in step.approval_gates
            )
            if needs_approval and self.gate(step) not in state["approved"]:
                workflow = await engine.pause_workflow(workflow.id)
                self.snapshot(state, workflow)
                self.studio._update_run(
                    run_id,
                    awaiting_approval=[{"id": self.gate(step), "name": step.name}],
                    output=self.output(workflow),
                    output_steps=self.output_steps(workflow),
                )
                raise StudioPausedError("YAML workflow awaits approval")
            self.studio._update_run(
                run_id,
                step=step.name,
                output=self.output(workflow),
                output_steps=self.output_steps(workflow),
            )
            try:
                await self.run_parallel(engine, workflow, state, step, payload, run_id)
                cached = state.get("parallel_outputs", {}).get(self.gate(step))
                if cached is None:
                    result = await self.invoke_step(engine, workflow, step, payload, run_id)
                    output = result.output
                else:
                    output = cached
            except StudioPausedError:
                raise
            except Exception as exc:
                workflow = await engine.complete_step(
                    workflow.id, step.step_num, {}, error=str(exc) or type(exc).__name__
                )
                self.snapshot(state, workflow)
                if workflow.state == WorkflowState.RUNNING:
                    continue
                raise
            workflow = await engine.complete_step(workflow.id, step.step_num, {"result": output})
            self.snapshot(state, workflow)
        self.studio._update_run(
            run_id, awaiting_approval=[], output_steps=self.output_steps(workflow)
        )
        if workflow.state != WorkflowState.COMPLETED:
            raise ValueError(
                "YAML workflow stopped before completion or reached its execution limit"
            )
        return self.output(workflow)

    def source_outputs(self, rerun):
        return self.studio._read(self.studio._path("yaml-runs", rerun["source_run_id"]))["outputs"]

    async def seed(self, engine, workflow, rerun):
        """Complete the steps before the chosen one with the source run's saved outputs."""
        if [step.name for step in workflow.steps] != rerun["names"]:
            raise ValueError("Workflow steps changed since the source run; start a new run instead")
        outputs = self.source_outputs(rerun)
        while (
            workflow.state == WorkflowState.RUNNING and workflow.current_step != rerun["step_num"]
        ):
            step = workflow.get_step(workflow.current_step)
            key = f"step-{step.step_num}" if step else ""
            if step is None or step.step_num > rerun["step_num"] or key not in outputs:
                break
            workflow = await engine.complete_step(
                workflow.id, step.step_num, {"result": outputs[key]}
            )
        if workflow.state != WorkflowState.RUNNING or workflow.current_step != rerun["step_num"]:
            raise ValueError(
                "Saved outputs no longer lead to the chosen step; start a new run instead"
            )
        return workflow

    def reused_output(self, payload, step):
        """In only-this-step mode, later steps keep the source run's outputs."""
        rerun = payload.get("_step_rerun")
        if not rerun or rerun["mode"] != "only" or step.step_num <= rerun["step_num"]:
            return None
        output = self.source_outputs(rerun).get(f"step-{step.step_num}")
        if output is None:
            raise ValueError(f"No saved output to reuse for step {step.name}")
        return output

    @staticmethod
    def validate_parallel(workflow, document):
        source = document.get("steps", [])
        names = {
            item.get("id"): step.name for item, step in zip(source, workflow.steps, strict=True)
        }
        for step in workflow.steps:
            step.parallel_with = [names.get(name, name) for name in step.parallel_with]
            for name in step.parallel_with:
                anchor = next((item for item in workflow.steps if item.name == name), None)
                if anchor is None or anchor.step_num >= step.step_num:
                    raise ValueError("Parallel references must name an earlier step")
                group = [anchor] + [item for item in workflow.steps if name in item.parallel_with]
                numbers = sorted(item.step_num for item in group)
                if (
                    len(group) > 4
                    or numbers != list(range(numbers[0], numbers[-1] + 1))
                    or any(item.routes or item.on_success or item.final_step for item in group)
                ):
                    raise ValueError(
                        "YAML parallel groups must be contiguous, at most four steps, without internal routes; use the canvas for arbitrary branches"
                    )

    async def invoke_step(self, engine, workflow, step, payload, run_id):
        candidates = [
            item
            for item in self.studio.definitions("agents")
            if item.get("document", {}).get("spec", {}).get("agent_type") == step.agent.name
        ]
        if not candidates:
            raise ValueError(
                "Create the saved agent required by this YAML step: " + step.agent.name
            )
        prior = [item for item in workflow.steps if item.status == StepStatus.COMPLETED]
        task = engine.render_prompt(
            step.prompt_template or "{task}",
            {
                "task": payload.get("expanded_task", payload["task"]),
                "description": payload.get("expanded_task", payload["task"]),
                "result": prior[-1].outputs.get("result", "") if prior else "",
            },
        )
        if step.approval_gates:
            task = step.get_approval_constraints() + "\n\n" + task
        if prior:
            task += "\nPrevious steps:\n" + self.output(workflow)
        rerun = payload.get("_step_rerun")
        if rerun and rerun.get("note") and step.step_num == rerun["step_num"]:
            task += "\n\nAdditional input for this re-run:\n" + rerun["note"]
        request = {
            key: payload[key]
            for key in [
                "provider",
                "model",
                "effort",
                "reference_context",
                "artifact_refs",
                "agent_snapshots",
                "requester_nexus_id",
            ]
            if key in payload
        }
        identity = f"step-{step.step_num}"
        self.studio.execution.record(
            run_id,
            "node",
            node_id=identity,
            node_name=step.name,
            agent_id=candidates[0]["id"],
            phase="yaml",
            status="running",
        )
        status = "failed"
        try:
            result = await self.studio._invoke(
                {**request, "agent_id": candidates[0]["id"]}, task, run_id
            )
            status = "completed"
            return result
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        finally:
            self.studio.execution.record(
                run_id,
                "node",
                node_id=identity,
                node_name=step.name,
                agent_id=candidates[0]["id"],
                phase="yaml",
                status=status,
            )

    async def run_parallel(self, engine, workflow, state, step, payload, run_id):
        group = [step] + [
            item
            for item in await engine.get_runnable_steps(workflow.id)
            if item.step_num != step.step_num
        ]
        if len(group) < 2:
            return
        pending = [
            item for item in group if self.gate(item) not in state.get("parallel_outputs", {})
        ]
        gates = [
            item
            for item in pending
            if (
                item.require_human_approval
                or any(
                    gate.required and gate.gate_type != ApprovalGateType.PR_MERGE
                    for gate in item.approval_gates
                )
            )
            and self.gate(item) not in state["approved"]
        ]
        if gates:
            workflow = await engine.pause_workflow(workflow.id)
            self.snapshot(state, workflow)
            self.studio._update_run(
                run_id,
                awaiting_approval=[{"id": self.gate(item), "name": item.name} for item in gates],
                output=self.output(workflow),
                output_steps=self.output_steps(workflow),
            )
            raise StudioPausedError("Parallel YAML group awaits approval")

        async def execute(item):
            try:
                result = await self.invoke_step(engine, workflow, item, payload, run_id)
                # Persist each success as it arrives so failed siblings do not replay it.
                with self.studio.lock:
                    state.setdefault("parallel_outputs", {})[self.gate(item)] = result.output
                    self.snapshot(state, workflow)
                return None
            except Exception as exc:
                return exc

        outcomes = await asyncio.gather(*(execute(item) for item in pending))
        current_error = next(
            (
                result
                for item, result in zip(pending, outcomes, strict=True)
                if item.step_num == step.step_num and result is not None
            ),
            None,
        )
        if current_error:
            raise current_error

    @staticmethod
    def output_steps(workflow):
        """Step names in the order output() joins them, so viewers can split the text."""
        return [step.name for step in workflow.steps if step.status == StepStatus.COMPLETED]

    @staticmethod
    def output(workflow):
        return "\n\n".join(
            f"{step.name}\n{step.outputs.get('result', '')}"
            for step in workflow.steps
            if step.status == StepStatus.COMPLETED
        )

    def resume(self, run_id, decision, *, checkpoint=None):
        from nexus.core.studio_experiments import (
            checkpoint_id,
            continuation_payload,
            validate_continuation,
        )

        if decision not in {"approve", "reject"}:
            raise ValueError("Choose approve or reject")
        with self.studio.lock:
            validate_continuation(self.studio, run_id, checkpoint)
            run = self.studio.run(run_id)
            if run["status"] != "waiting" or not run.get("awaiting_approval"):
                raise ValueError("YAML workflow is not awaiting approval")
            path = self.studio._path("yaml-runs", checkpoint_id(self.studio, run_id))
            state = self.studio._read(path)
            if decision == "reject":
                self.studio._update_run(
                    run_id, status="cancelled", awaiting_approval=[], error="YAML approval rejected"
                )
                return self.studio.run(run_id)
            if self.studio.team:
                self.studio.team.assert_execution(run.get("requester_nexus_id"))
            state["approved"].extend(gate["id"] for gate in run["awaiting_approval"])
            self.studio._write(path, state)
            self.studio._update_run(run_id, status="queued", error=None, awaiting_approval=[])
            self.studio.resume_workflow(
                run_id, continuation_payload(self.studio, run_id, state["payload"], "resume_yaml")
            )
            return self.studio.run(run_id)

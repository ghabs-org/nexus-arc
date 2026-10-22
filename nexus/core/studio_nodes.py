"""Typed Studio workflow nodes, reviewed integration effects and retry policy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

from nexus.core.studio_catalog import _text
from nexus.core.studio_connections import READ_ACTIONS


class StudioNodeTypes:
    def __init__(self, studio):
        self.studio = studio

    def validate(self, node, connections=None):
        kind = node.get("kind", "agent")
        result = {}
        retries = node.get("retries", 0)
        if type(retries) is not int or not 0 <= retries <= 3:
            raise ValueError("Retries must be between zero and three")
        if retries:
            result["retries"] = retries
        if kind == "decision":
            decision = node.get("decision")
            if (
                not isinstance(decision, dict)
                or decision.get("format") != "nexus-typed-decision/v1"
                and set(decision)
                - {
                    "instructions",
                    "options",
                    "default",
                    "threshold",
                    "backend",
                    "model",
                    "decision_model_id",
                    "timeout_seconds",
                }
            ):
                raise ValueError("Decision node requires typed choice configuration")
            decision_effective = dict(decision)
            if decision.get("decision_model_id"):
                dm = self.studio.catalog.get("decisionmodels", decision["decision_model_id"])
                decision_effective.setdefault("backend", dm["data"]["backend"])
                decision_effective.setdefault("model", dm["data"]["model"])
                if "timeout_seconds" in dm["data"]:
                    decision_effective.setdefault("timeout_seconds", dm["data"]["timeout_seconds"])
            self.validate_decision(decision_effective)
            result["decision"] = decision
        elif kind == "integration":
            record = (connections or {}).get(node.get("connector_id"))
            if record is None:
                record = self.studio.catalog.get("connectors", node.get("connector_id"))
            action = node.get("action")
            if action not in record["data"].get("actions", []):
                raise ValueError("Integration action must be enabled in its saved connection")
            args = node.get("arguments", {})
            if not isinstance(args, dict) or len(json.dumps(args)) > 64000:
                raise ValueError("Integration arguments must be an object up to 64 KiB")
            if retries and action not in READ_ACTIONS:
                raise ValueError("Automatic retries are disabled for mutating integration actions")
            result.update(connector_id=record["id"], action=action, arguments=args)
            if node.get("tool"):
                result["tool"] = _text(node["tool"], "MCP tool", 200)
        elif kind == "delay":
            seconds = node.get("delay_seconds", 1)
            if type(seconds) not in {int, float} or not 0 <= seconds <= 60:
                raise ValueError("Delay must be between zero and sixty seconds")
            result["delay_seconds"] = seconds
        return result

    def validate_decision(self, decision):
        if isinstance(decision, dict) and decision.get("format") == "nexus-typed-decision/v1":
            from nexus.core.studio_decisions import validate_decision

            return validate_decision(decision)
        # The shared runtime's ordinary validator requires a real principal.
        # Validate structure using the same code, with identity checking deferred
        # to execution rather than inventing an administrator identity.
        from nexus.core.studio_jobs import StudioJobs

        class ValidationWorkbench:
            def actor(self, payload):
                return None

        jobs = StudioJobs.__new__(StudioJobs)
        jobs.workbench = ValidationWorkbench()
        jobs.prepare({**decision, "task": "Validate choice", "kind": "decision"})

    def validate_gates(self, graph):
        kinds = {node["id"]: node["kind"] for node in graph["nodes"]}
        for node in graph["nodes"]:
            if node["kind"] == "integration" and node["action"] not in READ_ACTIONS:
                parents = [
                    edge["source"] for edge in graph["edges"] if edge["target"] == node["id"]
                ]
                if not parents or any(kinds.get(parent) != "approval" for parent in parents):
                    raise ValueError(
                        "Every mutating integration node must directly follow approval gates"
                    )

    async def execute(self, node, payload, run_id, prior):
        def render(value):
            if isinstance(value, str):
                return value.replace("{task}", payload["task"]).replace(
                    "{previous}", "\n\n".join(prior)
                )
            if isinstance(value, list):
                return [render(item) for item in value]
            if isinstance(value, dict):
                return {k: render(v) for k, v in value.items()}
            return value

        if node["kind"] == "decision":
            decision_config = dict(node["decision"])
            if decision_config.get("decision_model_id"):
                dm = self.studio.catalog.get("decisionmodels", decision_config["decision_model_id"])
                decision_config.setdefault("backend", dm["data"]["backend"])
                decision_config.setdefault("model", dm["data"]["model"])
            request = {
                **decision_config,
                "task": payload["task"]
                + ("\nPrevious results:\n" + "\n\n".join(prior) if prior else ""),
                "requester_nexus_id": payload.get("requester_nexus_id"),
            }
            return await self.studio.workbench.jobs.decision(
                run_id,
                request,
                record_id=f"{run_id}-{hashlib.sha256(node['id'].encode()).hexdigest()[:16]}",
            )
        if node["kind"] == "delay":
            await asyncio.sleep(node["delay_seconds"])
            return f"Waited {node['delay_seconds']} seconds"
        effect_id = f"{run_id}-{hashlib.sha256(node['id'].encode()).hexdigest()[:16]}"
        effect_path = self.studio._path("integration-effects", effect_id)
        rendered_arguments = render(node["arguments"])
        mutating = node["action"] not in READ_ACTIONS
        if mutating:
            with self.studio.lock:
                if effect_path.exists():
                    effect = self.studio._read(effect_path)
                    if effect["status"] == "completed":
                        return effect["output"]
                    if effect["status"] != "reviewed-retry":
                        raise ValueError(
                            "External action outcome is uncertain. Review it under Workspace tools before resuming"
                        )
                self.studio._write(
                    effect_path,
                    {
                        "id": effect_id,
                        "run_id": run_id,
                        "node_id": node["id"],
                        "node_name": node.get("name", node["id"]),
                        "owner_id": payload.get("requester_nexus_id"),
                        "connector_id": node["connector_id"],
                        "action": node["action"],
                        "arguments": rendered_arguments,
                        "status": "attempting",
                        "output": "",
                    },
                )
        result = await self.studio.connections.execute(
            node["connector_id"],
            {
                "action": node["action"],
                "arguments": rendered_arguments,
                "tool": node.get("tool"),
            },
            payload.get("requester_nexus_id"),
            approved=True,
        )
        output = (
            result
            if isinstance(result, str)
            else json.dumps(result, default=str, ensure_ascii=False)
        )
        if mutating:
            with self.studio.lock:
                effect = self.studio._read(effect_path)
                self.studio._write(effect_path, {**effect, "status": "completed", "output": output})
        return output

    def resolve(self, identity, payload, actor):
        with self.studio.lock:
            path = self.studio._path("integration-effects", identity)
            effect = self.studio.workbench.visible(self.studio._read(path), actor, own=True)
            run = self.studio.run(effect["run_id"])
            if run["status"] in {"running", "queued"}:
                raise ValueError("Wait for this run to stop before resolving its action")
            if payload.get("confirm") is not True or payload.get("outcome") not in {
                "completed",
                "retry",
            }:
                raise ValueError(
                    "Review the remote account, choose completed or retry, and confirm"
                )
            effect["status"] = (
                "completed" if payload["outcome"] == "completed" else "reviewed-retry"
            )
            effect["output"] = _text(
                payload.get("output") or "Remote action reviewed", "Reviewed result", 64000
            )
            effect["reviewed_by"] = actor["nexus_id"] if actor else "desktop"
            self.studio._write(path, effect)
            return effect

    def wrapper(self, invoke, node, run_id, payload):
        async def execute(context, node_input=None):
            for attempt in range(node.get("retries", 0) + 1):
                started = time.monotonic()
                status = "completed"
                self.studio.execution.record(
                    run_id,
                    "node",
                    node_id=node["id"],
                    node_name=node.get("name", node["id"]),
                    phase=node["kind"],
                    status="running",
                    tool=node.get("tool_id"),
                )
                try:
                    return await invoke(context, node_input)
                except BaseException as exc:
                    status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                    if not isinstance(exc, Exception) or attempt >= node.get("retries", 0):
                        raise
                finally:
                    event = {
                        "id": f"{run_id}-{hashlib.sha256(node['id'].encode()).hexdigest()[:16]}-{attempt}",
                        "run_id": run_id,
                        "owner_id": payload.get("requester_nexus_id"),
                        "node_id": node["id"],
                        "kind": node["kind"],
                        "attempt": attempt + 1,
                        "status": status,
                        "duration_seconds": round(time.monotonic() - started, 3),
                    }
                    self.studio._write(self.studio._path("node-traces", event["id"]), event)
                    self.studio.execution.record(
                        run_id,
                        "node",
                        node_id=node["id"],
                        node_name=node.get("name", node["id"]),
                        phase=node["kind"],
                        status=status,
                        source_id=event["id"],
                        duration_seconds=event["duration_seconds"],
                        tool=node.get("tool_id"),
                    )
                await asyncio.sleep(min(2**attempt, 4))

        return execute

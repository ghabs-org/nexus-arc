"""Reviewable DAG definitions using Nexus graph nodes and parallel execution."""

from __future__ import annotations

import asyncio
import json
import re
from string import Formatter

from nexus.core.studio_catalog import _text


class StudioPausedError(Exception):
    """A persisted local workflow needs a human decision."""


class StudioGraph:
    def __init__(self, studio):
        self.studio = studio
        from nexus.core.studio_workflow_v2 import StudioWorkflowV2

        self.structured = StudioWorkflowV2(studio)

    def validate(self, graph, agent_ids=None, connections=None):
        if isinstance(graph, dict) and graph.get("version") == 2:
            return self.structured.validate(graph, agent_ids=agent_ids, connections=connections)
        if not isinstance(graph, dict) or set(graph) - {"nodes", "edges"}:
            raise ValueError("Graph must contain nodes and edges")
        nodes, edges = graph.get("nodes"), graph.get("edges")
        if (
            not isinstance(nodes, list)
            or not 1 <= len(nodes) <= 40
            or not isinstance(edges, list)
            or len(edges) > 160
        ):
            raise ValueError("Use 1–40 nodes and at most 160 edges")
        normalized, identities = [], set()
        for node in nodes:
            if not isinstance(node, dict) or set(node) - {
                "id",
                "name",
                "kind",
                "agent_id",
                "prompt",
                "position",
                "tool_id",
                "args",
                "kwargs",
                "decision",
                "connector_id",
                "action",
                "arguments",
                "tool",
                "retries",
                "delay_seconds",
            }:
                unknown = (
                    sorted(
                        set(node)
                        - {
                            "id",
                            "name",
                            "kind",
                            "agent_id",
                            "prompt",
                            "position",
                            "tool_id",
                            "args",
                            "kwargs",
                            "decision",
                            "connector_id",
                            "action",
                            "arguments",
                            "tool",
                            "retries",
                            "delay_seconds",
                        }
                    )
                    if isinstance(node, dict)
                    else []
                )
                raise ValueError(
                    "Unsupported graph node fields" + (": " + ", ".join(unknown) if unknown else "")
                )
            identity = node.get("id")
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identity)
                or identity in identities
            ):
                raise ValueError("Node IDs must be unique lowercase identifiers")
            identities.add(identity)
            kind = node.get("kind", "agent")
            if kind not in {"agent", "approval", "tool", "decision", "integration", "delay"}:
                raise ValueError(
                    "Graph nodes support agents, registered tools and human approval gates"
                )
            result = {
                "id": identity,
                "name": _text(node.get("name"), "Node name", 160),
                "kind": kind,
            }
            if kind == "agent":
                if agent_ids is None or node.get("agent_id") not in agent_ids:
                    self.studio.definition("agents", node.get("agent_id"))
                prompt = _text(node.get("prompt"), "Node prompt", 16000)
                for _, field, spec, conversion in Formatter().parse(prompt):
                    if field is not None and (field != "task" or spec or conversion):
                        raise ValueError("Graph prompts support only {task}")
                result.update(agent_id=node["agent_id"], prompt=prompt)
            if kind == "tool":
                from nexus.tools import tool_registry

                tool_id = node.get("tool_id")
                if self.studio.team and not self.studio.team.tool_allowed(tool_id, self.studio):
                    raise PermissionError("Tool is not enabled for shared execution")
                if not isinstance(tool_id, str) or not tool_registry.get(tool_id):
                    raise ValueError("Choose a registered tool")
                args, kwargs = node.get("args", []), node.get("kwargs", {})
                if (
                    not isinstance(args, list)
                    or not isinstance(kwargs, dict)
                    or len(json.dumps([args, kwargs])) > 65536
                ):
                    raise ValueError(
                        "Tool args must be a JSON list and kwargs a JSON object, at most 64 KiB"
                    )
                result.update(tool_id=tool_id, args=args, kwargs=kwargs)
            from nexus.core.studio_nodes import StudioNodeTypes

            result.update(StudioNodeTypes(self.studio).validate(node, connections))
            position = node.get("position")
            if position is not None:
                if (
                    not isinstance(position, dict)
                    or set(position) != {"x", "y"}
                    or any(
                        type(v) not in {int, float} or not 0 <= v <= 10000
                        for v in position.values()
                    )
                ):
                    raise ValueError(
                        "Node positions must be numeric coordinates between 0 and 10000"
                    )
                result["position"] = position
            normalized.append(result)
        normalized_edges, pairs = [], set()
        incoming = {identity: [] for identity in identities}
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) - {"source", "target", "contains", "choice"}:
                raise ValueError("Unsupported graph edge fields")
            source, target = edge.get("source"), edge.get("target")
            if (
                source not in identities | {"START"}
                or target not in identities
                or source == target
                or (source, target) in pairs
            ):
                raise ValueError("Edges need valid distinct nodes and must be unique")
            pairs.add((source, target))
            match = edge.get("contains", "")
            if not isinstance(match, str) or len(match) > 1000 or source == "START" and match:
                raise ValueError(
                    "Edge conditions use a phrase (up to 1000 characters), after agent nodes"
                )
            incoming[target].append(source)
            normalized_edge = {"source": source, "target": target, "contains": match}
            if edge.get("choice"):
                choice = _text(edge["choice"], "Decision route", 64)
                origin = next((n for n in normalized if n["id"] == source), {})
                if origin.get("kind") != "decision":
                    raise ValueError("Choice edges must name an option of their source decision")
                decision = origin.get("decision", {})
                if decision.get("format") == "nexus-typed-decision/v1":
                    targets = set(decision.get("routes", {}).values()) | {
                        decision[k] for k in ("default", "escalate") if decision.get(k)
                    }
                    route_on = decision.get("route_on")
                    valid_choices = set(decision.get("routes", {}).keys()) | targets
                    if route_on and route_on in decision.get("questions", {}):
                        criteria = decision["questions"][route_on].get("criteria", {})
                        if isinstance(criteria, dict):
                            valid_choices |= set(criteria.keys())
                    if choice not in valid_choices:
                        raise ValueError("Choice edges must name an option of their source decision")
                elif choice not in decision.get("options", {}):
                    raise ValueError("Choice edges must name an option of their source decision")
                normalized_edge["choice"] = choice
            normalized_edges.append(normalized_edge)
        if any(not parents for parents in incoming.values()):
            raise ValueError("Every node needs an incoming edge; connect entry nodes from START")
        visited = {"START"}
        while pending := [
            identity
            for identity, parents in incoming.items()
            if identity not in visited and all(parent in visited for parent in parents)
        ]:
            visited.update(pending)
        if visited != identities | {"START"}:
            raise ValueError("Graph contains a cycle or unreachable nodes")
        result = {"nodes": normalized, "edges": normalized_edges}
        StudioNodeTypes(self.studio).validate_gates(result)
        return result

    async def execute(self, run_id, payload, document=None):
        if document and document.get("graph", {}).get("version") == 2:
            return await self.structured.execute(run_id, payload, document)
        if document is None:
            path = self.studio._path("graph-runs", payload.get("_evaluation_case") or run_id)
            if path.exists() and self.studio._read(path).get("graph", {}).get("version") == 2:
                return await self.structured.execute(run_id, payload)
        from nexus.workflows import FunctionNode, WorkflowContext

        if self.studio.team:
            self.studio.team.assert_execution(payload.get("requester_nexus_id"))
        state_id = payload.get("_evaluation_case") or run_id
        path = self.studio._path("graph-runs", state_id)
        if path.exists():
            state = self.studio._read(path)
        else:
            state = {
                "id": run_id,
                "graph": self.validate(document["graph"]),
                "workflow_id": payload["workflow_id"],
                "payload": {
                    key: payload[key]
                    for key in [
                        "kind",
                        "task",
                        "provider",
                        "model",
                        "effort",
                        "workflow_id",
                        "reference_context",
                        "expanded_task",
                        "artifact_refs",
                        "agent_snapshots",
                        "requester_nexus_id",
                    ]
                    if key in payload
                },
                "nodes": {},
                "outputs": {},
            }
            self.studio._write(path, state)
        graph = state["graph"]
        context = WorkflowContext(workflow_name="studio-" + run_id, root_input=payload["task"])
        incoming = {
            node["id"]: [edge for edge in graph["edges"] if edge["target"] == node["id"]]
            for node in graph["nodes"]
        }
        outputs = state["outputs"]
        statuses = state["nodes"]

        def active(edge):
            if edge["source"] == "START":
                return True
            if edge.get("choice"):
                try:
                    origin = next((n for n in graph["nodes"] if n["id"] == edge["source"]), {})
                    config = origin.get("decision", {})
                    out_val = outputs.get(edge["source"], "{}")
                    parsed = json.loads(out_val) if isinstance(out_val, str) else out_val
                    field = "choice"
                    if config.get("format") == "nexus-typed-decision/v1":
                        field = (
                            "selected_branch"
                            if edge["target"] == config.get("escalate")
                            else "routed_branch"
                        )
                    return (
                        statuses.get(edge["source"]) == "completed"
                        and parsed.get(field) == edge["choice"]
                    )
                except (ValueError, KeyError, TypeError):
                    return False
            return statuses.get(edge["source"]) == "completed" and (
                not edge["contains"]
                or edge["contains"].casefold() in outputs.get(edge["source"], "").casefold()
            )

        semaphore = asyncio.Semaphore(4)
        while True:
            ready = [
                node
                for node in graph["nodes"]
                if node["id"] not in statuses
                and all(
                    edge["source"] == "START" or edge["source"] in statuses
                    for edge in incoming[node["id"]]
                )
            ]
            if not ready:
                break
            for node in ready:
                if not any(active(edge) for edge in incoming[node["id"]]):
                    statuses[node["id"]] = "skipped"
                    self.studio.execution.record(
                        run_id,
                        "branch",
                        node_id=node["id"],
                        node_name=node.get("name", node["id"]),
                        status="skipped",
                    )
            ready = [node for node in ready if node["id"] not in statuses]
            gates = [node for node in ready if node["kind"] == "approval"]
            if gates:
                self.studio._write(path, state)
                self.studio._update_run(
                    run_id,
                    awaiting_approval=[{"id": n["id"], "name": n["name"]} for n in gates],
                    graph_nodes=statuses,
                    output=self.output(graph, outputs),
                    workflow_definition_id=state["workflow_id"],
                )
                raise StudioPausedError("Workflow awaits approval")
            if not ready:
                self.studio._write(path, state)
                continue
            self.studio._update_run(
                run_id, step=" + ".join(n["name"] for n in ready), graph_nodes=statuses
            )
            wrappers = []
            for node in ready:

                async def invoke(ctx, node_input=None, item=node):
                    prior = [
                        outputs[e["source"]]
                        for e in incoming[item["id"]]
                        if e["source"] != "START" and active(e)
                    ]
                    async with semaphore:
                        if self.studio.team:
                            self.studio.team.assert_execution(payload.get("requester_nexus_id"))
                        if item["kind"] in {"decision", "integration", "delay"}:
                            from nexus.core.studio_nodes import StudioNodeTypes

                            # Revalidate effects and gate topology against current connection policy.
                            engine = StudioNodeTypes(self.studio)
                            engine.validate_gates(graph)
                            output = await engine.execute(item, payload, run_id, prior)
                        elif item["kind"] == "tool":
                            if self.studio.team and not self.studio.team.tool_allowed(
                                item["tool_id"], self.studio
                            ):
                                raise PermissionError("Tool is not enabled for shared execution")
                            self.studio._update_run(run_id, provider="local-tool")
                            from nexus.tools import tool_registry

                            def render(value):
                                prior_str = "\n\n".join(
                                    p if isinstance(p, str) else json.dumps(p, default=str)
                                    for p in prior
                                )
                                if isinstance(value, str):
                                    return value.replace(
                                        "{task}", payload.get("expanded_task", payload["task"])
                                    ).replace("{previous}", prior_str)
                                if isinstance(value, list):
                                    return [render(entry) for entry in value]
                                if isinstance(value, dict):
                                    return {key: render(entry) for key, entry in value.items()}
                                return value

                            outcome = await tool_registry.call(
                                item["tool_id"], *render(item["args"]), **render(item["kwargs"])
                            )
                            if not outcome.ok:
                                raise RuntimeError(outcome.error or "Tool node failed")
                            output = (
                                outcome.output
                                if isinstance(outcome.output, str)
                                else json.dumps(outcome.output, ensure_ascii=False, default=str)
                            )
                        else:
                            task = item["prompt"].format(
                                task=payload.get("expanded_task", payload["task"])
                            )
                            if prior:
                                prior_str = "\n\n".join(
                                    p if isinstance(p, str) else json.dumps(p, default=str)
                                    for p in prior
                                )
                                task += "\nCompleted upstream nodes:\n" + prior_str
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
                                if payload.get(key)
                            }
                            result = await self.studio._invoke(
                                {**request, "agent_id": item["agent_id"]}, task, run_id
                            )
                            output = result.output
                    # Commit each completed node; sibling failure never claims its success.
                    outputs[item["id"]] = output
                    statuses[item["id"]] = "completed"
                    self.studio._write(path, state)
                    return output

                from nexus.core.studio_nodes import StudioNodeTypes

                wrappers.append(
                    FunctionNode(
                        StudioNodeTypes(self.studio).wrapper(invoke, node, run_id, payload),
                        name=node["id"],
                    )
                )
            try:
                await context.run_parallel(wrappers)
            finally:
                self.studio._write(path, state)
                self.studio._update_run(
                    run_id, graph_nodes=statuses, output=self.output(graph, outputs)
                )
        if len(statuses) != len(graph["nodes"]):
            raise RuntimeError("Graph stalled before completing all nodes")
        self.studio._update_run(run_id, awaiting_approval=[], graph_nodes=statuses)
        return self.output(graph, outputs)

    @staticmethod
    def output(graph, outputs):
        return "\n\n".join(
            f"{node['name']}\n{outputs[node['id']]}"
            for node in graph["nodes"]
            if node["id"] in outputs
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
            identity = checkpoint_id(self.studio, run_id)
            path = self.studio._path("graph-runs", identity)
            if path.exists() and self.studio._read(path).get("graph", {}).get("version") == 2:
                return self.structured.resume(run_id, decision, checkpoint=checkpoint)
            if self.studio._path("yaml-runs", identity).exists() and not path.exists():
                return self.studio.legacy.resume(run_id, decision, checkpoint=checkpoint)
            run = self.studio.run(run_id)
            if run["status"] != "waiting" or not run.get("awaiting_approval"):
                raise ValueError("This run is not awaiting an approval decision")
            state = self.studio._read(path)
            if decision == "reject":
                self.studio._update_run(
                    run_id,
                    status="cancelled",
                    awaiting_approval=[],
                    error="Rejected by local operator",
                )
                return self.studio.run(run_id)
            if self.studio.team:
                self.studio.team.assert_execution(state["payload"].get("requester_nexus_id"))
            for gate in run["awaiting_approval"]:
                state["nodes"][gate["id"]] = "completed"
                state["outputs"][gate["id"]] = "Approved by local operator\n" + "\n\n".join(
                    state["outputs"].get(edge["source"], "")
                    for edge in state["graph"]["edges"]
                    if edge["target"] == gate["id"]
                )
            self.studio._write(path, state)
            self.studio._update_run(run_id, status="queued", awaiting_approval=[])
            self.studio.resume_workflow(
                run_id, continuation_payload(self.studio, run_id, state["payload"], "resume_graph")
            )
            return self.studio.run(run_id)

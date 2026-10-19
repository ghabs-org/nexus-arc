"""Versioned structured workflows with bounded maps, loops and durable gates."""

import asyncio
import hashlib
import json
import re
import time
from copy import copy, deepcopy

from nexus.core.studio_catalog import _text
from nexus.core.studio_graph import StudioPausedError
from nexus.core.studio_workflow_cancellation import (
    CleanupEffectUncertainError,
    WorkflowCancellation,
    WorkflowCancelledError,
)
from nexus.core.studio_workflow_policy import workflow_limits
from nexus.core.studio_workflow_validation import (
    deterministic_condition,
    schema_definition,
    validate_mappings,
)


def resolve(value, context):
    if context.get("_mapping_language") == "interop-template/v1":
        from nexus.core.studio_interop_values import resolve_value

        return resolve_value(value, context)
    if isinstance(value, dict):
        if set(value) == {"template"} and context.get("_mapping_language") == "nexus-template/v1":
            from nexus.core.studio_interop_mappings import template_text

            return template_text(value["template"], lambda part: resolve(part, context))
        if set(value) == {"ref"}:
            path = value["ref"]
            if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z_][\w.-]{0,255}", path):
                raise ValueError("Invalid workflow data reference")
            current = context
            for key in path.split("."):
                try:
                    current = current[int(key)] if isinstance(current, list) else current[key]
                except (KeyError, TypeError, ValueError, IndexError) as exc:
                    raise ValueError(f"Workflow reference is unavailable: {path}") from exc
            return deepcopy(current)
        return {key: resolve(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, context) for item in value]
    return value


def condition(expression, context):
    deterministic_condition(expression)
    if set(expression) == {"interop_condition"}:
        from nexus.core.studio_interop_conditions import evaluate_condition

        return evaluate_condition(expression["interop_condition"], context).value is True
    left, right = resolve(expression["left"], context), resolve(expression["right"], context)
    operations = {
        "eq": lambda: left == right,
        "ne": lambda: left != right,
        "gt": lambda: left > right,
        "gte": lambda: left >= right,
        "lt": lambda: left < right,
        "lte": lambda: left <= right,
        "contains": lambda: right in left,
        "in": lambda: left in right,
    }
    if expression["op"] not in operations:
        raise ValueError("Unknown deterministic condition operator")
    try:
        return bool(operations[expression["op"]]())
    except TypeError as exc:
        raise ValueError("Condition values have incompatible types") from exc


def validate_schema(schema, value):
    if schema is not None:
        schema_definition(schema)
        import jsonschema

        try:
            jsonschema.Draft202012Validator(schema).validate(value)
        except jsonschema.ValidationError:
            raise ValueError("Workflow value does not match its declared schema") from None


class StudioWorkflowV2:
    def __init__(self, studio):
        self.studio = studio

    def validate(self, graph, depth=0, agent_ids=None, connections=None, limits=None, roots=None):
        limits = workflow_limits(limits or self.studio.workflow_limits)
        roots = set(roots or {"input"})
        if not isinstance(graph, dict) or set(graph) - {
            "version",
            "mapping_language",
            "interop_body",
            "interop_exit_handlers",
            "nodes",
            "edges",
            "input_schema",
            "output_schema",
            "output_mapping",
            "on_success",
            "on_error",
        }:
            raise ValueError("Unsupported structured workflow fields")
        if graph.get("mapping_language") not in {None, "nexus-template/v1", "interop-template/v1"}:
            raise ValueError("Unsupported workflow mapping language")
        interop = graph.get("mapping_language") == "interop-template/v1"
        if "interop_body" in graph and (not interop or type(graph["interop_body"]) is not bool):
            raise ValueError("External body scope requires its versioned mapping language")
        if graph.get("interop_body") and depth == 0:
            raise ValueError("External body scope must be nested under a collection")
        if interop:
            roots |= {"interop_input", "interop_nodes", "interop_workflow"}
        if depth >= limits["nesting_depth"]:
            raise ValueError(f"Workflow nesting exceeds {limits['nesting_depth']} levels")
        nodes, edges = graph.get("nodes"), graph.get("edges", [])
        if (
            not isinstance(nodes, list)
            or not 1 <= len(nodes) <= 40
            or not isinstance(edges, list)
            or len(edges) > 160
        ):
            raise ValueError("Use 1–40 workflow nodes and at most 160 edges")
        result, identities = deepcopy(graph), set()
        for node in result["nodes"]:
            if not isinstance(node, dict) or set(node) - {
                "id",
                "name",
                "kind",
                "depends_on",
                "input",
                "input_schema",
                "output_schema",
                "agent_id",
                "tool_id",
                "args",
                "kwargs",
                "prompt",
                "invocation_instruction",
                "connector_id",
                "action",
                "arguments",
                "tool",
                "decision",
                "delay_seconds",
                "retries",
                "retry_policy",
                "timeout_seconds",
                "body",
                "items",
                "max_items",
                "concurrency",
                "condition",
                "max_iterations",
                "workflow_id",
                "cases",
                "default",
                "position",
                "result_mode",
                "interop_body_id",
                "when",
            }:
                raise ValueError("Unsupported structured workflow node fields")
            identity = node.get("id")
            if (
                not isinstance(identity, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identity)
                or identity in identities
            ):
                raise ValueError("Workflow node IDs must be unique lowercase identifiers")
            identities.add(identity)
            node["name"] = _text(node.get("name", identity), "Node name", 160)
            if node.get("position") is not None:
                position = node["position"]
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
            kind = node.get("kind", "agent")
            node["kind"] = kind
            if kind not in {
                "agent",
                "tool",
                "integration",
                "approval",
                "delay",
                "decision",
                "map",
                "loop",
                "workflow",
                "switch",
                "input",
            }:
                raise ValueError("Unsupported structured workflow node kind")
            if any(k in node for k in ("result_mode", "interop_body_id", "when")) and not interop:
                raise ValueError("External runtime settings require interop-template/v1")
            if "result_mode" in node and (
                node["result_mode"] != "interop" or kind not in {"loop", "switch", "decision"}
            ):
                raise ValueError("External result mode is supported for loop, switch and decision nodes")
            if "interop_body_id" in node and (
                kind != "loop"
                or node.get("result_mode") != "interop"
                or not isinstance(node["interop_body_id"], str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", node["interop_body_id"])
            ):
                raise ValueError("External loop body aliases require a lowercase node identifier")
            if "when" in node:
                deterministic_condition(node["when"])
                if set(node["when"]) != {"interop_condition"}:
                    raise ValueError("External when requires a parsed external condition")
            if (
                kind == "loop"
                and "delay_seconds" in node
                and (
                    type(node["delay_seconds"]) not in {int, float}
                    or not 0 <= node["delay_seconds"] <= 300
                )
            ):
                raise ValueError("Loop delay must be between 0 and 300 seconds")
            timeout = node.setdefault("timeout_seconds", 300)
            if type(timeout) not in {int, float} or not 1 <= timeout <= 3600:
                raise ValueError("Node timeout must be between 1 and 3600 seconds")
            if "retry_policy" in node:
                from nexus.core.studio_workflow_retry import validate_retry

                validate_retry(node["retry_policy"])
                if node.get("retries", 0):
                    raise ValueError("Use retries or retry_policy, not both")
            retries = node.setdefault("retries", 0)
            if type(retries) is not int or not 0 <= retries <= 3:
                raise ValueError("Node retries must be between zero and three")
            retries = max(retries, node.get("retry_policy", {}).get("limit", 0))
            if retries and kind not in {"agent", "delay"}:
                raise ValueError(
                    "Automatic retries are limited to generation-only agents and delays"
                )
            if kind == "agent":
                if agent_ids is not None and node.get("agent_id") in agent_ids:
                    agent = (
                        agent_ids[node["agent_id"]]
                        if isinstance(agent_ids, dict)
                        else {"provider": "codex"}
                    )
                else:
                    agent = self.studio.definition("agents", node.get("agent_id"))["spec"]
                if retries:
                    from nexus.core.studio_workflow_retry import generation_retry_only

                    generation_retry_only(agent)
            if "invocation_instruction" in node:
                if kind != "agent":
                    raise ValueError("Invocation instructions require an agent node")
                _text(node["invocation_instruction"], "Invocation instructions", 4000)
            if kind == "agent":
                _text(node.get("prompt", "{task}"), "Node prompt", 16000)
            if kind == "tool":
                for field, expected in [("args", list), ("kwargs", dict)]:
                    value = node.get(field, [] if field == "args" else {})
                    if not (isinstance(value, dict) and set(value) == {"ref"}) and not isinstance(
                        value, expected
                    ):
                        raise ValueError(
                            "Tool args must be a list and kwargs an object or data reference"
                        )
                if len(json.dumps([node.get("args", []), node.get("kwargs", {})])) > 65536:
                    raise ValueError("Tool arguments exceed 64 KiB")
                from nexus.tools import tool_registry

                if not tool_registry.get(node.get("tool_id")):
                    raise ValueError("Choose a registered workflow tool")
                if self.studio.team and not self.studio.team.tool_allowed(
                    node["tool_id"], self.studio
                ):
                    raise PermissionError("Tool is not enabled in this workspace")
            if kind in {"integration", "decision", "delay"}:
                from nexus.core.studio_nodes import StudioNodeTypes

                node.update(StudioNodeTypes(self.studio).validate(node, connections))
            if kind in {"map", "loop"}:
                node["body"] = self.validate(
                    node.get("body"),
                    depth + 1,
                    agent_ids,
                    connections,
                    limits,
                    roots | ({"item", "index"} if kind == "map" else {"iteration", "previous"}),
                )
            if kind == "map":
                for field, default, maximum in [
                    ("max_items", min(100, limits["map_items"]), limits["map_items"]),
                    ("concurrency", min(4, limits["map_concurrency"]), limits["map_concurrency"]),
                ]:
                    value = node.setdefault(field, default)
                    if type(value) is not int or not 1 <= value <= maximum:
                        raise ValueError(f"{field} must be between one and {maximum}")
                if "items" not in node:
                    raise ValueError("Map nodes require items")
            if (
                kind == "loop"
                and node.get("result_mode") == "interop"
                and (
                    not node.get("interop_body_id")
                    or not isinstance(node.get("condition"), dict)
                    or set(node["condition"]) != {"interop_condition"}
                )
            ):
                raise ValueError("External loops require a body alias and parsed condition")
            if kind == "loop":
                maximum = node.setdefault("max_iterations", min(100, limits["loop_iterations"]))
                if type(maximum) is not int or not 1 <= maximum <= limits["loop_iterations"]:
                    raise ValueError(
                        f"Loop limit must be between one and {limits['loop_iterations']}"
                    )
                if not isinstance(node.get("condition"), dict):
                    raise ValueError("Loop nodes require a deterministic condition")
            if kind == "workflow":
                self.studio.definition("workflows", node.get("workflow_id"))
            if kind == "switch":
                cases = node.get("cases")
                if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
                    raise ValueError("Switch nodes require 1–20 cases")
                for case in cases:
                    if not isinstance(case, dict) or set(case) != {"condition", "value"}:
                        raise ValueError("Switch cases require condition and value")
        for node in result["nodes"]:
            if node["kind"] == "switch" and node.get("result_mode") == "interop":
                if any(
                    not isinstance(c["value"], str)
                    or c["value"] not in identities
                    or not isinstance(c["condition"], dict)
                    or set(c["condition"]) != {"interop_condition"}
                    for c in node["cases"]
                ) or (
                    node.get("default", "")
                    and (not isinstance(node["default"], str) or node["default"] not in identities)
                ):
                    raise ValueError(
                        "External switch cases require existing targets and parsed conditions"
                    )
        aliases = [n["interop_body_id"] for n in result["nodes"] if "interop_body_id" in n]
        if set(aliases) & identities:
            raise ValueError("External body aliases must be separate from graph nodes")
        incoming = {identity: [] for identity in identities}
        pairs = set()
        for edge in edges:
            if (
                not isinstance(edge, dict)
                or set(edge) - {"source", "target", "choice", "contains"}
                or edge.get("target") not in identities
                or edge.get("source") not in identities | {"START"}
            ):
                raise ValueError("Workflow edges must reference existing nodes")
            if (edge["source"], edge["target"]) in pairs:
                raise ValueError("Workflow edges must be unique")
            pairs.add((edge["source"], edge["target"]))
            if any(
                not isinstance(edge.get(key, ""), str) or len(edge.get(key, "")) > 1000
                for key in ("choice", "contains")
            ):
                raise ValueError("Workflow edge conditions must be text up to 1000 characters")
            if edge["source"] == "START" and (edge.get("choice") or edge.get("contains")):
                raise ValueError("START edges cannot have conditions")
            incoming[edge["target"]].append(edge)
        for node in result["nodes"]:
            dependencies = node.get("depends_on", [])
            if not isinstance(dependencies, list) or any(d not in identities for d in dependencies):
                raise ValueError("Dependencies must reference existing nodes")
            for dependency in dependencies:
                edge = {"source": dependency, "target": node["id"]}
                if not any(e["source"] == dependency for e in incoming[node["id"]]):
                    incoming[node["id"]].append(edge)
        result["edges"] = [edge for edges in incoming.values() for edge in edges]
        ordered = set()
        while True:
            ready = {
                i
                for i, values in incoming.items()
                if i not in ordered
                and all(e["source"] == "START" or e["source"] in ordered for e in values)
            }
            if not ready:
                break
            ordered.update(ready)
        if ordered != identities:
            raise ValueError("Workflow contains cyclic dependencies")
        # A shared alias is workflow-wide. Require an explicit dependency order,
        # rather than silently choosing a winner between concurrent controllers.
        ancestors = {}

        def parents(identity):
            if identity not in ancestors:
                ancestors[identity] = set()
                for edge in incoming[identity]:
                    source = edge["source"]
                    if source != "START":
                        ancestors[identity].add(source)
                        ancestors[identity].update(parents(source))
            return ancestors[identity]

        for alias in set(aliases):
            controllers = [n["id"] for n in result["nodes"] if n.get("interop_body_id") == alias]
            for index, first in enumerate(controllers):
                for second in controllers[index + 1 :]:
                    if first not in parents(second) and second not in parents(first):
                        raise ValueError("Shared external loop bodies require dependency-ordered controllers")
        from nexus.core.studio_connections import READ_ACTIONS

        kinds = {node["id"]: node["kind"] for node in result["nodes"]}
        for node in result["nodes"]:
            if node["kind"] == "integration" and node["action"] not in READ_ACTIONS:
                parents = incoming[node["id"]]
                if not parents or any(kinds.get(e["source"]) != "approval" for e in parents):
                    raise ValueError("Every mutating integration requires a direct approval gate")
        for key in ("on_success", "on_error"):
            if graph.get(key):
                result[key] = self.validate(
                    graph[key], depth + 1, agent_ids, connections, limits, {"input"}
                )
        if "interop_exit_handlers" in graph:
            handlers = result["interop_exit_handlers"]
            if not interop or not isinstance(handlers, list) or not 1 <= len(handlers) <= 4:
                raise ValueError("External exit handlers require a bounded list and interop-template/v1")
            if graph.get("on_success") or graph.get("on_error"):
                raise ValueError("Use external exit handlers or native outcome handlers, not both")
            phases, handler_ids = set(), set(identities)
            for handler in handlers:
                if (
                    not isinstance(handler, dict)
                    or set(handler) != {"phase", "body"}
                    or handler["phase"] not in ("always", "on_success", "on_failure", "on_cancel")
                    or handler["phase"] in phases
                ):
                    raise ValueError("Use distinct supported external exit phases")
                phases.add(handler["phase"])
                body = self.validate(
                    handler["body"], depth + 1, agent_ids, connections, limits, roots
                )
                if not body.get("interop_body") or len(body["nodes"]) != 1:
                    raise ValueError("External exit handler requires one scoped body node")
                identity = body["nodes"][0]["id"]
                if identity in identities:
                    raise ValueError("External exit handlers cannot also be regular nodes")
                handler_ids.add(identity)
                handler["body"] = body
            from nexus.core.studio_workflow_validation import references

            known = handler_ids | set(aliases)
            for handler in handlers:
                for path in references(handler["body"]):
                    if path.startswith("interop_nodes.") and path.split(".")[1] not in known:
                        raise ValueError("External exit capture must name a workflow node")
            result["interop_exit_handlers"] = handlers
        from nexus.core.studio_decisions import validate_routes

        validate_routes(result)
        validate_mappings(result, roots)
        result["version"] = 2
        return result

    def prepare(self, graph, payload, *, validate_input=True):
        limits = workflow_limits(payload.get("_workflow_limits", self.studio.workflow_limits))
        agents = payload.get("agent_snapshots")
        if payload.get("provider"):
            agents = deepcopy(agents if agents is not None else self.studio.versions.snapshots())
            for agent in agents.values():
                agent["provider"] = payload["provider"]
        snapshots = deepcopy(payload.get("workflow_snapshots", {}))
        shadow = copy(self.studio)
        original_definition = self.studio.definition
        shadow.definition = lambda kind, identity: (
            snapshots[identity]
            if kind == "workflows" and identity in snapshots
            else original_definition(kind, identity)
        )
        validator = StudioWorkflowV2(shadow)
        root = validator.validate(graph, agent_ids=agents, limits=limits)

        def visit(part, depth, chain):
            if depth >= limits["nesting_depth"]:
                raise ValueError(f"Workflow nesting exceeds {limits['nesting_depth']} levels")
            for node in part["nodes"]:
                if node["kind"] in {"map", "loop"}:
                    visit(node["body"], depth + 1, chain)
                if node["kind"] == "workflow":
                    identity = node["workflow_id"]
                    if identity in chain:
                        raise ValueError(
                            "Nested workflows contain a recursive cycle; use a bounded loop"
                        )
                    target = snapshots.get(identity)
                    if not target or target.get("graph", {}).get("version") != 2:
                        raise ValueError("Nested workflows require a pinned version-2 graph")
                    target["graph"] = validator.validate(
                        target["graph"], agent_ids=agents, limits=limits
                    )
                    visit(target["graph"], depth + 1, {*chain, identity})
            for key in ("on_success", "on_error"):
                if part.get(key):
                    visit(part[key], depth + 1, chain)
            for handler in part.get("interop_exit_handlers", []):
                visit(handler["body"], depth + 1, chain)

        visit(root, 0, {payload.get("workflow_id")})
        if validate_input:
            validate_schema(
                root.get("input_schema"), payload.get("workflow_input", payload["task"])
            )
        return root, snapshots

    async def execute(self, run_id, payload, document=None):
        path = self.studio._path("graph-runs", payload.get("_evaluation_case") or run_id)
        limits = workflow_limits(payload.get("_workflow_limits", self.studio.workflow_limits))
        with self.studio.lock:
            if path.exists():
                state = self.studio._read(path)
            else:
                prepared, snapshots = self.prepare(document["graph"], payload)
                state = {
                    "id": run_id,
                    "graph": prepared,
                    "limits": limits,
                    "workflow_id": payload["workflow_id"],
                    "payload": {**deepcopy(payload), "workflow_snapshots": snapshots},
                    "nodes": {},
                    "outputs": {},
                    "started": {},
                }
                self.studio._write(path, state)
        payload = state["payload"]
        limits = workflow_limits(state.get("limits", limits))
        semaphore = asyncio.Semaphore(limits["map_concurrency"])

        def persist():
            with self.studio.lock:
                self.studio._write(path, state)
                self.studio._update_run(run_id, graph_nodes=dict(state["nodes"]))

        cancellation = WorkflowCancellation(self.studio, run_id, state, persist)

        async def run_graph(graph, context, scope="root", depth=0):
            if graph.get("mapping_language") == "interop-template/v1" and not graph.get("interop_body"):
                context = {**context, "interop_input": context["input"], "interop_nodes": {}}
            cancellation.enter(graph, context, scope, depth)
            try:
                result = await run_graph_scoped(graph, context, scope, depth)
            except (StudioPausedError, asyncio.CancelledError):
                raise
            except Exception:
                cancellation.close(scope)
                raise
            cancellation.close(scope)
            return result

        async def run_graph_scoped(graph, context, scope="root", depth=0):
            if scope != "root" and not any(
                graph.get(k) for k in ("on_success", "on_error", "interop_exit_handlers")
            ):
                return await run_graph_body(graph, context, scope, depth)
            outcomes = state.setdefault("scope_outcomes", {})
            outcome = state.get("outcome") if scope == "root" else outcomes.get(scope)
            if outcome is None:
                try:
                    body_graph = (
                        {**graph, "output_mapping": {"ref": "nodes"}, "output_schema": None}
                        if graph.get("interop_exit_handlers")
                        else graph
                    )
                    work = run_graph_body(body_graph, context, scope, depth)
                    result = await asyncio.wait_for(work, 1800) if scope == "root" else await work
                except StudioPausedError:
                    raise
                except Exception as exc:
                    outcome = {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "node_id": state.get("failed_nodes", {}).get(scope),
                    }
                else:
                    outcome = {"status": "completed", "result": result}
                if scope == "root":
                    state["outcome"] = outcome
                else:
                    outcomes[scope] = outcome
                persist()
            if graph.get("interop_exit_handlers"):
                # Reconstruct direct completed outputs after body failure or recovery.
                outputs = {
                    key[len(scope) + 1 :]: value
                    for key, value in state["outputs"].items()
                    if key.startswith(scope + "/") and "/" not in key[len(scope) + 1 :]
                }
                terminal = {"failed": "failure", "cancelled": "cancelled", "completed": "success"}[
                    outcome["status"]
                ]
                workflow = {
                    "status": terminal,
                    "error": (
                        {"message": outcome.get("message"), "node_id": outcome.get("node_id")}
                        if terminal == "failure"
                        else None
                    ),
                }
                failures = state.setdefault("exit_failures", {})
                for entry in sorted(
                    graph["interop_exit_handlers"], key=lambda item: item["phase"] != "always"
                ):
                    if entry["phase"] not in {
                        "always",
                        {
                            "failure": "on_failure",
                            "success": "on_success",
                            "cancelled": "on_cancel",
                        }[terminal],
                    }:
                        continue
                    handler_id = entry["body"]["nodes"][0]["id"]
                    handler_scope = scope + "/interop_exit/" + handler_id
                    if (
                        sum(
                            h["body"]["nodes"][0]["id"] == handler_id
                            for h in graph["interop_exit_handlers"]
                        )
                        > 1
                    ):
                        # Previously valid distinct-handler paths remain stable.
                        # A reused template needs one receipt per intentional phase.
                        handler_scope += "/" + entry["phase"]
                    if handler_scope in failures:
                        continue
                    if state.get("cancellation") is not None:
                        state["cancellation"]["current_handler"] = handler_scope
                        persist()
                    try:
                        outputs[handler_id] = await run_graph(
                            entry["body"],
                            {
                                **context,
                                "interop_input": context.get("interop_input", context["input"]),
                                "interop_nodes": outputs,
                                "interop_workflow": workflow,
                            },
                            handler_scope,
                            depth + 1,
                        )
                        state["outputs"][scope + "/" + handler_id] = outputs[handler_id]
                        persist()
                    except StudioPausedError:
                        raise
                    except Exception as error:
                        failures[handler_scope] = type(error).__name__
                        self.studio.execution.record(
                            run_id,
                            "cleanup",
                            node_id=handler_scope,
                            status="failed",
                            message=type(error).__name__,
                        )
                        self.studio._update_run(
                            run_id,
                            workflow_warnings=[
                                {"node_id": key, "error_type": value}
                                for key, value in failures.items()
                            ],
                        )
                        persist()
                    if state.get("cancellation") is not None:
                        state["cancellation"].pop("current_handler", None)
                        persist()
                if terminal == "success":
                    result = resolve(
                        graph.get("output_mapping", {"ref": "nodes"}),
                        {
                            **context,
                            "nodes": outputs,
                            "interop_input": context.get("interop_input", context["input"]),
                            "_mapping_language": graph.get("mapping_language"),
                            "_interop_output_mapping": True,
                        },
                    )
                    validate_schema(graph.get("output_schema"), result)
                    return result
            if outcome["status"] == "cancelled":
                raise WorkflowCancelledError
            handler = "on_error" if outcome["status"] == "failed" else "on_success"
            if graph.get(handler):
                handler_input = (
                    {"error": outcome["error_type"]} if handler == "on_error" else outcome["result"]
                )
                handler_scope = handler if scope == "root" else scope + "/" + handler
                await run_graph(graph[handler], {"input": handler_input}, handler_scope, depth + 1)
            if outcome["status"] == "failed":
                raise ValueError(outcome["message"] or outcome["error_type"])
            return outcome["result"]

        async def run_graph_body(graph, context, scope, depth):
            context = {**context, "_mapping_language": graph.get("mapping_language")}
            if graph.get("mapping_language") == "interop-template/v1" and not graph.get("interop_body"):
                context.update(interop_input=context["input"], interop_nodes={})
            if depth >= limits["nesting_depth"]:
                raise ValueError(f"Workflow nesting exceeds {limits['nesting_depth']} levels")
            validate_schema(graph.get("input_schema"), context["input"])
            outputs, statuses = {}, {}
            aliases = [n["interop_body_id"] for n in graph["nodes"] if n.get("interop_body_id")]
            shared_aliases = {alias for alias in aliases if aliases.count(alias) > 1}
            incoming = {
                n["id"]: [e for e in graph["edges"] if e["target"] == n["id"]]
                for n in graph["nodes"]
            }

            def active(edge):
                source = edge["source"]
                if source == "START":
                    return True
                if statuses.get(source) != "completed":
                    return False
                value = outputs.get(source)
                if edge.get("choice"):
                    origin = next(n for n in graph["nodes"] if n["id"] == source)
                    config = origin.get("decision", {})
                    field = "selected_branch" if origin.get("result_mode") == "interop" else "choice"
                    if config.get("format") == "nexus-typed-decision/v1":
                        field = (
                            "selected_branch"
                            if edge["target"] == config.get("escalate")
                            else "routed_branch"
                        )
                    return isinstance(value, dict) and value.get(field) == edge["choice"]
                return (
                    not edge.get("contains") or edge["contains"].casefold() in str(value).casefold()
                )

            async def run_node(node):
                key = scope + "/" + node["id"]
                saved_retry = state.get("retry_attempts", {}).get(key, {})
                if (
                    saved_retry.get("terminal") == "completed"
                    and state["nodes"].get(key) != "completed"
                ):
                    # The acknowledged result was committed before the process stopped.
                    state["outputs"][key] = saved_retry["value"]
                    state["nodes"][key] = "completed"
                    persist()
                if state["nodes"].get(key) == "completed":
                    outputs[node["id"]] = state["outputs"][key]
                    if node.get("interop_body_id"):
                        alias = node["interop_body_id"]
                        outputs[alias] = state["outputs"][scope + "/" + alias]
                    statuses[node["id"]] = "completed"
                    return
                local = {**context, "nodes": outputs}
                if "when" in node and not condition(node["when"], local):
                    state["nodes"][key] = statuses[node["id"]] = "skipped"
                    self.studio.execution.record(
                        run_id, "branch", node_id=key, node_name=node["name"], status="skipped"
                    )
                    persist()
                    return
                node_input = resolve(
                    node.get("input", {"ref": "input"}), {**local, "_interop_input_mapping": True}
                )
                local["input"] = node_input
                validate_schema(node.get("input_schema"), node_input)
                kind = node["kind"]
                args, kwargs, arguments = [], {}, {}
                if kind == "tool":
                    args, kwargs = (
                        resolve(node.get("args", []), local),
                        resolve(node.get("kwargs", {}), {**local, "_interop_input_mapping": True}),
                    )
                    if (
                        not isinstance(args, list)
                        or not isinstance(kwargs, dict)
                        or len(json.dumps([args, kwargs])) > 65536
                    ):
                        raise ValueError(
                            "Resolved tool args must be a list and kwargs an object, up to 64 KiB"
                        )
                if kind == "integration":
                    arguments = resolve(node.get("arguments", {}), local)
                    if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 64000:
                        raise ValueError(
                            "Resolved integration arguments must be an object up to 64 KiB"
                        )
                if kind == "input":
                    inputs = self.studio.run(run_id).get("awaiting_input", [])
                    inputs = [item for item in inputs if item["id"] != key]
                    self.studio._update_run(
                        run_id,
                        awaiting_input=[
                            *inputs,
                            {
                                "id": key,
                                "name": node["name"],
                                "schema": node.get("output_schema", {}),
                            },
                        ],
                    )
                    persist()
                    raise StudioPausedError("Workflow requires input")
                if kind == "approval":
                    gates = self.studio.run(run_id).get("awaiting_approval", [])
                    gates = [gate for gate in gates if gate["id"] != key]
                    self.studio._update_run(
                        run_id,
                        awaiting_approval=[
                            *gates,
                            {"id": key, "name": node["name"], "schema": node.get("output_schema")},
                        ],
                    )
                    persist()
                    raise StudioPausedError("Structured workflow awaits approval")
                if kind == "agent" and (
                    node.get("retries", 0) or node.get("retry_policy", {}).get("limit", 0)
                ):
                    from nexus.core.studio_workflow_retry import generation_retry_only

                    agent = payload.get("agent_snapshots", {}).get(node["agent_id"])
                    if agent is None:
                        agent = self.studio.definition("agents", node["agent_id"])["spec"]
                    generation_retry_only(agent, payload.get("provider"))
                # Unknown tool outcomes are never replayed after process loss.
                if key in state["started"] and kind == "tool":
                    if state.get("cancellation"):
                        raise CleanupEffectUncertainError(
                            "Cleanup tool outcome uncertain; inspect external effects"
                        )
                    raise ValueError(
                        "Tool outcome uncertain; inspect its external effects before restarting this workflow"
                    )
                if (
                    state.get("cancellation")
                    and key in state["started"]
                    and kind in {"agent", "integration"}
                ):
                    raise CleanupEffectUncertainError(
                        "Cleanup outcome uncertain; inspect external effects before starting another run"
                    )
                if (
                    len(state["started"]) >= limits["node_instances"]
                    and key not in state["started"]
                ):
                    raise ValueError(
                        f"Workflow exceeds {limits['node_instances']} executed node instances"
                    )
                state["started"][key] = time.time()
                persist()
                self.studio.execution.record(
                    run_id,
                    "node",
                    node_id=key,
                    node_name=node["name"],
                    phase=kind,
                    status="running",
                    tool=node.get("tool_id"),
                )

                async def work():
                    if kind == "map":
                        items = resolve(node["items"], local)
                        if not isinstance(items, list) or len(items) > node["max_items"]:
                            raise ValueError("Map input must be an array within its item limit")
                        # A bounded worker pool avoids allocating one task per item.
                        cursor = iter(enumerate(items))
                        results = [None] * len(items)

                        async def worker():
                            for index, value in cursor:
                                try:
                                    results[index] = await run_graph(
                                        node["body"],
                                        {
                                            **local,
                                            "input": value,
                                            "item": value,
                                            "index": index,
                                            "interop_nodes": {**local.get("interop_nodes", {}), **outputs},
                                        },
                                        f"{key}/{index}",
                                        depth + 1,
                                    )
                                except Exception as error:
                                    results[index] = error

                        await asyncio.gather(
                            *(worker() for _ in range(min(len(items), node["concurrency"])))
                        )
                        for value in results:
                            if isinstance(value, BaseException):
                                raise value
                        return results
                    if kind == "loop":
                        alias = node.get("interop_body_id")
                        shared = alias in shared_aliases
                        value = None
                        if shared:
                            # Persist the seed before dispatch: a resumed controller
                            # must not seed itself from its own later global output.
                            seed_key = key + "/interop_initial"
                            if seed_key not in state["outputs"]:
                                state["outputs"][seed_key] = deepcopy(outputs.get(alias))
                                persist()
                            value = deepcopy(state["outputs"][seed_key])
                        for index in range(node["max_iterations"]):
                            value = await run_graph(
                                node["body"],
                                {
                                    **local,
                                    "iteration": index,
                                    "previous": value,
                                    "interop_nodes": {**local.get("interop_nodes", {}), **outputs},
                                },
                                f"{key}/{index}",
                                depth + 1,
                            )
                            if shared:
                                state["outputs"][scope + "/" + alias] = outputs[alias] = value
                                persist()
                            evaluation = {**local, "result": value, "iteration": index}
                            if node.get("result_mode") == "interop":
                                from nexus.core.studio_interop_conditions import evaluate_condition

                                answer = evaluate_condition(
                                    node["condition"]["interop_condition"], evaluation
                                )
                                reason = (
                                    "condition_unresolved"
                                    if answer.unresolved and answer.value is None
                                    else "condition_false"
                                )
                                if answer.value is True and index + 1 == node["max_iterations"]:
                                    reason = "max_iterations"
                                if answer.value is not True or index + 1 == node["max_iterations"]:
                                    alias = node["interop_body_id"]
                                    state["outputs"][scope + "/" + alias] = outputs[alias] = value
                                    return {
                                        "iterations_completed": index + 1,
                                        "stopped_reason": reason,
                                    }
                            elif not condition(node["condition"], evaluation):
                                return value
                            if node.get("delay_seconds"):
                                await asyncio.sleep(node["delay_seconds"])
                        raise ValueError(
                            "Loop iteration limit reached while its condition remains true"
                        )
                    if kind == "workflow":
                        target = (
                            state["payload"].get("workflow_snapshots", {}).get(node["workflow_id"])
                        )
                        if not target or target.get("graph", {}).get("version") != 2:
                            raise ValueError("Nested workflows require a pinned version-2 graph")
                        return await run_graph(
                            target["graph"], {**local, "input": node_input}, key, depth + 1
                        )
                    if kind == "switch":
                        if node.get("result_mode") == "interop":
                            from nexus.core.studio_interop_conditions import evaluate_condition

                            unresolved = 0
                            for index, case in enumerate(node["cases"]):
                                answer = evaluate_condition(
                                    case["condition"]["interop_condition"], local
                                )
                                unresolved += int(answer.unresolved)
                                if answer.value is True:
                                    return {
                                        "selected_branch": case["value"],
                                        "selected_case_index": index,
                                        "cases_unresolved": unresolved,
                                    }
                            return {
                                "selected_branch": node.get("default", ""),
                                "selected_case_index": -1,
                                "cases_unresolved": unresolved,
                            }
                        for case in node["cases"]:
                            if condition(case["condition"], local):
                                return resolve(case["value"], local)
                        return resolve(node.get("default"), local)
                    async with semaphore:
                        if self.studio.team:
                            self.studio.team.assert_execution(payload.get("requester_nexus_id"))
                        if (
                            kind == "decision"
                            and node["decision"].get("format") == "nexus-typed-decision/v1"
                        ):
                            from nexus.core.studio_decisions import StudioTypedDecisions

                            return await StudioTypedDecisions(self.studio.workbench.jobs).execute(
                                node["decision"],
                                node_input,
                                payload,
                                run_id,
                                run_id + "-typed-" + hashlib.sha256(key.encode()).hexdigest()[:24],
                            )
                        if kind == "agent":
                            prompt = node.get("prompt", "{task}")
                            task = prompt.replace(
                                "{task}",
                                json.dumps(node_input, ensure_ascii=False)
                                if not isinstance(node_input, str)
                                else node_input,
                            )
                            if node.get("invocation_instruction"):
                                task += "\n\n" + node["invocation_instruction"]
                            result = await self.studio._invoke(
                                {**payload, "agent_id": node["agent_id"]}, task, run_id
                            )
                            try:
                                return json.loads(result.output)
                            except ValueError:
                                return result.output
                        if kind == "tool":
                            from nexus.tools import tool_registry

                            result = await tool_registry.call(
                                node["tool_id"],
                                *args,
                                **kwargs,
                            )
                            if not result.ok:
                                raise ValueError(result.error or "Workflow tool failed")
                            return result.output
                        from nexus.core.studio_nodes import StudioNodeTypes

                        item = {
                            **node,
                            "id": hashlib.sha256(key.encode()).hexdigest()[:32],
                            "arguments": arguments,
                        }
                        value = await StudioNodeTypes(self.studio).execute(
                            item,
                            {
                                **payload,
                                "task": node_input
                                if isinstance(node_input, str)
                                else json.dumps(node_input, ensure_ascii=False),
                            },
                            run_id,
                            [],
                        )
                        try:
                            return json.loads(value)
                        except (ValueError, TypeError):
                            return value

                started, status = time.monotonic(), "failed"
                try:
                    if "retry_policy" in node:
                        from nexus.core.studio_workflow_retry import retry_delay, retry_matches

                        policy = node["retry_policy"]
                        saved = state.setdefault("retry_attempts", {}).setdefault(key, {"next": 0})
                        while True:
                            if saved.get("terminal"):
                                if saved["terminal"] == "completed":
                                    value = saved["value"]
                                    break
                                raise ValueError(
                                    saved.get("message", "Workflow retry budget exhausted")
                                )
                            attempt = saved["next"]
                            if attempt > policy["limit"]:
                                raise ValueError(
                                    "Workflow retry budget exhausted after interrupted attempt"
                                )
                            await asyncio.sleep(max(0, saved.get("not_before", 0) - time.time()))
                            # Consume the budget before invocation. A lost acknowledgement cannot reset it.
                            saved.update(next=attempt + 1, not_before=0)
                            persist()
                            error = None
                            try:
                                value = await asyncio.wait_for(work(), node["timeout_seconds"])
                                validate_schema(node.get("output_schema"), value)
                            except StudioPausedError:
                                saved["next"] = attempt
                                persist()
                                raise
                            except Exception as exc:
                                error = exc
                            if attempt < policy["limit"] and retry_matches(policy, error):
                                saved["not_before"] = time.time() + retry_delay(policy, attempt)
                                persist()
                                continue
                            if error is not None:
                                saved.update(terminal="failed", message=str(error))
                                persist()
                                raise error
                            saved.update(terminal="completed", value=value)
                            persist()
                            break
                    else:
                        for attempt in range(node.get("retries", 0) + 1):
                            try:
                                value = await asyncio.wait_for(work(), node["timeout_seconds"])
                                break
                            except StudioPausedError:
                                raise
                            except Exception:
                                if attempt >= node.get("retries", 0):
                                    raise
                                await asyncio.sleep(min(2**attempt, 4))
                    validate_schema(node.get("output_schema"), value)
                    state["outputs"][key] = outputs[node["id"]] = value
                    state["nodes"][key] = statuses[node["id"]] = status = "completed"
                except asyncio.CancelledError:
                    status = "cancelled"
                    raise
                except StudioPausedError:
                    status = "waiting"
                    raise
                except Exception:
                    state.setdefault("failed_nodes", {})[scope] = node["id"]
                    raise
                finally:
                    trace = {
                        "id": run_id + "-" + hashlib.sha256(key.encode()).hexdigest()[:24],
                        "run_id": run_id,
                        "owner_id": payload.get("requester_nexus_id"),
                        "node_id": key,
                        "node_name": node["name"],
                        "kind": kind,
                        "status": status,
                        "duration_seconds": time.monotonic() - started,
                    }
                    self.studio._write(self.studio._path("node-traces", trace["id"]), trace)
                    self.studio.execution.record(
                        run_id,
                        "node",
                        node_id=key,
                        node_name=node["name"],
                        phase=kind,
                        status=status,
                        source_id=trace["id"],
                        duration_seconds=trace["duration_seconds"],
                        tool=node.get("tool_id"),
                    )
                    persist()

            def settle_decisions():
                from nexus.core.studio_decisions import FORMAT, reroute_choice

                settled = state.setdefault("decision_routes", {})
                for node in graph["nodes"]:
                    config = node.get("decision", {})
                    key = scope + "/" + node["id"]
                    output = outputs.get(node["id"], {})
                    if (
                        config.get("format") != FORMAT
                        or statuses.get(node["id"]) != "completed"
                        or not output.get("escalated")
                        or config.get("escalation_mode", "reroute") != "reroute"
                        or key in settled
                    ):
                        continue
                    review = config["escalate"]
                    if review not in statuses:
                        continue
                    branch = ""
                    if statuses[review] == "completed":
                        reviewer = next(n for n in graph["nodes"] if n["id"] == review)
                        branch = reroute_choice(
                            config,
                            outputs[review],
                            reviewer.get("decision") if reviewer["kind"] == "decision" else None,
                        )
                    output = {**output, "routed_branch": branch}
                    outputs[node["id"]] = state["outputs"][key] = output
                    settled[key] = True
                    record_id = run_id + "-typed-" + hashlib.sha256(key.encode()).hexdigest()[:24]
                    record_path = self.studio._path("decisions", record_id)
                    if record_path.exists():
                        record = self.studio._read(record_path)
                        self.studio._write(record_path, {**record, "result": output})
                    persist()

            while len(statuses) < len(graph["nodes"]):
                settle_decisions()
                ready = [
                    n
                    for n in graph["nodes"]
                    if n["id"] not in statuses
                    and all(
                        e["source"] == "START" or e["source"] in statuses for e in incoming[n["id"]]
                    )
                ]
                if not ready:
                    raise ValueError("Structured workflow stalled")
                tasks = []
                for node in ready:
                    edges = incoming[node["id"]]
                    route_edges = [edge for edge in edges if edge.get("choice")]
                    interop_route_blocked = (
                        (
                            graph.get("mapping_language") == "interop-template/v1"
                            or any(
                                next(n for n in graph["nodes"] if n["id"] == edge["source"])
                                .get("decision", {})
                                .get("format")
                                == "nexus-typed-decision/v1"
                                for edge in route_edges
                            )
                        )
                        and route_edges
                        and not all(active(edge) for edge in route_edges)
                    )
                    if edges and (interop_route_blocked or not any(active(e) for e in edges)):
                        statuses[node["id"]] = "skipped"
                        key = scope + "/" + node["id"]
                        if state["nodes"].get(key) != "skipped":
                            state["nodes"][key] = "skipped"
                            self.studio.execution.record(
                                run_id,
                                "branch",
                                node_id=key,
                                node_name=node["name"],
                                status="skipped",
                            )
                    else:
                        tasks.append(run_node(node))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for value in results:
                    if isinstance(value, BaseException):
                        raise value
            settle_decisions()
            value = resolve(
                graph.get("output_mapping", {"ref": "nodes"}),
                {**context, "nodes": outputs, "_interop_output_mapping": True},
            )
            validate_schema(graph.get("output_schema"), value)
            return value

        context = {"input": state["payload"].get("workflow_input", state["payload"]["task"])}
        if self.studio.run(run_id).get("cancel_requested"):
            await cancellation.execute(run_graph)
        try:
            result = await run_graph(state["graph"], context)
        except asyncio.CancelledError:
            if self.studio.run(run_id).get("cancel_requested") and not self.studio.closing:
                await cancellation.execute(run_graph)
            raise
        self.studio._update_run(run_id, awaiting_approval=[])
        return json.dumps(result, ensure_ascii=False, default=str)

    def resume(self, run_id, decision):
        if decision not in {"approve", "reject"}:
            raise ValueError("Choose approve or reject")
        with self.studio.lock:
            run = self.studio.run(run_id)
            if run["status"] != "waiting" or not run.get("awaiting_approval"):
                raise ValueError("Run is not waiting for approval")
            path = self.studio._path("graph-runs", run_id)
            state = self.studio._read(path)
            if decision == "reject":
                from nexus.core.studio_workflow_cancellation import request_cleanup

                if request_cleanup(self.studio, run_id):
                    return self.studio.run(run_id)
                self.studio._update_run(run_id, status="cancelled", awaiting_approval=[])
                return self.studio.run(run_id)
            for gate in run["awaiting_approval"]:
                validate_schema(gate.get("schema"), {"approved": True})
            for gate in run["awaiting_approval"]:
                state["nodes"][gate["id"]] = "completed"
                state["outputs"][gate["id"]] = {"approved": True}
            self.studio._write(path, state)
            self.studio._update_run(run_id, status="queued", awaiting_approval=[])
            self.studio.resume_workflow(run_id, {**state["payload"], "resume_graph": True})
            return self.studio.run(run_id)

    def resume_input(self, run_id, values):
        if not isinstance(values, dict):
            raise ValueError("Input values must be keyed by requested input ID")
        with self.studio.lock:
            run = self.studio.run(run_id)
            pending = run.get("awaiting_input", [])
            if run["status"] != "waiting" or not pending:
                raise ValueError("Workflow is not waiting for input")
            if set(values) - {item["id"] for item in pending}:
                raise ValueError("Unknown workflow input ID")
            path = self.studio._path("graph-runs", run_id)
            state = self.studio._read(path)
            for item in pending:
                if item["id"] in values:
                    validate_schema(item["schema"], values[item["id"]])
            for item in pending:
                if item["id"] in values:
                    state["nodes"][item["id"]] = "completed"
                    state["outputs"][item["id"]] = values[item["id"]]
            remaining = [item for item in pending if item["id"] not in values]
            self.studio._write(path, state)
            self.studio._update_run(run_id, awaiting_input=remaining)
            if not remaining:
                self.studio._update_run(run_id, status="queued")
                self.studio.resume_workflow(run_id, {**state["payload"], "resume_graph": True})
            return self.studio.run(run_id)

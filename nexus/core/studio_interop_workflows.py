"""Compile external collection/control nodes into the shared version-2 workflow engine."""

import re

from nexus.core.studio_interop_conditions import parse_condition
from nexus.core.studio_interop_mappings import normalize_indices, source_ref
from nexus.core.studio_interop_values import LANGUAGE, import_value

BASIC = {"agent", "tool", "workflow", "approval"}
COMMON = {
    "id",
    "type",
    "depends_on",
    "input",
    "input_schema_override",
    "output_schema_override",
    "timeout",
    "when",
    "retry_strategy",
}
SPECIFIC = {
    "agent": {"agent_name", "instruction"},
    "tool": {"tool_name", "connector", "tool"},
    "approval": set(),
    "workflow": {"workflow_name"},
    "map": {
        "node",
        "items",
        "with_items",
        "withItems",
        "with_param",
        "withParam",
        "max_items",
        "concurrency_limit",
    },
    "loop": {"node", "condition", "max_iterations", "delay"},
    "switch": {"cases", "default"},
    "decision": {
        "images",
        "questions",
        "route_on",
        "routes",
        "default",
        "min_confidence",
        "escalate",
        "escalation_mode",
        "on_provider_error",
        "model",
    },
}


def needs_compatibility(source):
    if not isinstance(source, dict):
        return False
    if "toolsets" in source:
        return True
    nodes = source.get("nodes", [])
    if isinstance(nodes, list) and any(
        isinstance(n, dict)
        and (
            n.get("type") in ("map", "loop", "switch", "decision")
            or "connector" in n
            or "tool" in n
            or n.get("type") == "approval"
            or "when" in n
            or "retry_strategy" in n
            or "instruction" in n
        )
        for n in nodes
    ):
        return True

    def operators(value):
        if isinstance(value, dict):
            return set(value) in ({"coalesce"}, {"concat"}) or any(
                operators(v) for v in value.values()
            )
        if isinstance(value, str):
            return bool(re.search(r"\{\{[^}]*\[[0-9]+\][^}]*\}\}", value))
        return isinstance(value, list) and any(operators(v) for v in value)

    return (
        "retry_strategy" in source
        or "on_exit" in source
        or "decision" in source
        or operators(source)
    )


class InteropWorkflowCompiler:
    def __init__(self, bridge):
        self.bridge = bridge
        self.issue, self.fields, self.attempt = bridge.issue, bridge.fields, bridge.attempt
        self.nodes, self.paths, self.owners = {}, {}, {}

    def reference(self, expression, scope, condition_body=None):
        if expression in {
            "workflow.status",
            "workflow.error",
            "workflow.error.message",
            "workflow.error.node_id",
        }:
            if not scope.get("exit"):
                raise ValueError("Workflow status/error is available only in exit handlers")
            return "interop_workflow" + expression[len("workflow") :], "input"
        if expression.startswith("workflow.input"):
            path = source_ref(expression)
            return "interop_input" + path[5:], "input"
        if (
            expression in {"_map_item", "_map_index"}
            or expression.startswith("_map_item.")
            or expression.startswith("_map_item[")
        ):
            if not scope.get("map"):
                raise ValueError("Map variables require a mapped body")
            normalized = normalize_indices(expression)
            return (
                "item" + normalized[len("_map_item") :]
                if normalized.startswith("_map_item")
                else "index"
            ), "input"
        if expression == "_loop_iteration":
            if not scope.get("loop") and condition_body is None:
                raise ValueError("Loop iteration requires a loop body")
            return "iteration", "input"
        path = source_ref(expression)
        parts = path.split(".")
        if parts[0] != "nodes" or parts[1] not in self.nodes:
            raise ValueError("Unknown external node reference")
        target, suffix = parts[1], "." + ".".join(parts[2:]) if len(parts) > 2 else ""
        if target == condition_body:
            return "result" + suffix, "node"
        if target == scope.get("previous") and scope.get("current") == target:
            return "previous" + suffix, "previous"
        if target in scope.get("members", set()):
            return "nodes." + target + suffix, "node"
        if scope.get("body"):
            return "interop_nodes." + target + suffix, "node"
        return path, "node"

    def value(self, value, scope, path):
        return self.attempt(
            path, lambda: import_value(value, lambda expression: self.reference(expression, scope))
        )

    def condition(self, value, scope, path, body=None):
        return self.attempt(
            path,
            lambda: parse_condition(
                value, lambda expression: self.reference(expression, scope, body)
            ),
            {"interop_condition": {"boolean": {"value": False}}},
        )

    def retry(self, source, path):
        source = self.fields(source, {"limit", "retry_policy", "backoff"}, path)
        backoff = self.fields(
            source.get("backoff", {}), {"duration", "factor", "max_duration"}, path + "/backoff"
        )

        def seconds(value):
            if not isinstance(value, str) or not re.fullmatch(r"\d+(?:\.\d+)?(?:ms|s|m|h)", value):
                raise ValueError("Use a bounded retry duration")
            match = re.fullmatch(r"([\d.]+)(ms|s|m|h)", value)
            return float(match[1]) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[match[2]]

        policy = {
            "limit": source.get("limit", 0),
            "policy": source.get("retry_policy", "OnFailure"),
            "backoff": {
                "seconds": self.attempt(
                    path + "/backoff/duration", lambda: seconds(backoff.get("duration", "1s")), 1
                ),
                "factor": backoff.get("factor", 2),
                "max_seconds": self.attempt(
                    path + "/backoff/max_duration",
                    lambda: seconds(backoff.get("max_duration", "30s")),
                    30,
                ),
            },
        }
        from nexus.core.studio_workflow_retry import validate_retry

        self.attempt(path, lambda: validate_retry(policy))
        return policy

    def node(self, identity, scope=None, chain=()):
        scope = {**(scope or {}), "current": identity}
        source, path = self.nodes[identity], self.paths[identity]
        kind = source.get("type")
        if identity in chain or len(chain) >= self.bridge.studio.workflow_limits["nesting_depth"]:
            self.issue(
                path + "/node", "recursive_body", "Collection bodies must be acyclic and bounded"
            )
            return {"id": identity, "kind": "delay", "delay_seconds": 0}
        if not isinstance(kind, str) or kind not in SPECIFIC:
            self.issue(
                path + "/type", "unsupported_node", "This external node type is not yet supported"
            )
            return {"id": identity, "kind": "delay", "delay_seconds": 0}
        self.fields(source, COMMON | SPECIFIC[kind], path)
        if (
            scope.get("body")
            and source.get("depends_on")
            and any(
                dependency not in scope.get("members", {identity})
                for dependency in source.get("depends_on", [])
            )
        ):
            self.issue(
                path + "/depends_on",
                "unsupported_body_dependency",
                "Declare external dependencies on the collection controller",
            )
        if kind in BASIC:
            projected = {
                k: v
                for k, v in source.items()
                if k in COMMON | SPECIFIC[kind] and k not in {"when", "retry_strategy"}
            }
            projected["input"] = {}
            node = self.bridge.workflow_node(projected, path, self.source, self.tools)
            mapped = self.value(source.get("input", "{{workflow.input}}"), scope, path + "/input")
            if node.get("kind") == "integration":
                mode = next(key for key in node["arguments"] if key != "operation_id")
                node["arguments"][mode] = mapped
            else:
                node["kwargs" if kind == "tool" else "input"] = mapped
        else:
            node = {"id": identity, "name": identity, "kind": kind}
            if "depends_on" in source:
                node["depends_on"] = source["depends_on"]
            from nexus.core.studio_interop_configuration import duration

            node["timeout_seconds"] = self.attempt(
                path + "/timeout",
                lambda: duration(
                    source.get("timeout", self.source.get("default_node_timeout", "300s"))
                ),
                300,
            )
            if "input" in source:
                node["input"] = self.value(source["input"], scope, path + "/input")
            for old, new in (
                ("input_schema_override", "input_schema"),
                ("output_schema_override", "output_schema"),
            ):
                if old in source:
                    node[new] = source[old]
        retry = source.get("retry_strategy", self.source.get("retry_strategy"))
        if kind == "decision":
            if "retry_strategy" in source:
                self.issue(
                    path + "/retry_strategy",
                    "unsupported_retry",
                    "Decision nodes use their bounded provider call budget, not node retry strategies",
                )
        elif "retry_strategy" in source or "retry_strategy" in self.source:
            node["retry_policy"] = self.retry(retry, path + "/retry_strategy")
        if "when" in source:
            node["when"] = self.condition(source["when"], scope, path + "/when")
        if kind in {"map", "loop"}:
            body = source.get("node")
            if not isinstance(body, str) or body not in self.nodes:
                self.issue(path + "/node", "missing_body", "Choose an existing body node")
                return node
            child_scope = {**scope, "body": True}
            if kind == "map":
                child_scope["map"] = True
            else:
                child_scope.update(loop=True, previous=body)
            node["body"] = self.body_graph(body, child_scope, (*chain, identity))
            if kind == "map":
                sources = [
                    key
                    for key in ("items", "with_items", "withItems", "with_param", "withParam")
                    if key in source
                ]
                if len(sources) != 1:
                    self.issue(
                        path + "/items",
                        "ambiguous_items",
                        "Supply exactly one external items source",
                    )
                    node["items"] = []
                else:
                    node["items"] = self.value(source[sources[0]], scope, path + "/" + sources[0])
                if "max_items" in source:
                    node["max_items"] = source["max_items"]
                maximum = source.get(
                    "max_items", min(100, self.bridge.studio.workflow_limits["map_items"])
                )
                if (
                    sources
                    and isinstance(source[sources[0]], list)
                    and type(maximum) is int
                    and len(source[sources[0]]) > maximum
                ):
                    self.issue(
                        path + "/" + sources[0],
                        "item_limit",
                        "Literal collection exceeds the configured map limit",
                    )
                if "concurrency_limit" in source:
                    node["concurrency"] = source["concurrency_limit"]
                    if source["concurrency_limit"] == 0:
                        self.issue(
                            path + "/concurrency_limit",
                            "unbounded_concurrency",
                            "Choose an explicit bounded concurrency within administrator policy",
                        )
            else:
                node.update(result_mode="interop", interop_body_id=body)
                node["condition"] = self.condition(
                    source.get("condition"), scope, path + "/condition", body
                )
                if "max_iterations" in source:
                    node["max_iterations"] = source["max_iterations"]
                if "delay" in source:
                    node["delay_seconds"] = (
                        0
                        if source["delay"] == "0s"
                        else self.attempt(path + "/delay", lambda: duration(source["delay"]), 0)
                    )
        if kind == "decision":
            from nexus.core.studio_decisions import FORMAT, validate_decision

            config = {
                **self.decision_config,
                "format": FORMAT,
                "questions": source.get("questions"),
            }
            for key in (
                "route_on",
                "routes",
                "default",
                "min_confidence",
                "escalate",
                "escalation_mode",
                "on_provider_error",
                "model",
            ):
                if key in source:
                    config[key] = source[key]
            self.attempt(path, lambda: validate_decision(config))
            if "images" in source:
                from nexus.core.studio_decision_images import capable, limits

                entries = source["images"]
                maximum, _ = limits(config)
                if not capable(config):
                    self.issue(
                        path + "/images",
                        "unsupported_image_model",
                        "Select an explicitly image-capable decision model",
                    )
                if (
                    not isinstance(entries, list)
                    or len(entries) > 128
                    or any(not isinstance(v, str) for v in entries)
                ):
                    self.issue(
                        path + "/images",
                        "invalid_images",
                        "Use at most 128 artifact references or templates",
                    )
                elif sum("{{" not in v for v in entries) > maximum:
                    self.issue(
                        path + "/images",
                        "image_limit",
                        "Literal image entries exceed configured max_images",
                    )
                else:
                    node["images"] = [
                        self.value(v, scope, path + "/images/" + str(i))
                        for i, v in enumerate(entries)
                    ]
            node.update(decision=config, result_mode="interop")
        if kind == "switch":
            node["result_mode"] = "interop"
            cases = source.get("cases")
            if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
                self.issue(path + "/cases", "invalid_cases", "Use 1–20 ordered switch cases")
                cases = []
            node["cases"] = []
            for index, case in enumerate(cases):
                where = path + "/cases/" + str(index)
                case = self.fields(case, {"condition", "when", "node", "then"}, where)
                expressions = [k for k in ("condition", "when") if k in case]
                targets = [k for k in ("node", "then") if k in case]
                if len(expressions) != 1 or len(targets) != 1:
                    self.issue(
                        where, "ambiguous_case", "Each case requires one condition and one target"
                    )
                    continue
                node["cases"].append(
                    {
                        "condition": self.condition(
                            case[expressions[0]], scope, where + "/" + expressions[0]
                        ),
                        "value": case[targets[0]],
                    }
                )
            node["default"] = source.get("default", "")
        return node

    def members(self, root):
        if root not in self.nodes:
            return set()
        result = {root}
        controllers = {
            identity
            for identity, node in self.nodes.items()
            if node.get("type") in ("map", "loop") and node.get("node") == root
        }
        changed = True
        while changed:
            changed = False
            targets = set()
            for identity in result:
                source = self.nodes[identity]
                if source.get("type") == "switch":
                    cases = source.get("cases", [])
                    candidates = [
                        case.get("node", case.get("then"))
                        for case in (cases if isinstance(cases, list) else [])
                        if isinstance(case, dict)
                    ] + [source.get("default")]
                    targets.update(value for value in candidates if isinstance(value, str))
                if source.get("type") == "decision":
                    routes = source.get("routes", {})
                    candidates = list(routes.values()) if isinstance(routes, dict) else []
                    candidates += [source.get(key) for key in ("default", "escalate")]
                    targets.update(value for value in candidates if isinstance(value, str))
            for identity, source in self.nodes.items():
                dependencies = source.get("depends_on", [])
                if (
                    identity not in result
                    and identity not in controllers
                    and (
                        identity in targets
                        or isinstance(dependencies, list)
                        and any(
                            isinstance(dependency, str) and dependency in result
                            for dependency in dependencies
                        )
                    )
                ):
                    result.add(identity)
                    changed = True
        return result

    def body_graph(self, root, scope, chain):
        members = self.members(root)
        scope = {**scope, "members": members, "body": True}
        nodes = [
            self.node(identity, scope, chain)
            for identity in [root, *(key for key in self.nodes if key in members and key != root)]
        ]
        result = {
            "version": 2,
            "mapping_language": LANGUAGE,
            "interop_body": True,
            "nodes": nodes,
            "edges": [],
            "output_mapping": self.value("{{" + root + ".output}}", {}, self.paths[root]),
        }
        self.wire(result)
        return result

    def wire(self, result, handlers=()):
        scope_ids = {node["id"] for node in result["nodes"]}
        for node in result["nodes"]:
            for dependency in (
                node.get("depends_on", []) if isinstance(node.get("depends_on", []), list) else []
            ):
                if dependency in self.owners and dependency not in scope_ids:
                    self.issue(
                        self.paths[node["id"]] + "/depends_on",
                        "body_dependency",
                        "Depend on the controller, not its body node",
                    )
            if node["kind"] == "decision":
                config = node["decision"]
                routes = config.get("routes", {})
                if not isinstance(routes, dict):
                    continue
                targets = list(routes.values()) + [
                    config[k] for k in ("default", "escalate") if k in config
                ]
                for target in dict.fromkeys(t for t in targets if isinstance(t, str)):
                    selected = self.nodes.get(target)
                    if (
                        not selected
                        or target not in scope_ids
                        or target == node["id"]
                        or not isinstance(selected.get("depends_on", []), list)
                        or node["id"] not in selected.get("depends_on", [])
                    ):
                        self.issue(
                            self.paths[node["id"]],
                            "invalid_target",
                            "Decision targets must exist, be regular nodes and depend on their decision",
                        )
                    else:
                        result["edges"].append(
                            {"source": node["id"], "target": target, "choice": target}
                        )
                if config.get("escalate") and config.get("escalation_mode", "reroute") == "reroute":
                    escalate = config["escalate"]
                    for target in {
                        t for t in [*routes.values(), config.get("default")] if isinstance(t, str)
                    }:
                        if target != escalate:
                            result["edges"].append({"source": escalate, "target": target})
            if node["kind"] == "switch":
                targets = [c["value"] for c in node["cases"]] + (
                    [node["default"]] if node["default"] else []
                )
                if any(not isinstance(target, str) for target in targets):
                    self.issue(
                        self.paths[node["id"]] + "/cases",
                        "invalid_target",
                        "Switch targets must name existing nodes",
                    )
                for target in dict.fromkeys(t for t in targets if isinstance(t, str)):
                    selected = self.nodes.get(target)
                    if (
                        not selected
                        or target not in scope_ids
                        or target == node["id"]
                        or not isinstance(selected.get("depends_on", []), list)
                        or node["id"] not in selected.get("depends_on", [])
                    ):
                        self.issue(
                            self.paths[node["id"]] + "/cases",
                            "invalid_target",
                            "Switch targets must exist and depend on their switch",
                        )
                    else:
                        result["edges"].append(
                            {"source": node["id"], "target": target, "choice": target}
                        )

    def compile(self, source, path, name):
        self.source = self.fields(
            source,
            {
                "agent_name",
                "agent_type",
                "description",
                "nodes",
                "input_schema",
                "output_schema",
                "output_mapping",
                "default_node_timeout",
                "retry_strategy",
                "on_exit",
                "decision",
                "tools",
                "toolsets",
            },
            path,
        )
        if (
            source.get("agent_name", name) != name
            or source.get("agent_type", "workflow") != "workflow"
        ):
            self.issue(
                path, "conflicting_identity", "Workflow identity must match its resource header"
            )
        if "description" in source and (
            not isinstance(source["description"], str) or len(source["description"]) > 2000
        ):
            self.issue(
                path + "/description",
                "invalid_description",
                "Workflow description must be text of at most 2000 characters",
            )
        if "output_mapping" in source and not isinstance(source["output_mapping"], dict):
            self.issue(
                path + "/output_mapping",
                "invalid_mapping",
                "External workflow output mapping must be an object",
            )
        self.decision_config = {}
        if "decision" in self.source:
            block = self.fields(
                self.source["decision"],
                {
                    "model_alias",
                    "timeout_seconds",
                    "max_state_bytes",
                    "max_images",
                    "max_image_bytes",
                },
                path + "/decision",
            )
            alias = block.get("model_alias")
            binding = (
                self.bridge.bindings.get("decisions", {}).get(alias)
                if isinstance(alias, str)
                else None
            )
            if not binding:
                self.issue(
                    path + "/decision/model_alias",
                    "binding_required",
                    "Bind the decision alias explicitly to an existing Nexus decision backend and model",
                )
                binding = {"backend": "opencode", "model": "unbound"}
            self.decision_config = {
                **binding,
                **{
                    k: v
                    for k, v in block.items()
                    if k in {"timeout_seconds", "max_state_bytes", "max_images", "max_image_bytes"}
                },
            }
        from nexus.core.studio_interop_connectors import workflow_tools

        self.tools = workflow_tools(self.bridge, source, path)
        nodes = source.get("nodes", [])
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 40:
            self.issue(path + "/nodes", "invalid_nodes", "Use 1–40 source nodes")
            nodes = []
        for index, node in enumerate(nodes):
            where = path + "/nodes/" + str(index)
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("id"), str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", node["id"])
            ):
                self.issue(where, "invalid_identity", "Node IDs must be lowercase identifiers")
                continue
            identity = node["id"]
            if identity in self.nodes:
                self.issue(where, "duplicate_identity", "Node IDs must be unique")
            self.nodes[identity], self.paths[identity] = node, where
        for identity, node in self.nodes.items():
            if node.get("type") in ("map", "loop") and isinstance(node.get("node"), str):
                target = node["node"]
                if target in self.owners and (
                    node.get("type") != self.nodes[self.owners[target]].get("type")
                ):
                    self.issue(
                        self.paths[identity] + "/node",
                        "ambiguous_body",
                        "Mixed map/loop controllers require distinct body templates",
                    )
                self.owners[target] = identity

        for root, controller in list(self.owners.items()):
            for member in self.members(root):
                if member not in self.owners:
                    self.owners[member] = controller
        exits = self.source.get("on_exit", {})
        if isinstance(exits, str):
            exits = {"always": exits}
        else:
            exits = self.fields(
                exits, {"always", "on_success", "on_failure", "on_cancel"}, path + "/on_exit"
            )
        handlers = {}
        for phase in ("always", "on_success", "on_failure", "on_cancel"):
            if phase not in exits:
                continue
            target = exits[phase]
            if not isinstance(target, str) or target not in self.nodes:
                self.issue(
                    path + "/on_exit/" + phase,
                    "invalid_handler",
                    "Choose existing exit handler nodes",
                )
                continue
            handlers.setdefault(target, []).append(phase)
        self.handlers = handlers
        exit_members = {member for target in handlers for member in self.members(target)}
        result = {
            "version": 2,
            "mapping_language": LANGUAGE,
            "nodes": [
                self.node(identity)
                for identity in self.nodes
                if identity not in self.owners and identity not in exit_members
            ],
            "edges": [],
        }
        if not result["nodes"]:
            self.issue(
                path + "/nodes", "recursive_body", "Workflow must have a non-template entry node"
            )
        # Never silently forget orphan/cyclic templates not reached from a root.
        reachable = set()

        def visit(identity):
            if identity in reachable:
                return
            reachable.add(identity)
            if identity in self.owners:
                reachable.update(self.members(identity))
            target = self.nodes[identity].get("node")
            if isinstance(target, str) and target in self.nodes:
                visit(target)

        for node in result["nodes"]:
            visit(node["id"])
        if handlers:
            result["interop_exit_handlers"] = []
            for target, phases in handlers.items():
                members = self.members(target)
                for member in members:
                    visit(member)
                for phase in phases:
                    # Keep the original single-node projection byte-for-byte compatible.
                    body = (
                        self.body_graph(target, {"exit": True}, ())
                        if len(members) > 1
                        else {
                            "version": 2,
                            "mapping_language": LANGUAGE,
                            "interop_body": True,
                            "nodes": [self.node(target, {"body": True, "exit": True})],
                        }
                    )
                    body["output_mapping"] = {"ref": "nodes." + target}
                    result["interop_exit_handlers"].append({"phase": phase, "body": body})
        for identity in self.nodes.keys() - reachable:
            self.issue(
                self.paths[identity], "unreachable_body", "Body cycle or orphan cannot be compiled"
            )
        self.wire(result, handlers)
        for key in ("input_schema", "output_schema"):
            if key in source:
                result[key] = source[key]
        if "output_mapping" not in source:
            self.issue(
                path + "/output_mapping",
                "required_field",
                "Choose an explicit workflow output mapping",
            )
        result["output_mapping"] = self.value(
            source.get("output_mapping", {}), {}, path + "/output_mapping"
        )
        return result

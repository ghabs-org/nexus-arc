"""Static structured mapping and schema checks; no provider calls or effects."""

import re

REFERENCE = re.compile(r"[A-Za-z_][\w-]*(?:\.[\w-]+)*")
OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"}


def references(value):
    if isinstance(value, dict):
        if set(value) == {"ref"}:
            path = value["ref"]
            if not isinstance(path, str) or len(path) > 256 or not REFERENCE.fullmatch(path):
                raise ValueError("Invalid workflow data reference")
            yield path
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def schema_definition(schema):
    if schema is None:
        return
    if not isinstance(schema, (dict, bool)):
        raise ValueError("Workflow schemas must be JSON Schema objects or booleans")

    def check(part):
        if isinstance(part, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                if key in part and (
                    not isinstance(part[key], str) or not part[key].startswith("#")
                ):
                    raise ValueError("External workflow schema references are disabled")
            for item in part.values():
                check(item)
        elif isinstance(part, list):
            for item in part:
                check(item)

    check(schema)
    try:
        import jsonschema
    except ImportError:
        raise ValueError(
            "Install nexus-arc[studio-connectors] for typed workflow schemas"
        ) from None
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError:
        raise ValueError("Invalid workflow JSON Schema") from None


def deterministic_condition(expression):
    if isinstance(expression, dict) and set(expression) == {"interop_condition"}:
        from nexus.core.studio_interop_conditions import validate_condition

        validate_condition(expression["interop_condition"])
        return
    if not isinstance(expression, dict) or set(expression) != {"left", "op", "right"}:
        raise ValueError("Conditions require left, op and right")
    if not isinstance(expression["op"], str) or expression["op"] not in OPS:
        raise ValueError("Unknown deterministic condition operator")


def validate_mappings(graph, roots):
    aliases = {n["interop_body_id"]: n["id"] for n in graph["nodes"] if "interop_body_id" in n}
    ids = {n["id"] for n in graph["nodes"]}
    ancestors = {i: set() for i in ids}
    for _ in range(len(ids)):
        for edge in graph["edges"]:
            if edge["source"] != "START":
                ancestors[edge["target"]].update({edge["source"], *ancestors[edge["source"]]})

    def check(value, allowed_nodes, allow_all=False):
        if graph.get("mapping_language") == "nexus-template/v1":

            def templates(part):
                if isinstance(part, dict):
                    if set(part) == {"template"}:
                        from nexus.core.studio_interop_mappings import template_text

                        template_text(part["template"], lambda ref: "")
                    for item in part.values():
                        templates(item)
                elif isinstance(part, list):
                    for item in part:
                        templates(item)

            templates(value)
        if graph.get("mapping_language") == "interop-template/v1":
            from nexus.core.studio_interop_values import validate_value

            validate_value(value)
        for path in references(value):
            parts = path.split(".")
            if parts[0] not in roots | {"nodes"}:
                raise ValueError(f"Workflow reference is outside this scope: {path}")
            if parts[0] == "nodes":
                if len(parts) == 1:
                    if not allow_all:
                        raise ValueError("Node inputs must name a specific completed node")
                elif aliases.get(parts[1], parts[1]) not in allowed_nodes:
                    raise ValueError(f"Workflow reference requires an upstream dependency: {path}")

    for key in ("input_schema", "output_schema"):
        schema_definition(graph.get(key))
    handler_ids = {
        handler["body"]["nodes"][0]["id"] for handler in graph.get("interop_exit_handlers", [])
    }
    check(graph.get("output_mapping", {"ref": "nodes"}), ids | handler_ids, True)
    for node in graph["nodes"]:
        for key in ("input_schema", "output_schema"):
            schema_definition(node.get(key))
        fields = [
            "input",
            "args",
            "kwargs",
            "arguments",
            "items",
            "cases",
            "default",
            "when",
            "images",
        ]
        for key in fields:
            if key in node:
                check(node[key], ancestors[node["id"]])
        if node.get("body") and not graph.get("interop_body"):
            for path in references(node["body"]):
                if (
                    path.startswith("interop_nodes.")
                    and aliases.get(path.split(".")[1], path.split(".")[1])
                    not in ancestors[node["id"]]
                ):
                    raise ValueError(
                        f"External body capture requires an upstream dependency: {path}"
                    )
        if node["kind"] == "loop":
            deterministic_condition(node["condition"])
            if graph.get("mapping_language") == "interop-template/v1":
                from nexus.core.studio_interop_values import validate_value

                validate_value(node["condition"])
            for path in references(node["condition"]):
                if path.split(".")[0] not in roots | {"nodes", "iteration", "result"}:
                    raise ValueError(f"Workflow reference is outside this loop condition: {path}")
                if (
                    path.startswith("nodes.")
                    and aliases.get(path.split(".")[1], path.split(".")[1])
                    not in ancestors[node["id"]]
                ):
                    raise ValueError(f"Workflow reference requires an upstream dependency: {path}")
        if node["kind"] == "switch":
            for case in node["cases"]:
                deterministic_condition(case["condition"])

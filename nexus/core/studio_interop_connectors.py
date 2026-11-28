"""Explicit OpenAPI node bindings and local toolset membership for conversion."""

from nexus.core.studio_interop_agent_settings import resource_binding
from nexus.core.studio_openapi import operations


def workflow_tools(bridge, source, path):
    tools = bridge.tools(source.get("tools", []), path + "/tools")
    aliases = source.get("toolsets", [])
    if not isinstance(aliases, list) or len(aliases) > 30:
        bridge.issue(path + "/toolsets", "invalid_toolsets", "Use up to thirty bound toolsets")
        return tools
    for index, alias in enumerate(aliases):
        target = resource_binding(
            bridge, "toolsets", alias, "toolsets", path + "/toolsets/" + str(index)
        )
        if target:
            tools.extend(bridge.studio.catalog.get("toolsets", target)["data"]["tools"])
    return list(dict.fromkeys(tools))


def connector_node(bridge, source, path):
    alias, tool = source.get("connector"), source.get("tool")
    if (
        not isinstance(alias, str)
        or not alias
        or "/" in alias
        or not isinstance(tool, str)
        or not tool
        or "/" in tool
    ):
        raise ValueError("Connector nodes need nonempty connector/tool aliases without slashes")
    target = resource_binding(bridge, "connectors", alias, "connectors", path + "/connector")
    if not target:
        return {}
    binding = bridge.bindings.get("operations", {}).get(alias + "/" + tool)
    if not isinstance(binding, dict) or set(binding) != {"operation_id", "input"}:
        raise ValueError(
            "Bind connector/tool explicitly to operation_id and input: body or parameters"
        )
    if (
        not isinstance(binding["input"], str)
        or binding["input"] not in {"body", "parameters"}
        or not isinstance(binding["operation_id"], str)
    ):
        raise ValueError(
            "Operation binding requires an operation ID and body or parameters input mode"
        )
    data = bridge.studio.catalog.get("connectors", target)["data"]
    if data.get("type") != "openapi":
        raise ValueError("Connector-node imports currently require an existing OpenAPI connector")
    available = operations(data["spec"])
    selected = binding["operation_id"]
    if selected not in data["operations"] or selected not in available:
        raise ValueError("Choose an operation already selected in the bound connector")
    operation = available[selected]
    action = "read" if operation["method"] in {"GET", "HEAD", "OPTIONS"} else "invoke"
    if action not in data["actions"]:
        raise ValueError("Required read/invoke action is not selected in the connector")
    if binding["input"] == "body":
        if "application/json" not in operation["requestBody"].get("content", {}) or any(
            p.get("required") or p["in"] == "path" for p in operation["parameters"]
        ):
            raise ValueError(
                "Body mappings require a JSON body and no required operation parameters"
            )
    elif operation["requestBody"].get("required"):
        raise ValueError("Parameter mappings cannot omit a required operation body")
    return {
        "kind": "integration",
        "connector_id": target,
        "action": action,
        "arguments": {"operation_id": selected},
        "_input_mode": binding["input"],
    }

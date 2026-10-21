"""Lossless bounded external agent settings mapped to existing Nexus resources."""

import re
from urllib.parse import urlparse

from nexus.core.studio_configuration import digest

RUNTIME = {
    "request_timeout_seconds",
    "max_llm_calls_per_task",
    "max_tool_calls_per_task",
    "output_schema",
    "skills",
}


def declarative_card(bridge, source, path):
    """Map routing descriptions, keeping knowledge bundles in their separate field."""
    from nexus.core.studio_agent_settings import validate_settings

    card = {}
    if "skills" in source:
        skills = source["skills"]
        if not isinstance(skills, list) or len(skills) > 100:
            bridge.issue(
                path + "/skills", "invalid_capabilities", "Use up to 100 routing capabilities"
            )
        else:
            card["skills"] = []
            for index, skill in enumerate(skills):
                entry = bridge.fields(
                    skill,
                    {"id", "name", "description", "tags", "examples"},
                    path + "/skills/" + str(index),
                )
                entry = dict(entry)
                if "id" not in entry:
                    entry["id"] = "cap-" + digest(entry)[:16]
                card["skills"].append(entry)
    for field, target in (
        ("inputModes", "defaultInputModes"),
        ("outputModes", "defaultOutputModes"),
    ):
        if field in source:
            card[target] = source[field]
    return (
        bridge.attempt(
            path + "/skills",
            lambda: validate_settings(bridge.studio, {"agent_card": card}).get("agent_card", {}),
            {},
        )
        if card
        else {}
    )


def resource_binding(bridge, group, alias, kind, path):
    target = bridge.bindings.get(group, {}).get(alias) if isinstance(alias, str) else None
    if not isinstance(target, str):
        bridge.issue(
            path,
            "binding_required",
            "Bind this external resource alias explicitly to an existing Nexus resource",
        )
        return None
    return bridge.attempt(path, lambda: bridge.studio.catalog.get(kind, target) and target)


def tool_references(bridge, entry, path, refs):
    kind = entry.get("tool_type")
    if kind == "builtin-group":
        entry = bridge.fields(entry, {"tool_type", "group_name"}, path)
        target = resource_binding(bridge, "toolsets", entry.get("group_name"), "toolsets", path)
        if target:
            refs.setdefault("toolset_ids", []).append(target)
        return True
    if kind != "mcp":
        return False
    entry = bridge.fields(entry, {"tool_type", "connection_params", "allow_list"}, path)
    params = bridge.fields(
        entry.get("connection_params"),
        {"type", "command", "args", "env", "url"},
        path + "/connection_params",
    )
    if (
        params.get("type") not in {"stdio", "sse", "streamable-http"}
        or (
            params.get("type") == "stdio"
            and (
                not isinstance(params.get("command"), str)
                or not params["command"]
                or len(params["command"]) > 1000
                or any(c in params["command"] for c in "\r\n\x00")
            )
        )
        or (params.get("type") != "stdio" and not isinstance(params.get("url"), str))
    ):
        bridge.issue(
            path + "/connection_params",
            "invalid_transport",
            "Use a bounded MCP transport descriptor and explicit existing connector binding",
        )
    if not isinstance(params.get("env", {}), dict):
        bridge.issue(
            path + "/connection_params/env",
            "invalid_environment",
            "MCP environment references must be an object",
        )
    for name, value in (
        params.get("env", {}).items() if isinstance(params.get("env", {}), dict) else []
    ):
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or not isinstance(value, str)
            or not re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", value)
        ):
            bridge.issue(
                path + "/connection_params/env",
                "secret_reference_required",
                "MCP environment values require references; no values are expanded or exported as credentials",
            )
    if params.get("url"):
        url = urlparse(params["url"] if isinstance(params["url"], str) else "")
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            bridge.issue(
                path + "/connection_params/url",
                "invalid_endpoint",
                "Use a credential-free MCP endpoint and explicit Nexus connector binding",
            )
    args = params.get("args", [])
    if (
        not isinstance(args, list)
        or len(args) > 50
        or any(not isinstance(v, str) or len(v) > 2000 for v in args)
        or any(
            re.search(r"(?i)(password|api[_-]?key|token|secret)", v)
            for v in args
            if isinstance(v, str)
        )
    ):
        bridge.issue(
            path + "/connection_params/args",
            "secret_reference_required",
            "Credential-bearing command arguments require manual adaptation to server-managed references",
        )
    alias = "mcp-" + digest(params)[:12]
    if alias not in bridge.bindings.get("connectors", {}):
        bridge.issue(
            path + "/connection_params",
            "binding_required",
            "Bind connector alias "
            + alias
            + " to an existing Nexus MCP connection; conversion never launches or installs a server",
        )
        return True
    target = resource_binding(bridge, "connectors", alias, "connectors", path)
    if target:
        connector = bridge.studio.catalog.get("connectors", target)["data"]
        selected = entry.get("allow_list", connector.get("read_only_tools", []))
        if (
            connector.get("type") != "mcp"
            or not isinstance(selected, list)
            or not selected
            or any(not isinstance(t, str) for t in selected)
            or not set(selected) <= set(connector.get("read_only_tools", []))
        ):
            bridge.issue(
                path + "/allow_list",
                "unsupported_mutating_tool",
                "Bind selected read-only MCP tools; mutations require approved integration workflow nodes",
            )
        else:
            refs.setdefault("connector_ids", []).append(target)
            refs.setdefault("connector_tools", {})[target] = selected
    return True


def agent_settings(bridge, source, path):
    result = {}
    if "request_timeout_seconds" in source:
        value = source["request_timeout_seconds"]
        if type(value) is not int or not 1 <= value <= 3600:
            bridge.issue(
                path + "/request_timeout_seconds",
                "invalid_timeout",
                "Use a timeout between one and 3600 seconds",
            )
        else:
            result["timeout_seconds"] = value
    if "max_llm_calls_per_task" in source:
        value = source["max_llm_calls_per_task"]
        if type(value) is not int or not 1 <= value <= 100:
            bridge.issue(
                path + "/max_llm_calls_per_task",
                "invalid_limit",
                "Use one to 100 total model calls",
            )
        else:
            result["max_model_calls"] = value
    if "max_tool_calls_per_task" in source:
        value = source["max_tool_calls_per_task"]
        if type(value) is not int or not 0 <= value <= 100:
            bridge.issue(
                path + "/max_tool_calls_per_task",
                "invalid_limit",
                "Use zero to 100 total tool calls",
            )
        else:
            result["max_tool_calls"] = value
    if "output_schema" in source:
        from nexus.core.studio_workflow_validation import schema_definition

        bridge.attempt(path + "/output_schema", lambda: schema_definition(source["output_schema"]))
        result["output_schema"] = source["output_schema"]
    skills = source.get("skills", [])
    if not isinstance(skills, list) or len(skills) > 20:
        bridge.issue(
            path + "/skills", "invalid_skills", "Use up to twenty explicitly bound skill resources"
        )
    else:
        for index, skill in enumerate(skills):
            location = path + "/skills/" + str(index)
            skill = bridge.fields(skill, {"name", "path"}, location)
            if set(skill) not in ({"name"}, {"path"}):
                bridge.issue(
                    location,
                    "invalid_skill",
                    "Select one skill name or path alias; files are never read during conversion",
                )
                continue
            target = resource_binding(
                bridge, "skills", next(iter(skill.values())), "skills", location
            )
            if target:
                result.setdefault("skill_ids", []).append(target)
    return result

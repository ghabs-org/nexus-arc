"""Reusable agent resource references and bounded, provider-independent settings."""

import copy
import hashlib
import json

FIELDS = {
    "model_id",
    "toolset_ids",
    "connector_ids",
    "connector_tools",
    "max_tool_calls",
    "max_model_calls",
    "output_schema",
    "agent_card",
}


def routing_description(agent):
    """Bounded coordinator hints from the same snapshot used for execution."""
    skills = agent.get("agent_card", {}).get("skills", [])
    parts = ["Routing capabilities: " + json.dumps(skills, ensure_ascii=False)] if skills else []
    parts.append(agent.get("purpose") or agent.get("instructions") or agent.get("agent_type", ""))
    return "\n".join(parts)[:2000]


def validate_settings(studio, payload):
    from nexus.core.studio import _identity
    from nexus.core.studio_workflow_validation import schema_definition

    result = {}
    for field, kind in (("toolset_ids", "toolsets"), ("connector_ids", "connectors")):
        values = payload.get(field, [])
        if (
            not isinstance(values, list)
            or len(values) > 30
            or any(not isinstance(v, str) for v in values)
        ):
            raise ValueError(field + " must contain up to thirty resource IDs")
        for value in values:
            studio.catalog.get(kind, _identity(value))
        if values:
            result[field] = list(dict.fromkeys(values))
    if payload.get("model_id"):
        studio.catalog.get("models", _identity(payload["model_id"]))
        result["model_id"] = payload["model_id"]
    if "max_tool_calls" in payload:
        limit = payload["max_tool_calls"]
        if limit is not None:
            if type(limit) is not int or not 0 <= limit <= 100:
                raise ValueError("Agent tool call limit must be between zero and 100")
            result["max_tool_calls"] = limit
    if "max_model_calls" in payload:
        limit = payload["max_model_calls"]
        if limit is not None:
            if type(limit) is not int or not 1 <= limit <= 101:
                raise ValueError("Agent model call limit must be between one and 101")
            result["max_model_calls"] = limit
    if "connector_tools" in payload:
        permissions = payload["connector_tools"]
        if not isinstance(permissions, dict) or set(permissions) - set(
            result.get("connector_ids", [])
        ):
            raise ValueError(
                "Connector tool permissions must name this agent's selected connections"
            )
        for identity, selected in permissions.items():
            data = studio.catalog.get("connectors", identity)["data"]
            if (
                data.get("type") != "mcp"
                or not isinstance(selected, list)
                or not selected
                or len(selected) > 100
                or any(not isinstance(v, str) for v in selected)
                or not set(selected) <= set(data.get("read_only_tools", []))
            ):
                raise ValueError("Agent MCP tools must be explicitly selected read-only operations")
        result["connector_tools"] = copy.deepcopy(permissions)
    if "agent_card" in payload:
        card = payload["agent_card"]
        if len(json.dumps(card).encode()) > 65536:
            raise ValueError("Agent card metadata exceeds 64 KiB")
        if not isinstance(card, dict) or set(card) - {
            "description",
            "skills",
            "defaultInputModes",
            "defaultOutputModes",
            "version",
            "documentationUrl",
            "provider",
        }:
            raise ValueError("Unsupported agent card metadata")
        for field in ("description", "version"):
            if field in card and (
                not isinstance(card[field], str) or not 1 <= len(card[field]) <= 2000
            ):
                raise ValueError("Agent card description/version must be bounded nonempty text")
        from urllib.parse import urlparse

        def public_url(value):
            if not isinstance(value, str) or len(value) > 2000:
                return False
            try:
                parsed = urlparse(value)
                return bool(
                    parsed.scheme == "https"
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and not any(ord(c) < 33 for c in value)
                )
            except ValueError:
                return False

        if "documentationUrl" in card and not public_url(card["documentationUrl"]):
            raise ValueError("Agent documentation URL must use HTTPS without credentials")
        if "provider" in card:
            provider = card["provider"]
            if (
                not isinstance(provider, dict)
                or set(provider) != {"organization", "url"}
                or not isinstance(provider["organization"], str)
                or not 1 <= len(provider["organization"]) <= 2000
                or not public_url(provider["url"])
            ):
                raise ValueError("Agent card provider requires organization and HTTPS URL")
        skills = card.get("skills", [])
        if not isinstance(skills, list) or len(skills) > 100:
            raise ValueError("Agent card accepts up to 100 routing capabilities")
        for skill in skills:
            if (
                not isinstance(skill, dict)
                or set(skill) - {"id", "name", "description", "tags", "examples"}
                or any(
                    not isinstance(skill.get(k), str) or not 1 <= len(skill[k]) <= 2000
                    for k in ("id", "name", "description")
                )
            ):
                raise ValueError("Agent capabilities require bounded IDs, names and descriptions")
            for field in ("tags", "examples"):
                if field in skill and (
                    not isinstance(skill[field], list)
                    or len(skill[field]) > 30
                    or any(
                        not isinstance(value, str) or len(value) > 2000 for value in skill[field]
                    )
                ):
                    raise ValueError("Capability tags and examples must be bounded text lists")
        if len({skill["id"] for skill in skills}) != len(skills):
            raise ValueError("Agent routing capability IDs must be unique")
        for field in ("defaultInputModes", "defaultOutputModes"):
            if field in card and card[field] != ["text"]:
                raise ValueError(
                    "Only implemented text transport modes can be advertised by this profile"
                )
        result["agent_card"] = copy.deepcopy(card)
    if "output_schema" in payload:
        schema_definition(payload["output_schema"])
        result["output_schema"] = copy.deepcopy(payload["output_schema"])
    return result


def resolve_agent(studio, agent):
    """Resolve configuration once for queuing; credentials remain execution-time only."""
    resolved = copy.deepcopy(agent)
    tools = list(agent.get("tool_ids", []))
    revisions = []
    for identity in agent.get("toolset_ids", []):
        item = studio.catalog.get("toolsets", identity)
        tools.extend(item["data"]["tools"])
        revisions.append({"kind": "toolsets", "id": identity, "revision": item["revision"]})
    prefix = "connector:" + hashlib.sha256(str(studio.root).encode()).hexdigest()[:8] + ":"
    for identity in agent.get("connector_ids", []):
        item = studio.catalog.get("connectors", identity)
        if not item["data"]["enabled"]:
            raise ValueError("An agent connection is disabled; configure and explicitly enable it")
        tools.append(prefix + identity)
        revisions.append({"kind": "connectors", "id": identity, "revision": item["revision"]})
    if agent.get("model_id"):
        item = studio.catalog.get("models", agent["model_id"])
        resolved.update(provider=item["data"]["provider"], model=item["data"]["model"])
        if item["data"].get("effort") and not resolved.get("effort"):
            resolved["effort"] = item["data"]["effort"]
        revisions.append({"kind": "models", "id": item["id"], "revision": item["revision"]})
    resolved["tool_ids"] = list(dict.fromkeys(tools))
    resolved["skill_context"] = studio.catalog.context(
        {"skill_ids": agent.get("skill_ids", [])}, require_active=True
    )
    resolved["resource_revisions"] = revisions
    return resolved


def pin_agents(studio, payload):
    selected = {payload["agent_id"]} if payload.get("agent_id") else set()
    if payload.get("kind") == "delegation":
        selected.update(payload.get("agent_ids", []))
    if payload.get("kind") == "workflow" or (
        payload.get("kind") == "evaluation" and payload.get("workflow_snapshot")
    ):
        # Pin named children too; cycles/actual reachability are checked by graph preflight.
        def walk(value):
            referenced = set()
            if isinstance(value, dict):
                if value.get("kind") == "agent" and value.get("agent_id"):
                    selected.add(value["agent_id"])
                if value.get("kind") == "workflow" and value.get("workflow_id"):
                    referenced.add(value["workflow_id"])
                for child in value.values():
                    referenced.update(walk(child))
            elif isinstance(value, list):
                for child in value:
                    referenced.update(walk(child))
            return referenced

        # Only traverse named definitions reachable from this workflow, never unrelated agents.
        queue, seen = [payload["workflow_snapshot"]], set()
        snapshots = payload.get("workflow_snapshots", {})
        while queue:
            doc = queue.pop()
            key = id(doc)
            if key in seen:
                continue
            seen.add(key)
            selected.update(step["agent_type"] for step in doc.get("steps", []))
            for identity in walk(doc):
                if identity not in snapshots:
                    raise ValueError("Workflow references an unavailable named workflow")
                queue.append(snapshots[identity])
    for identity in selected:
        agent = payload["agent_snapshots"].get(identity)
        if agent is None:
            raise ValueError("Workflow references an unavailable agent")
        payload["agent_snapshots"][identity] = resolve_agent(studio, agent)


def validate_output(agent, output):
    if "output_schema" not in agent:
        return
    from nexus.core.studio_workflow_v2 import validate_schema

    try:
        value = json.loads(output)
    except (TypeError, ValueError):
        raise ValueError("Agent output is not valid JSON for its configured schema") from None
    validate_schema(agent["output_schema"], value)


def call_limit(agent):
    count = agent.get("max_tool_calls", agent.get("max_model_calls", 9) - 1)
    return min(count, agent.get("max_model_calls", count + 1) - 1)

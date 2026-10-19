"""Quick Build workflow authoring; semantics use the shared configuration overlay."""

import copy
import re
from string import Formatter

from nexus.core.studio_catalog import _text


def workflow_shape(value):
    if not isinstance(value, dict) or set(value) - {"id", "name", "description", "steps", "graph"}:
        raise ValueError("Unsupported workflow proposal fields")
    if ("graph" in value) == ("steps" in value):
        raise ValueError("Choose exactly one workflow graph or sequential steps")
    return value


def normalize_workflow(studio, value, resource_id, reference, agent_ids, workflow_ids):
    value = workflow_shape(value)
    result = {
        "id": resource_id(value.get("id")),
        "name": _text(value.get("name"), "Workflow name", 160),
        "description": value.get("description", ""),
    }
    if not isinstance(result["description"], str) or len(result["description"]) > 2000:
        raise ValueError("Workflow description must be text up to 2000 characters")
    if "graph" in value:
        graph = copy.deepcopy(value["graph"])

        def rewrite(part):
            if isinstance(part, dict):
                for node in part.get("nodes", []):
                    rewrite_node(node)
                for field in ("on_success", "on_error"):
                    rewrite(part.get(field))
                for handler in part.get("interop_exit_handlers", []):
                    if isinstance(handler, dict):
                        rewrite(handler.get("body"))

        def rewrite_node(part):
            if isinstance(part, dict):
                for field, ids in (("agent_id", agent_ids), ("workflow_id", workflow_ids)):
                    if part.get(field) and resource_id(part[field]) in ids:
                        part[field] = resource_id(part[field])
                if part.get("connector_id"):
                    part["connector_id"] = reference("connectors", part["connector_id"])
                rewrite(part.get("body"))

        rewrite(graph)
        result["graph"] = graph
        return result
    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
        raise ValueError("A workflow needs between 1 and 12 sequential steps")
    result["steps"], seen = [], set()
    for step in steps:
        if not isinstance(step, dict) or set(step) - {"id", "name", "agent_type", "prompt"}:
            raise ValueError("Unsupported workflow step fields")
        identity = _text(step.get("id"), "Step ID", 64)
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", identity) or identity in seen:
            raise ValueError("Step IDs must be unique lowercase identifiers")
        seen.add(identity)
        agent = resource_id(step.get("agent_type"))
        if agent not in agent_ids:
            agent = step.get("agent_type")
            try:
                studio.definition("agents", agent)
            except FileNotFoundError:
                raise ValueError(
                    "Step references an unavailable agent outside this build"
                ) from None
        prompt = _text(step.get("prompt"), "Step prompt")
        for _, field, specification, conversion in Formatter().parse(prompt):
            if field is not None and (field != "task" or specification or conversion):
                raise ValueError("Step prompts support only the {task} placeholder")
        result["steps"].append(
            {
                "id": identity,
                "name": _text(step.get("name"), "Step name", 160),
                "agent_type": agent,
                "prompt": prompt,
            }
        )
    return result


def workflow_document(workflow):
    document = {
        "apiVersion": "nexus-arc/v1",
        "kind": "Workflow",
        "metadata": {"name": workflow["name"], "description": workflow["description"]},
    }
    if "graph" in workflow:
        document["graph"] = workflow["graph"]
    else:
        document["steps"] = [
            {
                "id": step["id"],
                "name": step["name"],
                "agent_type": step["agent_type"],
                "prompt_template": step["prompt"],
            }
            for step in workflow["steps"]
        ]
    return document

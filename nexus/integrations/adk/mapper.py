"""Mapping helpers from Nexus ARC agents to ADK-style neutral specs."""

from __future__ import annotations

import re
from collections.abc import Iterable

from nexus.agents.base import BaseAgent
from nexus.agents.coordinator import Coordinator
from nexus.agents.loop import LoopAgent
from nexus.agents.parallel import ParallelAgent
from nexus.agents.sequential import SequentialAgent
from nexus.integrations.adk.types import AdkEdgeSpec, AdkNodeSpec, AdkWorkflowSpec


def export_agent_to_adk_spec(agent: BaseAgent) -> AdkWorkflowSpec:
    """Export a supported ARC agent/composition to an ADK-style workflow spec.

    v0 supports SequentialAgent, ParallelAgent, LoopAgent, Coordinator, and
    leaf sub-agents. The returned spec is ARC-owned and does not require Google ADK.
    """
    nodes: list[AdkNodeSpec] = []
    edges: list[AdkEdgeSpec] = []
    used_ids: set[str] = set()
    entrypoint = _walk_agent(agent, nodes=nodes, edges=edges, used_ids=used_ids)
    return AdkWorkflowSpec(
        name=agent.name,
        entrypoint=entrypoint,
        nodes=tuple(nodes),
        edges=tuple(edges),
        metadata={"origin": "arc", "format": "adk-style-neutral", "version": 0},
    )


def _walk_agent(
    agent: BaseAgent,
    *,
    nodes: list[AdkNodeSpec],
    edges: list[AdkEdgeSpec],
    used_ids: set[str],
) -> str:
    node_id = _unique_id(agent.name, used_ids)

    if isinstance(agent, SequentialAgent):
        nodes.append(_node_for_agent(agent, node_id, "sequential"))
        child_ids = [
            _walk_agent(child, nodes=nodes, edges=edges, used_ids=used_ids)
            for child in agent.sub_agents
        ]
        for child_id in child_ids:
            edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="next"))
        for source, target in _pairwise(child_ids):
            edges.append(
                AdkEdgeSpec(source=source, target=target, kind="next", metadata={"sequence": True})
            )
        return node_id

    if isinstance(agent, ParallelAgent):
        nodes.append(
            _node_for_agent(
                agent,
                node_id,
                "parallel",
                metadata={"merge_strategy": agent.merge_strategy, "separator": agent.separator},
            )
        )
        child_ids = [
            _walk_agent(child, nodes=nodes, edges=edges, used_ids=used_ids)
            for child in agent.sub_agents
        ]
        for child_id in child_ids:
            edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="branch"))
        return node_id

    if isinstance(agent, LoopAgent):
        nodes.append(
            _node_for_agent(
                agent,
                node_id,
                "loop",
                metadata={
                    "max_iterations": agent.max_iterations,
                    "stop_condition_exported": False,
                    "unsupported": ["python_stop_condition"],
                },
            )
        )
        child_id = _walk_agent(agent.sub_agent, nodes=nodes, edges=edges, used_ids=used_ids)
        edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="loop"))
        return node_id

    if isinstance(agent, Coordinator):
        nodes.append(
            _node_for_agent(
                agent,
                node_id,
                "coordinator",
                metadata={"router_url": agent.router_url, "workspace_path": agent.workspace_path},
            )
        )
        child_ids = [
            _walk_agent(child, nodes=nodes, edges=edges, used_ids=used_ids)
            for child in agent.sub_agents
        ]
        for child_id in child_ids:
            edges.append(AdkEdgeSpec(source=node_id, target=child_id, kind="delegates"))
        return node_id

    nodes.append(_node_for_agent(agent, node_id, "agent"))
    return node_id


def _node_for_agent(
    agent: BaseAgent, node_id: str, kind: str, metadata: dict | None = None
) -> AdkNodeSpec:
    return AdkNodeSpec(
        id=node_id,
        kind=kind,  # type: ignore[arg-type]
        name=agent.name,
        description=agent.description,
        metadata={"arc_class": type(agent).__name__, **(metadata or {})},
    )


def _unique_id(name: str, used_ids: set[str]) -> str:
    base = _slug(name) or "agent"
    candidate = base
    index = 2
    while candidate in used_ids:
        candidate = f"{base}-{index}"
        index += 1
    used_ids.add(candidate)
    return candidate


def _slug(value: str) -> str:
    return re.sub(r"(^-|-$)", "", re.sub(r"[^a-z0-9]+", "-", value.lower()))


def _pairwise(values: Iterable[str]) -> Iterable[tuple[str, str]]:
    items = list(values)
    return zip(items, items[1:], strict=False)


def import_adk_spec(
    spec: AdkWorkflowSpec,
    leaf_factory,
    *,
    ai_provider=None,
    stop_condition=None,
) -> BaseAgent:
    """Rebuild an ARC agent composition from an ADK-style workflow spec.

    Args:
        spec: Workflow spec (e.g. from :func:`export_agent_to_adk_spec`).
        leaf_factory: ``leaf_factory(node: AdkNodeSpec) -> BaseAgent`` for
            ``kind="agent"`` leaves.
        ai_provider: Provider for imported :class:`Coordinator` nodes.
        stop_condition: Loop stop predicate; defaults to single-pass
            (Python lambdas do not survive spec export).

    Raises:
        ValueError: On unknown node kinds, missing entrypoint, or loops
            without exactly one child.
    """
    entry = spec.node_by_id(spec.entrypoint)
    if entry is None:
        raise ValueError(f"spec entrypoint '{spec.entrypoint}' not found")
    return _build_node(spec, entry, leaf_factory, ai_provider=ai_provider,
                       stop_condition=stop_condition)


def _child_ids(spec: AdkWorkflowSpec, node_id: str, kinds: set[str]) -> list[str]:
    direct = [
        edge for edge in spec.edges
        if edge.source == node_id and edge.kind in kinds
    ]
    sequenced = [edge.target for edge in direct if edge.metadata.get("sequence") is True]
    sequenced += [edge.target for edge in direct if edge.target not in sequenced]
    return sequenced


def _build_node(spec, node, leaf_factory, *, ai_provider, stop_condition):
    from nexus.agents.parallel import ParallelAgent as _Parallel
    from nexus.agents.sequential import SequentialAgent as _Sequential

    if node.kind == "agent":
        return leaf_factory(node)
    if node.kind == "sequential":
        children = [_build_node(spec, _require(spec, cid), leaf_factory,
                                ai_provider=ai_provider, stop_condition=stop_condition)
                    for cid in _child_ids(spec, node.id, {"next"})]
        if not children:
            raise ValueError(f"sequential node '{node.id}' has no children")
        return _Sequential(name=node.name, sub_agents=children, description=node.description)
    if node.kind == "parallel":
        children = [_build_node(spec, _require(spec, cid), leaf_factory,
                                ai_provider=ai_provider, stop_condition=stop_condition)
                    for cid in _child_ids(spec, node.id, {"branch", "next"})]
        if not children:
            raise ValueError(f"parallel node '{node.id}' has no children")
        metadata = dict(node.metadata or {})
        return _Parallel(
            name=node.name, sub_agents=children, description=node.description,
            merge_strategy=str(metadata.get("merge_strategy") or "concat"),
            separator=str(metadata.get("separator", "\n")),
        )
    if node.kind == "loop":
        children = _child_ids(spec, node.id, {"loop", "next"})
        if len(children) != 1:
            raise ValueError(f"loop node '{node.id}' needs exactly one child")
        metadata = dict(node.metadata or {})
        return LoopAgent(
            name=node.name,
            sub_agent=_build_node(spec, _require(spec, children[0]), leaf_factory,
                                  ai_provider=ai_provider, stop_condition=stop_condition),
            stop_condition=stop_condition or (lambda _output: True),
            max_iterations=int(metadata.get("max_iterations") or 5),
            description=node.description,
        )
    if node.kind == "coordinator":
        children = [_build_node(spec, _require(spec, cid), leaf_factory,
                                ai_provider=ai_provider, stop_condition=stop_condition)
                    for cid in _child_ids(spec, node.id, {"delegates", "next"})]
        if not children:
            raise ValueError(f"coordinator node '{node.id}' has no children")
        metadata = dict(node.metadata or {})
        return Coordinator(
            name=node.name, sub_agents=children, ai_provider=ai_provider,
            router_url=str(metadata.get("router_url") or "http://127.0.0.1:7771"),
            workspace_path=str(metadata.get("workspace_path") or "/tmp"),
            description=node.description,
        )
    raise ValueError(f"unknown node kind '{node.kind}' for node '{node.id}'")


def _require(spec: AdkWorkflowSpec, node_id: str):
    node = spec.node_by_id(node_id)
    if node is None:
        raise ValueError(f"edge target '{node_id}' not found")
    return node

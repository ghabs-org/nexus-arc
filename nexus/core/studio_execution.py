"""Content-free execution events and read-only chronological workflow replay.

Old traces remain readable. Their missing timestamps are reported rather than
invented; replaying this view never resumes an executor or invokes a tool.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import UTC, datetime

_TEXT_FIELDS = {
    "status",
    "previous_status",
    "agent_id",
    "agent",
    "provider",
    "model",
    "artifact_id",
    "node_id",
    "node_name",
    "tool",
    "message",
    "parent_id",
    "source_id",
    "phase",
    "node_status",
}
_SECRET = re.compile(
    r"(?i)(bearer\s+)[^\s,;]+|((?:api[_-]?key|token|password|secret|authorization)\s*[=:]\s*)[^\s,;&]+"
)
_URL_AUTH = re.compile(r"(https?://)[^\s/@]+:[^\s/@]+@", re.I)


def _label(value, limit=240):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))[:limit]
    text = _URL_AUTH.sub(r"\1[redacted]@", text)
    return _SECRET.sub(lambda m: (m.group(1) or m.group(2) or "") + "[redacted]", text)


def _fields(values):
    result = {
        key: _label(value)
        for key, value in values.items()
        if key in _TEXT_FIELDS and value is not None
    }
    duration = values.get("duration_seconds")
    if type(duration) in {int, float} and math.isfinite(duration) and duration >= 0:
        result["duration_seconds"] = round(duration, 3)
    revision = values.get("artifact_revision")
    if type(revision) is int and revision > 0:
        result["artifact_revision"] = revision
    return result


def _timestamp(value):
    try:
        if type(value) in {float, int} and math.isfinite(value):
            return datetime.fromtimestamp(value, UTC).isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat() if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError, OSError):
        return None


class StudioExecution:
    def __init__(self, studio):
        self.studio = studio

    def record(self, run_id, kind, **fields):
        """Append a bounded metadata event under the existing state lock.

        Sequence gaps after a failed write are harmless; sequence numbers are
        never reused. No request-controlled owner or secret-bearing payload is
        accepted. The run's existing owner is authoritative.
        """
        studio = self.studio
        with studio.lock:
            run = studio.run(run_id)
            path = studio._path("execution-sequences", run_id)
            previous = studio._read(path).get("sequence", 0) if path.exists() else 0
            sequence = previous + 1
            studio._write(path, {"id": run_id, "sequence": sequence})
            record = {
                **_fields(fields),
                "id": f"{run_id}-{sequence:012d}",
                "run_id": run_id,
                "owner_id": run.get("requester_nexus_id") or run.get("owner_id"),
                "kind": _label(kind, 40),
                "sequence": sequence,
                "created_at": datetime.now(UTC).isoformat(),
                "version": 1,
            }
            studio._write(studio._path("execution-events", record["id"]), record)
            return record

    def snapshot(self, run_id, actor, *, limit=500, before=None):
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Execution page size must be between one and 1000")
        studio = self.studio
        with studio.lock:
            run = studio.collaboration.owned(studio.run(run_id), actor)
            saved = {}
            graph_format = None
            for collection in ("graph-runs", "yaml-runs"):
                path = studio._path(collection, run_id)
                if path.exists():
                    saved, graph_format = studio._read(path), collection
                    break
            expected_owner = run.get("requester_nexus_id") or run.get("owner_id") or "desktop"

            def rows(collection):
                # Match both run and owner. A foreign row linked to this run
                # must not become visible even to the run owner's account.
                return [
                    row
                    for row in studio.records(collection, limit=None)
                    if row.get("run_id") == run_id
                    and (row.get("owner_id") or "desktop") == expected_owner
                    and (not studio.team or studio.team.visible(row, actor))
                ]

            canonical = rows("execution-events")
            traces = rows("node-traces")
            invocations = rows("invocation-traces")
            tools = rows("tool-traces")
            status_events = rows("events")
            graph, states = self._graph(saved, run, traces)
            events = []
            seen = set()

            def add(identity, kind, values, at=None, source="recorded", sequence=None):
                key = f"{kind}:{identity}"
                if key in seen:
                    return
                seen.add(key)
                events.append(
                    {
                        **_fields(values),
                        "id": _label(identity, 160),
                        "kind": kind,
                        "at": _timestamp(at),
                        "time_source": source,
                        "recorded_sequence": sequence,
                    }
                )

            for event in canonical:
                if event.get("kind") in {"approval", "input"} and event.get("status") == "resolved":
                    # Clearing a gate alone is not proof of approval. Confirm
                    # completion against its committed checkpoint; rejection
                    # clears pending gates too, but never completes the node.
                    node_status = states.get(event.get("node_id"))
                    if node_status == "completed":
                        event = {**event, "node_status": "completed"}
                    elif run.get("status") == "cancelled":
                        event = {**event, "node_status": "cancelled"}
                add(
                    event["id"],
                    event.get("kind", "event"),
                    event,
                    event.get("created_at"),
                    sequence=event.get("sequence"),
                )

            def covered(kind, row, key=None):
                expected_status = row.get("status")
                if kind == "tool" and expected_status is None:
                    expected_status = "completed" if row.get("ok") else "failed"
                return any(
                    event.get("kind") == kind
                    and event.get("status") == expected_status
                    and (
                        event.get("source_id") == row.get("id")
                        or (key and row.get(key) and event.get(key) == row.get(key))
                    )
                    for event in canonical
                )

            for row in traces:
                kind = "node"
                if covered(kind, row, "node_id"):
                    continue
                node_id = row.get("node_id", "")
                started = saved.get("started", {}).get(node_id)
                at = row.get("created_at")
                add(
                    row["id"],
                    kind,
                    {**row, "phase": row.get("kind", "node")},
                    at or started,
                    "recorded" if at else "checkpoint" if started else "unavailable",
                )
            for collection, kind in ((invocations, "invocation"), (tools, "tool")):
                for row in collection:
                    if covered(kind, row, "tool" if kind == "tool" else None):
                        continue
                    add(
                        row["id"],
                        kind,
                        {
                            **row,
                            "source_id": row.get("source_id") or row["id"],
                            "status": row.get("status")
                            or ("completed" if row.get("ok") else "failed"),
                        },
                        row.get("created_at"),
                        "recorded" if row.get("created_at") else "unavailable",
                    )
            if not any(event.get("kind") == "run" for event in canonical):
                for row in status_events:
                    add(row["id"], "run", row, row.get("updated_at"))
            for field, kind in (("awaiting_approval", "approval"), ("awaiting_input", "input")):
                for gate in run.get(field, []):
                    if any(
                        event.get("kind") == kind and event.get("node_id") == gate["id"]
                        for event in canonical
                    ):
                        continue
                    add(
                        f"{kind}:{gate['id']}",
                        kind,
                        {
                            "node_id": gate["id"],
                            "node_name": gate.get("name", gate["id"]),
                            "status": "waiting",
                        },
                        None,
                        "unavailable",
                    )
            traced_states = {
                (event.get("node_id"), event.get("node_status", event.get("status")))
                for event in events
                if event["kind"] in {"node", "branch", "approval", "input"}
            }
            for node_id, status in states.items():
                if (node_id, status) not in traced_states:
                    add(
                        "checkpoint:" + node_id,
                        "node",
                        {"node_id": node_id, "status": status},
                        None,
                        "checkpoint",
                    )
            artifact_ids = set(run.get("artifact_ids", []))
            artifact_ids.update(
                run[key] for key in ("artifact_id", "speech_artifact_id") if run.get(key)
            )
            for artifact_id in sorted(artifact_ids):
                try:
                    artifact = studio.catalog.get("artifacts", artifact_id, archived=True)
                    studio.collaboration.owned(artifact, actor)
                    if (artifact.get("owner_id") or "desktop") != expected_owner:
                        continue
                except (FileNotFoundError, ValueError, PermissionError):
                    continue
                if not any(
                    event.get("kind") == "artifact" and event.get("artifact_id") == artifact_id
                    for event in canonical
                ):
                    add(
                        "artifact:" + artifact_id,
                        "artifact",
                        {
                            "artifact_id": artifact_id,
                            "artifact_revision": artifact["revision"],
                            "status": "available",
                            "message": artifact.get("name", "Artifact"),
                        },
                        artifact.get("created_at"),
                    )
            events.sort(
                key=lambda event: (
                    event["at"] is None,
                    event["at"] or "",
                    event.get("recorded_sequence") or 0,
                    event["id"],
                    event["kind"],
                )
            )
            for index, event in enumerate(events, 1):
                event["sequence"] = index
            if not graph["nodes"]:
                self._project_participants(graph, states, events, run)
            total = len(events)
            end = total
            if before:
                matches = [index for index, event in enumerate(events) if event["id"] == before]
                if not matches:
                    raise ValueError(
                        "Execution cursor is no longer available; refresh the latest page"
                    )
                end = matches[0]
            start = max(0, end - limit)
            events = events[start:end]
            node_count = len(graph["nodes"])
            graph["nodes"] = graph["nodes"][:1000]
            visible_nodes = {node["id"] for node in graph["nodes"]}
            graph["edges"] = [
                edge
                for edge in graph["edges"]
                if edge["source"] in visible_nodes and edge["target"] in visible_nodes
            ][:4000]
            states = {key: value for key, value in states.items() if key in visible_nodes}
            return {
                "version": 2,
                "run_id": run_id,
                "status": _label(run.get("status", "unknown")),
                "graph": graph,
                "states": states,
                "timeline": events,
                "invocations": [_fields(row) for row in invocations[:1000]],
                "replay": {
                    "read_only": True,
                    "event_count": total,
                    "cursor": end,
                    "previous_cursor": events[0]["id"] if start else None,
                    "page_start": start,
                    "page_end": end,
                },
                "coverage": {
                    "format": "yaml"
                    if graph_format == "yaml-runs"
                    else "graph-v2"
                    if saved.get("graph", {}).get("version") == 2
                    else "graph-v1"
                    if saved
                    else "run",
                    "graph_nodes_omitted": max(0, node_count - 1000),
                    "missing_timestamps": sum(event["at"] is None for event in events),
                    "legacy_ordering": "Untimed legacy checkpoints follow timed events; their historical order is unknown.",
                    "provider_tool_events": "Only calls exposed by the selected provider or Nexus runtime are recorded.",
                },
            }

    @staticmethod
    def _project_participants(graph, states, events, run):
        """Show recorded participants without treating chronology as causality.

        A run contains its participants. Only explicit parent references create
        parent edges; concurrent agents never acquire a guessed execution order.
        Canvas/YAML workflows keep their authoritative saved graph instead.
        """
        participants = [
            event for event in events if event["kind"] in {"invocation", "delegation", "tool"}
        ]
        if not participants:
            return
        root = "execution:run"
        graph["nodes"].append({"id": root, "name": "Run", "kind": "run"})
        states[root] = _label(run.get("status", "unknown"), 40)
        nodes = {}
        aliases = {}

        def alias(value, identity):
            if value:
                aliases.setdefault(value, set()).add(identity)

        for event in events:
            if event["kind"] == "run":
                event["graph_node_id"] = root
        for event in participants:
            kind = event["kind"]
            source = event.get("source_id")
            if not source:
                source = (
                    event.get("agent_id")
                    if kind == "delegation"
                    else event.get("tool")
                    if kind == "tool"
                    else event["id"]
                )
            source = source or event["id"]
            identity = f"execution:{kind}:" + hashlib.sha256(source.encode()).hexdigest()[:20]
            event["graph_node_id"] = identity
            if identity not in nodes:
                name = (
                    event.get("agent") or event.get("agent_id") or event.get("tool") or kind.title()
                )
                node = {"id": identity, "name": _label(name), "kind": kind}
                if event.get("provider"):
                    node["provider"] = event["provider"]
                graph["nodes"].append(node)
                nodes[identity] = node
            if event.get("status"):
                states[identity] = event["status"]
            for value in (
                event["id"],
                event.get("source_id"),
                event.get("agent_id"),
                event.get("node_id"),
            ):
                alias(value, identity)
            alias(identity, identity)
        parent_edges = set()
        for event in participants:
            parents = aliases.get(event.get("parent_id"), set())
            if len(parents) == 1:
                source = next(iter(parents))
                target = event["graph_node_id"]
                if source != target:
                    parent_edges.add((source, target))
        parented = {target for _, target in parent_edges}
        graph["edges"].extend(
            {"source": source, "target": target, "relationship": "recorded-parent"}
            for source, target in sorted(parent_edges)
        )
        graph["edges"].extend(
            {"source": root, "target": identity, "relationship": "run-membership"}
            for identity in nodes
            if identity not in parented
        )
        graph["projection"] = "recorded-participants"
        graph["relationship_note"] = (
            "Run links show membership, not execution order. Parent links appear only when explicitly recorded."
        )

    @staticmethod
    def _graph(saved, run, traces):
        definition = saved.get("graph", {})
        structured = definition.get("version") == 2
        states = {
            str(k): _label(v, 40) for k, v in saved.get("nodes", run.get("graph_nodes", {})).items()
        }
        nodes, edges = [], []
        known = set()

        def visit(graph, scope=""):
            for node in graph.get("nodes", []):
                identity = scope + node["id"]
                known.add(identity)
                nodes.append(
                    {
                        "id": identity,
                        "name": _label(node.get("name", node["id"])),
                        "kind": _label(node.get("kind", "agent"), 40),
                    }
                )
                body = node.get("body")
                if node.get("kind") == "workflow":
                    body = (
                        saved.get("payload", {})
                        .get("workflow_snapshots", {})
                        .get(node.get("workflow_id"), {})
                        .get("graph")
                    )
                    if (
                        structured
                        and body
                        and any(key.startswith(identity + "/") for key in states)
                    ):
                        visit(body, identity + "/")
                    body = None
                if structured and isinstance(body, dict):
                    prefixes = {
                        key.rsplit("/", 1)[0] + "/"
                        for key in [*states, *saved.get("started", {})]
                        if key.startswith(identity + "/")
                    }
                    for prefix in sorted(prefixes):
                        # Only direct body instances; deeper maps visit recursively.
                        if len(prefix.split("/")) == len(identity.split("/")) + 2:
                            visit(body, prefix)
            for edge in graph.get("edges", []):
                if edge.get("source") in {"START", "END"} or edge.get("target") in {"START", "END"}:
                    continue
                edges.append(
                    {
                        "source": scope + edge["source"],
                        "target": scope + edge["target"],
                        **({"choice": _label(edge["choice"])} if edge.get("choice") else {}),
                    }
                )

        visit(definition, "root/" if structured else "")
        for handler in ("on_success", "on_error"):
            if (
                structured
                and definition.get(handler)
                and any(key.startswith(handler + "/") for key in states)
            ):
                visit(definition[handler], handler + "/")
        for row in traces:
            identity = row.get("node_id")
            if identity and identity not in known:
                known.add(identity)
                nodes.append(
                    {
                        "id": identity,
                        "name": _label(row.get("node_name", identity)),
                        "kind": _label(row.get("kind", "node"), 40),
                    }
                )
        return {"nodes": nodes, "edges": edges}, states

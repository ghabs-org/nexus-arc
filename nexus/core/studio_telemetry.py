"""OpenTelemetry trace and Prometheus metrics exporters with scoped redaction."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime

from nexus import __version__
from nexus.core.studio_execution import _label


def _to_nano(timestamp_str: str | None) -> int:
    if not timestamp_str:
        return 0
    try:
        dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return int(dt.astimezone(UTC).timestamp() * 1_000_000_000)
    except (ValueError, TypeError, OverflowError):
        return 0


def _trace_id(run_id: str) -> str:
    return hashlib.md5(run_id.encode("utf-8")).hexdigest()


def _span_id(item_id: str) -> str:
    return hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:16]


def _otel_attr(key: str, value: str | int | float | bool):
    if type(value) is bool:
        return {"key": key, "value": {"boolValue": value}}
    if type(value) is int:
        return {"key": key, "value": {"intValue": value}}
    if type(value) is float:
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": _label(str(value))}}


class StudioTelemetry:
    """Aggregates relational traces, token usage, OpenTelemetry spans and Prometheus metrics."""

    def __init__(self, studio):
        self.studio = studio

    def summarize_run_tokens(self, run_id: str, actor: dict | None = None) -> dict:
        """Aggregate prompt, completion and total token usage across invocations for a run."""
        run = self.studio.collaboration.owned(self.studio.run(run_id), actor)
        expected_owner = run.get("requester_nexus_id") or run.get("owner_id") or "desktop"
        invocations = [
            row
            for row in self.studio.records("invocation-traces", limit=None)
            if row.get("run_id") == run_id
            and (not self.studio.team or self.studio.team.visible(row, actor))
        ]

        total_prompt = 0
        total_completion = 0
        total_tokens = 0
        by_model = {}
        by_provider = {}
        by_agent = {}
        invocations_with_usage = 0

        for inv in invocations:
            usage = inv.get("usage") or {}
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            total = usage.get("total_tokens", prompt + completion)

            if usage:
                invocations_with_usage += 1

            total_prompt += prompt
            total_completion += completion
            total_tokens += total

            model = inv.get("model") or "default"
            provider = inv.get("provider") or "unknown"
            agent = inv.get("agent") or "assistant"

            for bucket, key in ((by_model, model), (by_provider, provider), (by_agent, agent)):
                entry = bucket.setdefault(
                    key,
                    {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "count": 0},
                )
                entry["prompt_tokens"] += prompt
                entry["completion_tokens"] += completion
                entry["total_tokens"] += total
                entry["count"] += 1

        return {
            "run_id": run_id,
            "owner_id": expected_owner,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "invocations_total": len(invocations),
            "invocations_with_usage": invocations_with_usage,
            "by_model": by_model,
            "by_provider": by_provider,
            "by_agent": by_agent,
        }

    def export_opentelemetry_traces(self, run_id: str, actor: dict | None = None) -> dict:
        """Export OpenTelemetry OTLP JSON representation of a run and its hierarchical traces."""
        run = self.studio.collaboration.owned(self.studio.run(run_id), actor)
        expected_owner = run.get("requester_nexus_id") or run.get("owner_id") or "desktop"

        def rows(collection):
            return [
                row
                for row in self.studio.records(collection, limit=None)
                if row.get("run_id") == run_id
                and (not self.studio.team or self.studio.team.visible(row, actor))
            ]

        invocations = rows("invocation-traces")
        nodes = rows("node-traces")
        tools = rows("tool-traces")

        trace_id_hex = _trace_id(run_id)
        root_span_id = _span_id(f"run-{run_id}")

        spans = []

        # Root span representing the overall run
        run_start = _to_nano(run.get("created_at"))
        run_end = _to_nano(run.get("updated_at") or run.get("created_at"))
        if run_end <= run_start:
            run_end = run_start + 1_000_000_000

        run_status_code = 1 if run.get("status") == "completed" else 2 if run.get("status") in {"failed", "cancelled"} else 0

        run_attrs = [
            _otel_attr("nexus.run_id", run_id),
            _otel_attr("nexus.owner_id", expected_owner),
            _otel_attr("nexus.run_kind", run.get("kind", "workflow")),
            _otel_attr("nexus.run_status", run.get("status", "unknown")),
        ]
        if run.get("task"):
            run_attrs.append(_otel_attr("nexus.task", run["task"]))

        spans.append({
            "traceId": trace_id_hex,
            "spanId": root_span_id,
            "parentSpanId": "",
            "name": f"run:{run.get('kind', 'workflow')}",
            "kind": 1,  # SPAN_KIND_INTERNAL
            "startTimeUnixNano": str(run_start),
            "endTimeUnixNano": str(run_end),
            "status": {"code": run_status_code},
            "attributes": run_attrs,
        })

        # Node spans
        for node in nodes:
            nid = node["id"]
            parent_id = node.get("parent_id")
            parent_span_id = _span_id(parent_id) if parent_id else root_span_id
            start_nano = _to_nano(node.get("created_at") or run.get("created_at"))
            duration_s = node.get("duration_seconds", 0.0) or 0.0
            end_nano = start_nano + int(duration_s * 1_000_000_000)
            if end_nano <= start_nano:
                end_nano = start_nano + 1_000_000

            node_ok = node.get("status") == "completed"
            attrs = [
                _otel_attr("nexus.run_id", run_id),
                _otel_attr("nexus.node_id", node.get("node_id", "")),
                _otel_attr("nexus.node_name", node.get("node_name", "")),
                _otel_attr("nexus.phase", node.get("kind", "node")),
                _otel_attr("nexus.status", node.get("status", "unknown")),
            ]
            spans.append({
                "traceId": trace_id_hex,
                "spanId": _span_id(nid),
                "parentSpanId": parent_span_id,
                "name": f"node:{node.get('node_name') or node.get('node_id', 'step')}",
                "kind": 1,
                "startTimeUnixNano": str(start_nano),
                "endTimeUnixNano": str(end_nano),
                "status": {"code": 1 if node_ok else 2},
                "attributes": attrs,
            })

        # Invocation spans
        for inv in invocations:
            iid = inv["id"]
            parent_id = inv.get("parent_id")
            parent_span_id = _span_id(parent_id) if parent_id else root_span_id
            start_nano = _to_nano(inv.get("created_at") or run.get("created_at"))
            duration_s = inv.get("duration_seconds", 0.0) or 0.0
            end_nano = start_nano + int(duration_s * 1_000_000_000)
            if end_nano <= start_nano:
                end_nano = start_nano + 1_000_000

            inv_ok = inv.get("status") == "completed"
            attrs = [
                _otel_attr("nexus.run_id", run_id),
                _otel_attr("gen_ai.system", inv.get("provider", "unknown")),
                _otel_attr("gen_ai.request.model", inv.get("model") or "default"),
                _otel_attr("gen_ai.agent.name", inv.get("agent") or "assistant"),
                _otel_attr("nexus.status", inv.get("status", "unknown")),
            ]
            usage = inv.get("usage") or {}
            if "prompt_tokens" in usage:
                attrs.append(_otel_attr("gen_ai.usage.prompt_tokens", usage["prompt_tokens"]))
            if "completion_tokens" in usage:
                attrs.append(_otel_attr("gen_ai.usage.completion_tokens", usage["completion_tokens"]))
            if "total_tokens" in usage:
                attrs.append(_otel_attr("gen_ai.usage.total_tokens", usage["total_tokens"]))

            spans.append({
                "traceId": trace_id_hex,
                "spanId": _span_id(iid),
                "parentSpanId": parent_span_id,
                "name": f"agent:{inv.get('agent', 'assistant')}",
                "kind": 3,  # SPAN_KIND_CLIENT
                "startTimeUnixNano": str(start_nano),
                "endTimeUnixNano": str(end_nano),
                "status": {"code": 1 if inv_ok else 2},
                "attributes": attrs,
            })

        # Tool spans
        for tool in tools:
            tid = tool["id"]
            parent_id = tool.get("parent_id")
            parent_span_id = _span_id(parent_id) if parent_id else root_span_id
            start_nano = _to_nano(tool.get("created_at") or run.get("created_at"))
            duration_s = tool.get("duration_seconds", 0.0) or 0.0
            end_nano = start_nano + int(duration_s * 1_000_000_000)
            if end_nano <= start_nano:
                end_nano = start_nano + 1_000_000

            tool_ok = bool(tool.get("ok", True))
            attrs = [
                _otel_attr("nexus.run_id", run_id),
                _otel_attr("tool.name", tool.get("tool", "unknown")),
                _otel_attr("nexus.status", "completed" if tool_ok else "failed"),
            ]
            spans.append({
                "traceId": trace_id_hex,
                "spanId": _span_id(tid),
                "parentSpanId": parent_span_id,
                "name": f"tool:{tool.get('tool', 'tool')}",
                "kind": 3,
                "startTimeUnixNano": str(start_nano),
                "endTimeUnixNano": str(end_nano),
                "status": {"code": 1 if tool_ok else 2},
                "attributes": attrs,
            })

        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "nexus-studio"}},
                            {"key": "service.version", "value": {"stringValue": __version__}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "nexus.studio.observability", "version": "1.0.0"},
                            "spans": spans,
                        }
                    ],
                }
            ]
        }

    def export_prometheus_metrics(self, actor: dict | None = None) -> str:
        """Export standard Prometheus text exposition format metrics scoped to actor."""
        identity = actor["nexus_id"] if actor and self.studio.team else None
        is_owner = actor and actor.get("role") == "owner"

        def visible(record):
            if not self.studio.team or not identity or is_owner:
                return True
            return (record.get("owner_id") or record.get("requester_nexus_id")) == identity

        runs = [r for r in self.studio.records("runs", limit=None) if visible(r)]
        invocations = [r for r in self.studio.records("invocation-traces", limit=None) if visible(r)]
        tools = [r for r in self.studio.records("tool-traces", limit=None) if visible(r)]
        guardrails = [r for r in self.studio.records("guardrail-events", limit=None) if visible(r)]

        lines = []

        # Runs metric
        lines.append("# HELP nexus_runs_total Total number of runs by status and kind")
        lines.append("# TYPE nexus_runs_total counter")
        run_counts = {}
        for r in runs:
            kind = _prom_label(r.get("kind", "workflow"))
            status = _prom_label(r.get("status", "unknown"))
            key = (kind, status)
            run_counts[key] = run_counts.get(key, 0) + 1
        for (kind, status), count in sorted(run_counts.items()):
            lines.append(f'nexus_runs_total{{kind="{kind}",status="{status}"}} {count}')

        # Invocations metric
        lines.append("# HELP nexus_invocations_total Total agent invocations by provider, model, agent and status")
        lines.append("# TYPE nexus_invocations_total counter")
        inv_counts = {}
        token_prompt_counts = {}
        token_comp_counts = {}
        duration_sums = {}
        duration_counts = {}

        for inv in invocations:
            provider = _prom_label(inv.get("provider", "unknown"))
            model = _prom_label(inv.get("model") or "default")
            agent = _prom_label(inv.get("agent") or "assistant")
            status = _prom_label(inv.get("status", "unknown"))
            key = (provider, model, agent, status)
            inv_counts[key] = inv_counts.get(key, 0) + 1

            t_key = (provider, model)
            usage = inv.get("usage") or {}
            p = usage.get("prompt_tokens", 0)
            c = usage.get("completion_tokens", 0)
            token_prompt_counts[t_key] = token_prompt_counts.get(t_key, 0) + p
            token_comp_counts[t_key] = token_comp_counts.get(t_key, 0) + c

            dur = inv.get("duration_seconds")
            if type(dur) in {int, float} and math.isfinite(dur) and dur > 0:
                duration_sums[t_key] = duration_sums.get(t_key, 0.0) + dur
                duration_counts[t_key] = duration_counts.get(t_key, 0) + 1

        for (provider, model, agent, status), count in sorted(inv_counts.items()):
            lines.append(f'nexus_invocations_total{{provider="{provider}",model="{model}",agent="{agent}",status="{status}"}} {count}')

        # Prompt tokens metric
        lines.append("# HELP nexus_prompt_tokens_total Total prompt tokens consumed")
        lines.append("# TYPE nexus_prompt_tokens_total counter")
        for (provider, model), count in sorted(token_prompt_counts.items()):
            lines.append(f'nexus_prompt_tokens_total{{provider="{provider}",model="{model}"}} {count}')

        # Completion tokens metric
        lines.append("# HELP nexus_completion_tokens_total Total completion tokens consumed")
        lines.append("# TYPE nexus_completion_tokens_total counter")
        for (provider, model), count in sorted(token_comp_counts.items()):
            lines.append(f'nexus_completion_tokens_total{{provider="{provider}",model="{model}"}} {count}')

        # Invocations duration metric
        lines.append("# HELP nexus_invocation_duration_seconds Total duration of invocations in seconds")
        lines.append("# TYPE nexus_invocation_duration_seconds summary")
        for (provider, model), d_sum in sorted(duration_sums.items()):
            d_count = duration_counts.get((provider, model), 0)
            lines.append(f'nexus_invocation_duration_seconds_sum{{provider="{provider}",model="{model}"}} {round(d_sum, 3)}')
            lines.append(f'nexus_invocation_duration_seconds_count{{provider="{provider}",model="{model}"}} {d_count}')

        # Tool calls metric
        lines.append("# HELP nexus_tool_calls_total Total tool calls by tool name and status")
        lines.append("# TYPE nexus_tool_calls_total counter")
        tool_counts = {}
        for t in tools:
            name = _prom_label(t.get("tool", "unknown"))
            ok = "completed" if t.get("ok", True) else "failed"
            key = (name, ok)
            tool_counts[key] = tool_counts.get(key, 0) + 1
        for (name, status), count in sorted(tool_counts.items()):
            lines.append(f'nexus_tool_calls_total{{tool="{name}",status="{status}"}} {count}')

        # Guardrails blocked metric
        lines.append("# HELP nexus_guardrail_events_total Total guardrail output blocks")
        lines.append("# TYPE nexus_guardrail_events_total counter")
        lines.append(f'nexus_guardrail_events_total{{status="blocked"}} {len(guardrails)}')

        return "\n".join(lines) + "\n"


def _prom_label(value: str) -> str:
    cleaned = _label(str(value), 100)
    cleaned = cleaned.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return cleaned

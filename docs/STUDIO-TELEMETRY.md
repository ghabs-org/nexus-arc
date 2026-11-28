# Studio Telemetry and Observability

Studio provides built-in telemetry aggregating parent-child execution traces, model
token usage, OpenTelemetry (OTLP JSON) spans, and Prometheus exposition metrics.

## Parent-child span relationships

Studio tracks hierarchical execution relationships across run lifecycles:

- **Run span**: Root span (`run:<kind>`) representing overall workflow or chat execution.
- **Node spans**: Child spans (`node:<name>`) representing workflow step executions,
  linked via `parent_id` (or defaulting to the root run span).
- **Agent invocation spans**: Client spans (`agent:<name>`) representing LLM provider
  interactions, with `parent_id` linking back to the owning workflow node or run.
- **Tool spans**: Client spans (`tool:<name>`) executed within an agent tool loop,
  linked via `parent_id` to the parent LLM invocation that produced the tool call.

All spans carry stable, deterministic 16-hex `spanId` values and 32-hex `traceId` values
derived from the underlying resource identities.

## Token usage aggregation

For any run with LLM invocations, token usage is aggregated across turns:

- `total_prompt_tokens`: Cumulative prompt tokens across all invocations.
- `total_completion_tokens`: Cumulative completion tokens across all invocations.
- `total_tokens`: Total tokens consumed.
- `invocations_total`: Total invocation count.
- `invocations_with_usage`: Invocations reporting valid token metadata.
- Breakdown dictionaries: `by_model`, `by_provider`, and `by_agent`.

Accessible programmatically via `studio.telemetry.summarize_run_tokens(run_id, actor)`
and over HTTP via `GET /api/workbench/traces/<run_id>/usage`.

## OpenTelemetry OTLP export

Run traces export directly to OpenTelemetry OTLP/HTTP JSON format (v1):

- Resource attributes identify `service.name` (`nexus-studio`) and `service.version`.
- Standard semantic conventions:
  - `gen_ai.system`: LLM provider name.
  - `gen_ai.request.model`: Model identifier.
  - `gen_ai.agent.name`: Agent name.
  - `gen_ai.usage.*`: Prompt, completion, and total token usage.
  - `nexus.*`: Run, node, owner, and execution status metadata.
- Start and end timestamps converted to nanosecond Unix epochs.
- Span statuses adhere to OpenTelemetry codes (1 = OK, 2 = ERROR).

Accessible programmatically via `studio.telemetry.export_opentelemetry_traces(run_id, actor)`
and over HTTP via `GET /api/workbench/traces/<run_id>/otel`.

## Prometheus metrics export

System and run metrics export in standard Prometheus text exposition format:

- `nexus_runs_total`: Counter by `kind` and `status`.
- `nexus_invocations_total`: Counter by `provider`, `model`, and `status`.
- `nexus_prompt_tokens_total`: Counter by `provider` and `model`.
- `nexus_completion_tokens_total`: Counter by `provider` and `model`.
- `nexus_tool_calls_total`: Counter by `tool` and `status`.
- `nexus_invocation_duration_seconds_*`: Histogram and summary for call latency.
- `nexus_guardrail_events_total`: Counter by `guardrail` and `disposition`.

Accessible programmatically via `studio.telemetry.export_prometheus_metrics(actor)`
and over HTTP via `GET /api/workbench/metrics/prometheus` and `GET /api/metrics`.

## Secret redaction and privacy

Telemetry exports automatically sanitize labels, attributes, and task texts:
- Values associated with sensitive keys (`secret`, `key`, `token`, `password`, `auth`,
  `bearer`, `credential`, `private`) are redacted to `[REDACTED]`.
- Task prompts, tool arguments, and output strings are never exposed in Prometheus metric
  labels.

## Team scoping and authorization

All telemetry queries enforce workspace team policies:
- Operators and builders only observe traces and usage for runs visible to their account.
- Workspace owners observe all runs within their scope.
- Unauthenticated requests are rejected with HTTP 401/403.

## Compatibility

Storage is entirely additive:
- Relational `parent_id` fields are populated on new records without requiring migration
  of existing records.
- Legacy traces without token usage or parent links remain readable and export cleanly
  with graceful defaults.

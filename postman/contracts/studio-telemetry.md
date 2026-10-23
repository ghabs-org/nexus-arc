# Studio telemetry and observability endpoints

Telemetry endpoints provide token usage summaries, OpenTelemetry traces, and
Prometheus metrics using existing Studio sessions and team policy authorization.

## Endpoints

### 1. Token usage summary

`GET /api/workbench/traces/<run_id>/usage`

Requires authenticated session with view access to `<run_id>`.

Response format: `application/json`

```json
{
  "run_id": "run-12345",
  "owner_id": "alice",
  "total_prompt_tokens": 120,
  "total_completion_tokens": 45,
  "total_tokens": 165,
  "invocations_total": 2,
  "invocations_with_usage": 2,
  "by_model": {
    "gpt-5": {
      "prompt_tokens": 120,
      "completion_tokens": 45,
      "total_tokens": 165,
      "count": 2
    }
  },
  "by_provider": {
    "codex": {
      "prompt_tokens": 120,
      "completion_tokens": 45,
      "total_tokens": 165,
      "count": 2
    }
  },
  "by_agent": {
    "assistant": {
      "prompt_tokens": 120,
      "completion_tokens": 45,
      "total_tokens": 165,
      "count": 2
    }
  }
}
```

### 2. OpenTelemetry traces export

`GET /api/workbench/traces/<run_id>/otel`

Requires authenticated session with view access to `<run_id>`.

Response format: `application/json` (OpenTelemetry OTLP JSON trace schema v1)

- Root span: `run:<kind>` with `traceId` and `spanId` derived deterministically.
- Child spans: `node:<name>`, `agent:<name>`, `tool:<name>`.
- Spans reference `parentSpanId` of their invoking entity.
- Semantic attributes include `gen_ai.*` usage and provider conventions.
- Sensitive labels and credentials matching secret keywords are redacted.

### 3. Prometheus metrics exposition

`GET /api/workbench/metrics/prometheus`
`GET /api/metrics`

Requires authenticated session (or local unauthenticated access when team policy is disabled).

Response format: `text/plain; version=0.0.4`

- Standard Prometheus metric types: counters, summaries, histograms.
- Metric series include `nexus_runs_total`, `nexus_invocations_total`,
  `nexus_prompt_tokens_total`, `nexus_completion_tokens_total`, and `nexus_tool_calls_total`.
- Label values are sanitized; high-cardinality task text is omitted.

## Authorization and permissions

- Non-owners receive HTTP 403 when requesting traces for runs they do not own.
- Anonymous callers receive HTTP 401 when team policy is enabled.
- Missing run IDs return HTTP 404.

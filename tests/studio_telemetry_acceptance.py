"""Acceptance fixture and verification for Studio Telemetry and Observability."""

import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests.studio_sharing_team_acceptance import create_fixture


def run_acceptance_checks(base_url, run_id):
    alice_cookie = f"nexus_web_session={'a' * 32}"
    bob_cookie = f"nexus_web_session={'b' * 32}"

    # 1. Verify token usage endpoint for owner Alice
    req = urllib.request.Request(f"{base_url}/api/workbench/traces/{run_id}/usage")
    req.add_header("Cookie", alice_cookie)
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode())
        assert data["run_id"] == run_id
        assert data["total_prompt_tokens"] == 150
        assert data["total_completion_tokens"] == 60
        assert data["total_tokens"] == 210
        assert "gpt-5" in data["by_model"]

    # 2. Verify OpenTelemetry endpoint for owner Alice
    req = urllib.request.Request(f"{base_url}/api/workbench/traces/{run_id}/otel")
    req.add_header("Cookie", alice_cookie)
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        otel = json.loads(resp.read().decode())
        assert "resourceSpans" in otel
        spans = otel["resourceSpans"][0]["scopeSpans"][0]["spans"]
        run_span = next(s for s in spans if s["name"].startswith("run:"))
        inv_span = next(s for s in spans if s["name"] == "agent:Analyst")
        tool_span = next(s for s in spans if s["name"] == "tool:search_data")
        assert inv_span["parentSpanId"] == run_span["spanId"]
        assert tool_span["parentSpanId"] == inv_span["spanId"]

    # 3. Verify access rejection for non-owner Bob
    req = urllib.request.Request(f"{base_url}/api/workbench/traces/{run_id}/usage")
    req.add_header("Cookie", bob_cookie)
    try:
        urllib.request.urlopen(req)
        raise AssertionError("Expected 403 for unauthorized viewer")
    except urllib.error.HTTPError as exc:
        assert exc.code == 403

    # 4. Verify Prometheus metrics endpoint
    req = urllib.request.Request(f"{base_url}/api/workbench/metrics/prometheus")
    req.add_header("Cookie", alice_cookie)
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 200
        metrics = resp.read().decode()
        assert "# TYPE nexus_runs_total counter" in metrics
        assert "# TYPE nexus_invocations_total counter" in metrics
        assert 'nexus_prompt_tokens_total{provider="codex",model="gpt-5"} 150' in metrics

    print("All Studio Telemetry acceptance checks passed successfully.")


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-telemetry-acceptance-") as root:
        studio, handler, _ = create_fixture(root)

        # Seed test data for Alice
        run = studio.start_run({"kind": "chat", "task": "Telemetry run", "requester_nexus_id": "alice"})
        run_id = run["id"]

        inv_id = f"inv-{run_id}"
        studio._write(
            studio._path("invocation-traces", inv_id),
            {
                "id": inv_id,
                "run_id": run_id,
                "agent": "Analyst",
                "provider": "codex",
                "model": "gpt-5",
                "status": "completed",
                "duration_seconds": 0.45,
                "usage": {"prompt_tokens": 150, "completion_tokens": 60, "total_tokens": 210},
                "created_at": run.get("created_at"),
                "owner_id": "alice",
            },
        )

        studio._write(
            studio._path("tool-traces", f"tool-{run_id}"),
            {
                "id": f"tool-{run_id}",
                "run_id": run_id,
                "tool": "search_data",
                "parent_id": inv_id,
                "ok": True,
                "created_at": run.get("created_at"),
                "duration_seconds": 0.08,
                "owner_id": "alice",
            },
        )

        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        base_url = f"http://127.0.0.1:{server.server_port}"
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            run_acceptance_checks(base_url, run_id)
        finally:
            server.shutdown()
            server.server_close()
            studio.close()


if __name__ == "__main__":
    main()

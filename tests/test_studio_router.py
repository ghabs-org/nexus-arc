"""Studio + nexus-router: Auto routing, outcomes, feedback, fallback, usage, router and OpenClaw views."""

import time
from types import SimpleNamespace

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_observability import normalize_usage
from nexus.core.studio_runtime import RuntimeServiceUnavailableError, StudioModelRouter

DECISION = {
    "decision_id": "dec-1",
    "selected_model": "anthropic/claude-opus-5-5",
    "fallbacks": ["openai-codex/gpt-6.1-sol"],
    "task_type": "coding",
}


class Provider:
    def __init__(self, name, calls, *, ready=True, fail=None, usage=None):
        self.name, self.calls, self.ready, self.fail, self.usage = name, calls, ready, fail, usage

    async def check_availability(self):
        return self.ready

    async def execute_agent(self, context):
        self.calls.append((self.name, context))
        if self.fail:
            return AgentResult(success=False, output="", error=self.fail)
        return AgentResult(
            success=True,
            output=f"Reply from {self.name}",
            provider_used=self.name,
            metadata={"usage": self.usage} if self.usage else {},
        )


class StreamingProvider(Provider):
    async def execute_agent_streaming(self, context, on_delta):
        self.calls.append((self.name, context))
        on_delta("partial answer")
        return AgentResult(success=False, output="partial answer", error="rate limit reached")


class FakeRouter:
    url = "http://127.0.0.1:7771"

    def __init__(self, decision=DECISION, error=None):
        self.decision, self.error = decision, error
        self.routes, self.outcomes, self.feedbacks = [], [], []
        self.fields, self.observed = [], []
        self.stats_error = None

    def route(self, identity, task, allowed=None, key=None, **fields):
        self.routes.append((identity, task, allowed, key))
        self.fields.append(fields)
        if self.error:
            raise self.error
        return dict(self.decision)

    def observe(self, identity, provider, http_status=None, error_type=None, **fields):
        self.observed.append((provider, http_status, error_type))

    def outcome(self, identity, decision_id, **fields):
        self.outcomes.append((decision_id, fields))

    def feedback(self, identity, payload):
        self.feedbacks.append(payload)

    def diagnostics(self, identity=None):
        return {"warnings": [], "providers": {
            "anthropic": {"enabled": True, "health_score": 0.4, "auth": "ok", "quota": "low", "cooldown_until": None},
            "github-copilot": {"enabled": True, "health_score": 1.0, "auth": "ok", "quota": "healthy"}}}

    def pricing(self, identity=None):
        return {"models": {"anthropic/*": {"input_per_mtok": 3.0, "output_per_mtok": 15.0}},
                "subscription_providers": []}

    def decisions(self, identity=None, **params):
        return {"items": [{"id": "dec-1", "source_tag": "studio:r"}]}

    def cli_status(self, identity=None):
        return {"active_session_pins": []}

    def stats(self, identity=None):
        if self.stats_error:
            raise self.stats_error
        return {"models": []}


def make(tmp_path, *, router=None, behavior=None, streaming=()):
    calls = []
    behavior = behavior or {}

    def factory(name):
        cls = StreamingProvider if name in streaming else Provider
        return cls(name, calls, **behavior.get(name, {}))

    options = {"router_client": router} if router else None
    service = StudioService(tmp_path, provider_factory=factory, workbench_options=options)
    service.providers = lambda: [
        {"id": "claude-code", "configured": True},
        {"id": "codex", "configured": True},
        {"id": "copilot", "configured": False},
    ]
    return service, calls


def wait_run(service, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = service.run(identity)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.01)
    pytest.fail("Studio task did not finish")


def test_auto_routes_to_mapped_provider_and_reports_outcome(tmp_path):
    router = FakeRouter()
    service, calls = make(
        tmp_path,
        router=router,
        behavior={"claude-code": {"usage": {"input_tokens": 7, "output_tokens": 3}}},
    )
    try:
        run = wait_run(
            service, service.start_run({"task": "fix the parser", "provider": "auto"})["id"]
        )
        assert run["status"] == "completed", run.get("error")
        assert [(name, ctx.model_override) for name, ctx in calls] == [
            ("claude-code", "claude-opus-5-5")
        ]
        assert run["provider"] == "claude-code" and run["provider_requested"] == "auto"
        assert (
            run["router_decision_id"] == "dec-1"
            and run["routed_model"] == "anthropic/claude-opus-5-5"
        )
        identity, task, allowed, key = router.routes[0]
        assert task.startswith("fix the parser") or "fix the parser" in task
        assert allowed == ["anthropic", "openai-codex"] and key == run["session_id"]
        [(decision_id, fields)] = router.outcomes
        assert decision_id == "dec-1" and fields["success"] is True
        assert fields["input_tokens"] == 7 and fields["output_tokens"] == 3
        assert "fallback_used" not in fields and "user_override" not in fields
    finally:
        service.close()


def test_auto_falls_back_on_a_limit_and_reports_it(tmp_path):
    router = FakeRouter()
    service, calls = make(
        tmp_path, router=router, behavior={"claude-code": {"fail": "You've hit your usage limit"}}
    )
    try:
        run = wait_run(service, service.start_run({"task": "t", "provider": "auto"})["id"])
        assert run["status"] == "completed", run.get("error")
        assert [name for name, _ in calls] == ["claude-code", "codex"]
        assert calls[1][1].model_override == "gpt-6.1-sol"
        assert run["fallback_from"] == "claude-code"
        assert run["attempts"] == [
            {"provider": "claude-code", "model": "claude-opus-5-5", "reason": "limit"}
        ]
        fields = router.outcomes[0][1]
        assert (
            fields["fallback_used"] is True
            and fields["fallback_model"] == "openai-codex/gpt-6.1-sol"
        )
    finally:
        service.close()


def test_auto_primary_not_ready_is_an_override_not_a_failure(tmp_path):
    router = FakeRouter()
    service, calls = make(tmp_path, router=router, behavior={"claude-code": {"ready": False}})
    try:
        run = wait_run(service, service.start_run({"task": "t", "provider": "auto"})["id"])
        assert run["status"] == "completed" and [name for name, _ in calls] == ["codex"]
        fields = router.outcomes[0][1]
        assert (
            fields["user_override"] is True
            and fields["user_override_model"] == "openai-codex/gpt-6.1-sol"
        )
        assert "fallback_used" not in fields
    finally:
        service.close()


def test_auto_still_answers_when_router_is_down(tmp_path):
    router = FakeRouter(
        error=RuntimeServiceUnavailableError("Configured runtime service is unavailable")
    )
    service, calls = make(tmp_path, router=router)
    try:
        run = wait_run(service, service.start_run({"task": "t", "provider": "auto"})["id"])
        assert run["status"] == "completed"
        assert calls[0][0] == "claude-code" and calls[0][1].model_override is None
        assert "unavailable" in run["router_error"] and "router_decision_id" not in run
        assert router.outcomes == []
    finally:
        service.close()


def test_auto_failure_is_reported_as_unsuccessful(tmp_path):
    router = FakeRouter(decision={**DECISION, "fallbacks": []})
    service, _ = make(tmp_path, router=router, behavior={"claude-code": {"fail": "model crashed"}})
    try:
        run = wait_run(service, service.start_run({"task": "t", "provider": "auto"})["id"])
        assert run["status"] == "failed"
        assert router.outcomes[0][1]["success"] is False
    finally:
        service.close()


def test_auto_requires_a_configured_router(tmp_path):
    service, _ = make(tmp_path)
    try:
        with pytest.raises(ValueError, match="Unknown provider"):
            service.start_run({"task": "t", "provider": "auto"})
    finally:
        service.close()


def test_controls_fallback_providers_retry_only_limits(tmp_path):
    service, calls = make(tmp_path, behavior={"claude-code": {"fail": "429 Too Many Requests"}})
    try:
        controls = service.workbench.controls(None)
        assert controls["fallback_providers"] == []
        saved = service.workbench.save_controls({**controls, "fallback_providers": ["codex"]}, None)
        assert saved["fallback_providers"] == ["codex"]
        # Older clients that omit the field keep the saved list.
        kept = service.workbench.save_controls(
            {k: v for k, v in saved.items() if k != "fallback_providers"}, None
        )
        assert kept["fallback_providers"] == ["codex"]
        for bad in (["codex", "codex"], ["nope"], "codex"):
            with pytest.raises(ValueError, match="Fallback"):
                service.workbench.save_controls({**kept, "fallback_providers": bad}, None)
        run = wait_run(service, service.start_run({"task": "t", "provider": "claude-code"})["id"])
        assert run["status"] == "completed" and [name for name, _ in calls] == [
            "claude-code",
            "codex",
        ]
        assert run["fallback_from"] == "claude-code"
    finally:
        service.close()


def test_non_limit_failures_and_streamed_output_do_not_fall_back(tmp_path):
    service, calls = make(
        tmp_path,
        behavior={"claude-code": {"fail": "syntax error in tool call"}},
        streaming=("agy",),
    )
    try:
        controls = service.workbench.controls(None)
        service.workbench.save_controls({**controls, "fallback_providers": ["codex"]}, None)
        run = wait_run(service, service.start_run({"task": "t", "provider": "claude-code"})["id"])
        assert run["status"] == "failed" and [name for name, _ in calls] == ["claude-code"]
        calls.clear()
        run = wait_run(service, service.start_run({"task": "t", "provider": "agy"})["id"])
        assert run["status"] == "failed" and [name for name, _ in calls] == [
            "agy"
        ]  # already streamed
    finally:
        service.close()


def test_usage_is_normalized_and_summarized_per_user(tmp_path):
    assert normalize_usage(
        {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 2}
    ) == {"prompt_tokens": 15, "completion_tokens": 2, "total_tokens": 17}
    assert normalize_usage({"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}) == {
        "prompt_tokens": 3,
        "completion_tokens": 1,
        "total_tokens": 4,
    }
    assert normalize_usage({"input_tokens": -1}) == {} and normalize_usage(None) == {}
    service, _ = make(
        tmp_path, behavior={"claude-code": {"usage": {"input_tokens": 10, "output_tokens": 2}}}
    )
    try:
        wait_run(service, service.start_run({"task": "t", "provider": "claude-code"})["id"])
        wait_run(service, service.start_run({"task": "t", "provider": "codex"})["id"])
        summary = service.workbench.get(["usage"], None, {"days": ["7"]})
        rows = {row["provider"]: row for row in summary["items"]}
        assert rows["claude-code"]["total_tokens"] == 12 and rows["claude-code"]["reported"] == 1
        assert rows["codex"]["reported"] == 0 and rows["codex"]["invocations"] == 1
        assert summary["providers"][0]["provider"] == "claude-code"
    finally:
        service.close()


def test_router_feedback_uses_the_runs_decision(tmp_path):
    router = FakeRouter()
    service, _ = make(tmp_path, router=router)
    try:
        run = wait_run(service, service.start_run({"task": "t", "provider": "auto"})["id"])
        result = service.collaboration.router_feedback(
            {
                "run_id": run["id"],
                "verdict": "wrong",
                "corrected_task": "reasoning",
                "model_verdict": "too_cheap",
                "decision_id": "forged",
            },
            None,
        )
        [payload] = router.feedbacks
        assert payload["decision_id"] == "dec-1" and payload["source_surface"] == "studio"
        assert payload["corrected_task"] == "reasoning" and payload["model_verdict"] == "too_cheap"
        assert (
            service.run(run["id"])["router_feedback"]["verdict"]
            == "wrong"
            == result["router_feedback"]["verdict"]
        )
        with pytest.raises(ValueError, match="Corrected task"):
            service.collaboration.router_feedback(
                {"run_id": run["id"], "verdict": "wrong", "corrected_task": "x"}, None
            )
        with pytest.raises(ValueError, match="Verdict"):
            service.collaboration.router_feedback({"run_id": run["id"], "verdict": "meh"}, None)
        plain = wait_run(service, service.start_run({"task": "t", "provider": "codex"})["id"])
        with pytest.raises(ValueError, match="not chosen by the model router"):
            service.collaboration.router_feedback(
                {"run_id": plain["id"], "verdict": "correct"}, None
            )
    finally:
        service.close()


def test_router_overview_is_failure_tolerant(tmp_path):
    router = FakeRouter()
    router.stats_error = RuntimeServiceUnavailableError("down")
    service, _ = make(tmp_path, router=router)
    try:
        overview = service.workbench.get(["router"], None)
        assert overview["configured"] is True and overview["diagnostics"]["warnings"] == []
        assert overview["stats"] == {"error": "down"}
    finally:
        service.close()
    bare, _ = make(tmp_path / "bare")
    try:
        assert bare.workbench.get(["router"], None) == {"configured": False}
    finally:
        bare.close()


def test_router_overview_is_owner_only_in_team_mode(tmp_path):
    service, _ = make(tmp_path, router=FakeRouter())
    try:
        service.team = SimpleNamespace()
        with pytest.raises(PermissionError, match="owners"):
            service.workbench.router_overview({"nexus_id": "u", "role": "operator"})
    finally:
        service.team = None
        service.close()


def test_openclaw_overview_lists_bound_runs_and_open_gates(tmp_path):
    service, _ = make(tmp_path)
    try:
        record = {
            "id": "oc1",
            "kind": "entrypoints",
            "owner_id": None,
            "name": "Telegram ops",
            "description": "",
            "data": {
                "type": "openclaw",
                "enabled": True,
                "allowed_channels": ["telegram"],
                "operator_targets": ["agents:helper"],
                "identity_map": {"42": "desktop"},
                "token_env": "OC_TOKEN",
            },
            "revision": 1,
            "archived": False,
            "created_at": "2026-11-08",
            "updated_at": "2026-11-08",
        }
        service._write(service.catalog._path("entrypoints", "oc1"), record)
        run = wait_run(
            service, service.start_run({"task": "status report", "provider": "codex"})["id"]
        )
        scope = {
            "entrypoint_id": "oc1",
            "sender": "42",
            "channel": "telegram",
            "requester_nexus_id": None,
        }
        service._write(
            service._path("openclaw-runs", run["id"]),
            {"id": run["id"], "scope": scope, "target": "agents:helper"},
        )
        service._write(
            service._path("channel-events", "e1"),
            {
                "id": "e1",
                **scope,
                "run_id": run["id"],
                "task": "status report",
                "openclaw_operator": True,
            },
        )
        service._write(
            service._path("openclaw-gates", "g1"),
            {
                "id": "g1",
                "scope": scope,
                "run_id": run["id"],
                "used": False,
                "expires": time.time() + 60,
            },
        )
        service._write(
            service._path("openclaw-receipts", "r1"), {"id": "r1", "status": "dispatching"}
        )
        overview = service.features.get(["openclaw"], {}, None)
        [item] = overview["items"]
        assert item["name"] == "Telegram ops" and item["targets"] == ["agents:helper"]
        assert (
            item["open_gates"] == 1
            and item["token_configured"] is False
            and item["mapped_senders"] == 1
        )
        assert item["runs"][0]["run_id"] == run["id"] and item["runs"][0]["status"] == "completed"
        assert item["runs"][0]["task"] == "status report" and overview["uncertain_receipts"] == 1
        member = service.openclaw.overview.__func__  # members only see their own entrypoints/runs
        service.team = SimpleNamespace()
        try:
            assert member(service.openclaw, {"nexus_id": "bob", "role": "operator"})["items"] == []
        finally:
            service.team = None
    finally:
        service.close()


def test_model_router_client_contract(monkeypatch):
    from unittest.mock import Mock

    remote = Mock(return_value={"selected_model": "openai-codex/gpt-6.1-sol", "decision_id": "d"})
    monkeypatch.setattr("nexus.core.router_client.request_json", remote)
    router = StudioModelRouter({"url": "http://127.0.0.1:7771", "allow_private": True})
    router.studio = SimpleNamespace(team=None)
    decision = router.route(None, "task", ["openai-codex", "anthropic"], "session-1")
    assert decision["selected_model"] == "openai-codex/gpt-6.1-sol"
    body = remote.call_args.kwargs["body"]
    assert (
        body["allowed_providers"] == ["anthropic", "openai-codex"]
        and body["experiment_key"] == "session-1"
    )
    assert body["source_type"] == "studio"
    assert router(None, "task", "agent") == "openai-codex/gpt-6.1-sol"
    remote.return_value = {}
    with pytest.raises(ValueError, match="no usable model"):
        router.route(None, "task")
    router.outcome(None, "d", success=True)
    assert remote.call_args.args[1].endswith("/outcome") and remote.call_args.kwargs["body"] == {
        "decision_id": "d",
        "success": True,
    }
    router.diagnostics()
    assert remote.call_args.args[:2] == ("GET", "http://127.0.0.1:7771/diagnostics")


def test_coordinator_reads_selected_model(monkeypatch):
    from nexus.agents import coordinator

    monkeypatch.setattr(
        "nexus.core.router_client.request_json",
        lambda *a, **k: {"selected_model": "nvidia/x", "decision_id": "d"},
    )
    decision = coordinator._call_nexus_router("http://router", "task")
    assert decision["selected_model"] == "nvidia/x" and decision["decision_id"] == "d"



# ── Round 2: explain, catalog, links, limits, ratings, provider view, experiments, auto everywhere ──


def test_auto_sends_catalog_links_and_signals_and_keeps_explain_per_step(tmp_path):
    router = FakeRouter(decision={**DECISION, "explain": {
        "rules_triggered": ["static_weights", "caller_catalog"],
        "classifier": {"task_type": "coding", "effective_task_type": "coding", "confidence": 0.91, "source": "local"},
        "model_scores": [{"model": "anthropic/claude-opus-5-5", "total": 0.82}, {"model": "x/y", "total": 0.7},
                         {"model": "x/z", "total": 0.6}, {"model": "x/w", "total": 0.5}]}})
    service, _ = make(tmp_path, router=router)
    try:
        first = wait_run(service, service.start_run({"task": "fix the parser", "provider": "auto"})["id"])
        second = wait_run(service, service.start_run(
            {"task": "and the tests", "provider": "auto", "session_id": first["session_id"]})["id"])
        fields = router.fields[-1]
        assert {"id": "anthropic/claude-opus-5-5", "vision": True} in fields["available_models"]
        assert not any(m["id"].startswith("github-copilot/") for m in fields["available_models"])  # not ready
        assert fields["nexus_context"]["nexus_project"] == service.root.name
        assert fields["nexus_context"]["nexus_workflow_id"] == second["id"]
        assert fields["source_tag"] == f"studio:{second['id']}"
        assert "fix the parser" in fields["conversation_context"]
        run = service.run(second["id"])
        assert run["router_explain"]["task"] == "coding" and len(run["router_explain"]["candidates"]) == 3
        assert run["router_explain"]["considered"] == 4
        [step] = run["router_decisions"]
        assert step["decision_id"] == "dec-1" and step["outcome"] == "success" and step["provider"] == "claude-code"
    finally:
        service.close()


def test_node_task_type_hint_is_sent_as_the_classifier(tmp_path):
    router = FakeRouter()
    service, _ = make(tmp_path, router=router)
    try:
        asyncio_run = __import__("asyncio").run
        asyncio_run(service._resolve_auto({"router_task_type": "code_review", "router_step": "review"}, "t", "run-x"))
        assert router.fields[-1]["classifier"] == {"task_type": "code_review", "confidence": 0.9}
        assert router.fields[-1]["nexus_context"]["nexus_step_id"] == "review"
        workflow = service.graph.structured
        node = {"id": "draft", "kind": "agent", "agent_id": "a", "task_type": "coding"}
        with pytest.raises(ValueError, match="task_type"):
            workflow.validate({"version": 2, "nodes": [{**node, "task_type": "dancing"}], "edges": []})
        with pytest.raises(ValueError, match="task_type"):
            workflow.validate({"version": 2, "nodes": [{"id": "t", "kind": "tool", "tool_id": "x", "task_type": "coding"}], "edges": []})
    finally:
        service.close()


def test_limits_are_observed_by_the_router_even_without_auto(tmp_path):
    router = FakeRouter()
    service, calls = make(tmp_path, router=router, behavior={"claude-code": {"fail": "429 Too Many Requests"}})
    try:
        controls = service.workbench.controls(None)
        service.workbench.save_controls({**controls, "fallback_providers": ["codex"]}, None)
        run = wait_run(service, service.start_run({"task": "t", "provider": "claude-code"})["id"])
        assert run["status"] == "completed"
        assert router.observed == [("anthropic", 429, "rate_limit")]
    finally:
        service.close()


def test_thumbs_on_a_routed_answer_teach_the_router(tmp_path):
    router = FakeRouter()
    service, _ = make(tmp_path, router=router)
    try:
        run = wait_run(service, service.start_run({"task": "t", "provider": "auto"})["id"])
        service.collaboration.feedback({"run_id": run["id"], "rating": -1}, None)
        [payload] = router.feedbacks
        assert payload["decision_id"] == "dec-1" and payload["verdict"] == "wrong"
        assert payload["reason_tag"] == "quality" and payload["source_surface"] == "studio"
        plain = wait_run(service, service.start_run({"task": "t", "provider": "codex"})["id"])
        service.collaboration.feedback({"run_id": plain["id"], "rating": 1}, None)
        assert len(router.feedbacks) == 1  # unrouted answers stay local
    finally:
        service.close()


def test_router_view_per_studio_provider_is_cached(tmp_path):
    router = FakeRouter()
    calls = []
    original = router.diagnostics
    router.diagnostics = lambda identity=None: calls.append(1) or original(identity)
    service, _ = make(tmp_path, router=router)
    try:
        items = service.workbench.get(["router-providers"], None)["items"]
        assert items["claude-code"]["health_score"] == 0.4 and items["claude"]["router_provider"] == "anthropic"
        assert items["copilot"]["auth"] == "ok"  # Copilot gets a sign-in state from the router
        service.workbench.get(["router-providers"], None)
        assert len(calls) == 1
        service.team = SimpleNamespace()
        try:
            assert service.workbench.router_providers({"nexus_id": "u", "role": "operator"}) == {"items": {}}
        finally:
            service.team = None
    finally:
        service.close()


def test_experiment_variants_accept_auto_with_route_mode(tmp_path):
    from nexus.core.studio_experiments import validate_variants

    assert validate_variants([{"name": "router fast", "provider": "auto", "route_mode": "fast"}])
    with pytest.raises(ValueError, match="route_mode"):
        validate_variants([{"name": "x", "provider": "codex", "route_mode": "fast"}])
    with pytest.raises(ValueError, match="route_mode"):
        validate_variants([{"name": "x", "provider": "auto", "route_mode": "warp"}])
    router = FakeRouter()
    service, _ = make(tmp_path, router=router)
    try:
        __import__("asyncio").run(service.router_case_outcome({}, "dec-9", False, 0))
        assert router.outcomes == [("dec-9", {"success": False, "latency_ms": router.outcomes[0][1]["latency_ms"]})]
    finally:
        service.close()


def test_auto_for_saved_agents_entrypoints_and_quick_build(tmp_path):
    from nexus.core.studio_messaging import validate_channel

    service, _ = make(tmp_path)
    try:
        with pytest.raises(ValueError, match="Unknown provider"):
            service.save_definition("agents", {"name": "Auto agent", "provider": "auto", "instructions": "x"})
    finally:
        service.close()
    service, _ = make(tmp_path / "with-router", router=FakeRouter())
    try:
        saved = service.save_definition("agents", {"name": "Auto agent", "provider": "auto", "instructions": "x"})
        assert service.definition("agents", saved["id"])["spec"]["provider"] == "auto"
        service.builder.validate_request({"task": "build a triage team", "provider": "auto", "build_id": "b1"})
    finally:
        service.close()
    base = {"type": "openclaw", "identity_map": {"1": "desktop"}, "allowed_channels": ["telegram"],
            "operator_targets": ["agents:helper"]}
    assert validate_channel({**base, "provider": "auto"})["provider"] == "auto"
    with pytest.raises(ValueError, match="OpenClaw provider"):
        validate_channel({**base, "provider": "bogus"})
    with pytest.raises(ValueError, match="OpenClaw provider"):
        validate_channel({**base, "type": "telegram", "provider": "auto", "operator_targets": None} if False else
                         {"type": "telegram", "identity_map": {"1": "desktop"}, "allowed_channels": ["c"], "provider": "auto"})


def test_route_preview_is_a_dry_run(tmp_path):
    router = FakeRouter()
    service, _ = make(tmp_path, router=router)
    try:
        assert service.features.get(["route-preview"], {"task": ["short"]}, None) == {"available": True, "pick": None}
        preview = service.features.get(["route-preview"], {"task": ["please refactor the parser module"]}, None)
        assert preview["pick"]["provider"] == "claude-code" and preview["pick"]["model"] == "claude-opus-5-5"
        assert router.fields[-1]["dry_run"] is True and router.fields[-1]["source_tag"] == "studio:preview"
    finally:
        service.close()
    bare, _ = make(tmp_path / "bare")
    try:
        assert bare.route_preview("please refactor the parser module", None) == {"available": False, "pick": None}
    finally:
        bare.close()


def test_usage_shows_estimated_cost_from_router_pricing(tmp_path):
    from nexus.core.studio_routing import estimate_cost

    table = {"models": {"anthropic/*": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
                        "anthropic/claude-haiku-5-5": {"input_per_mtok": 1.0, "output_per_mtok": 5.0}}}
    assert estimate_cost(table, "claude", "claude-opus-5-5", 1_000_000, 100_000) == (4.5, "tariff")
    assert estimate_cost(table, "claude", "claude-haiku-5-5", 1_000_000, 0) == (1.0, "tariff")
    assert estimate_cost(table, "claude-code", "claude-opus-5-5", 10, 10) == (0.0, "subscription")
    assert estimate_cost(table, "openai", "gpt-x", 10, 10) == (None, None)
    assert estimate_cost(None, "claude", None, 10, 10) == (None, None)
    service, _ = make(tmp_path, router=FakeRouter(), behavior={"claude-code": {"usage": {"input_tokens": 10, "output_tokens": 2}}})
    try:
        wait_run(service, service.start_run({"task": "t", "provider": "claude-code"})["id"])
        summary = service.workbench.get(["usage"], None, {"days": ["7"]})
        assert summary["providers"][0]["cost_source"] == "subscription"
    finally:
        service.close()


def test_router_panel_lists_recent_routed_runs(tmp_path):
    service, _ = make(tmp_path, router=FakeRouter())
    try:
        assert service.workbench.get(["router"], None)["decisions"]["items"][0]["source_tag"] == "studio:r"
    finally:
        service.close()



def test_build_with_ai_design_runs_on_the_routers_pick(tmp_path):
    import asyncio

    router = FakeRouter()
    service, _ = make(tmp_path, router=router)
    seen = {}

    class Designer:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            seen["model"] = context.model_override
            return AgentResult(success=True, output='{"name": "Greeting", "data": {"content": "Hi {{Name}}", "variables": {}}}')

    service.builder.provider_factory = lambda name: seen.setdefault("provider", name) and Designer()
    try:
        run = service.start_run({"kind": "design", "design_kind": "prompt", "task": "a greeting prompt",
                                 "provider": "auto", "requester_nexus_id": None})
        wait_run(service, run["id"])
        assert seen["provider"] == "claude-code" and seen["model"] == "claude-opus-5-5"
        assert service.run(run["id"])["router_decision_id"] == "dec-1"
        assert router.outcomes and router.outcomes[-1][1]["success"] is True
    finally:
        service.close()



def test_saved_model_profiles_join_the_caller_catalog(tmp_path):
    from nexus.core.studio_routing import caller_catalog

    catalog = caller_catalog(["claude-code"], [("claude-code", "claude-opus-6-0"), ("copilot", "gpt-x"), ("claude-code", " ")])
    assert catalog[0] == {"id": "anthropic/claude-opus-6-0", "vision": True}
    assert not any(item["id"].startswith("github-copilot/") for item in catalog)  # copilot not ready
    router = FakeRouter()
    service, _ = make(tmp_path, router=router)
    try:
        service.catalog.save("models", {"name": "Next Claude", "data": {"provider": "claude-code", "model": "claude-opus-6-0"}})
        wait_run(service, service.start_run({"task": "t", "provider": "auto"})["id"])
        assert router.fields[-1]["available_models"][0]["id"] == "anthropic/claude-opus-6-0"
    finally:
        service.close()



def test_studio_cost_applies_long_context_tiers():
    from nexus.core.studio_routing import estimate_cost

    table = {"models": {"anthropic/claude-haiku-5-5": {
        "input_per_mtok": 0.1, "output_per_mtok": 0.5,
        "long_context": {"above_input_tokens": 100000, "input_per_mtok": 0.5, "output_per_mtok": 2.5}}}}
    assert estimate_cost(table, "claude", "claude-haiku-5-5", 100_000, 0) == (0.01, "tariff")
    assert estimate_cost(table, "claude", "claude-haiku-5-5", 200_000, 1_000_000) == (2.6, "tariff")

"""Typed external decisions: strict answers, routing, credentials and checkpoint safety."""

import asyncio
import copy
import json

import pytest

from nexus.adapters.decisions.errors import DecisionCallError
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_decisions import answers_for, validate_questions, wire_questions
from nexus.tools import tool_registry
from tests.test_studio_interop_workflows import run
from tests.test_studio_team import team as team

BINDING = {"backend": "openrouter", "model": "typesafe/jev-1.13"}
QUESTIONS = {
    "team": {
        "type": "choice",
        "instructions": "Choose team",
        "criteria": {"billing": "Payments", "technical": "Bugs", "other": "Other"},
    },
    "urgent": {"type": "boolean", "instructions": "Needs immediate review"},
    "quality": {
        "type": "score",
        "instructions": "Rate detail",
        "criteria": [{"low": "Incomplete"}, {"high": "Complete"}],
    },
}


def answers(choice="billing", confidence=0.9):
    return {
        "team": {
            "type": "choice",
            "choice": choice,
            "confidence": confidence,
            "probabilities": {k: 1 if k == choice else 0 for k in QUESTIONS["team"]["criteria"]},
        },
        "urgent": {"type": "noul", "noul": 0.8},
        "quality": {
            "type": "score",
            "score": 0.75,
            "confidence": 0.7,
            "probabilities": {"0": 0.25, "1": 0.75},
            "legend": {"0": "Incomplete", "1": "Complete"},
        },
    }


def source(mode="handoff"):
    return {
        "agent_name": "decide",
        "agent_type": "workflow",
        "decision": {"model_alias": "triage"},
        "tools": [{"tool_type": "builtin", "tool_name": "record"}],
        "nodes": [
            {
                "id": "triage",
                "type": "decision",
                "questions": copy.deepcopy(QUESTIONS),
                "route_on": "team",
                "routes": {"billing": "billing", "technical": "technical"},
                "default": "general",
                "min_confidence": 0.8,
                "escalate": "review",
                "escalation_mode": mode,
                "input": {"ticket": "{{workflow.input.ticket}}"},
            },
            *[
                {
                    "id": name,
                    "type": "tool",
                    "tool_name": "record",
                    "depends_on": ["triage"],
                    "input": {"label": name},
                }
                for name in ["billing", "technical", "general", "review"]
            ],
        ],
        "output_mapping": {
            "decision": "{{triage.output}}",
            "result": {
                "coalesce": [
                    "{{billing.output}}",
                    "{{technical.output}}",
                    "{{general.output}}",
                    "{{review.output}}",
                ]
            },
        },
    }


def install(studio, workflow):
    report = studio.configuration.convert_interop(
        {
            "documents": [workflow],
            "bindings": {
                "decisions": {"triage": BINDING},
                "tools": {"record": "interop:decision-record"},
            },
        },
        None,
    )
    assert report["valid"], report
    assert not studio.records("runs")
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    return report["document"]


@pytest.mark.parametrize(
    "choice,confidence,expected",
    [
        ("billing", 0.9, "billing"),
        ("technical", 0.9, "technical"),
        ("other", 0.9, "general"),
        ("billing", 0.2, "review"),
    ],
)
def test_multiquestion_direct_default_and_handoff_only_run_selected_branch(
    tmp_path, choice, confidence, expected
):
    calls, requests = [], []

    async def record(label):
        calls.append(label)
        return label

    def decide(state, questions, **kwargs):
        requests.append((state, questions, kwargs))
        return answers(choice, confidence)

    tool_registry.register("interop:decision-record", record, "Synthetic selected effect")
    studio = StudioService(tmp_path, workbench_options={"decision_client": decide})
    try:
        native = install(studio, source())
        result = json.loads(run(studio, "decide", {"ticket": "Synthetic ticket"})["output"])
        assert calls == [expected]
        assert requests[0][0] == {"ticket": "Synthetic ticket"}
        assert requests[0][1]["urgent"]["type"] == "noul"
        assert requests[0][1]["quality"]["criteria"] == ["Incomplete", "Complete"]
        decision = result["decision"]
        assert decision["answers"]["urgent"] == {"value": True, "probability": 0.8}
        assert decision["answers"]["quality"]["probabilities"] == {"low": 0.25, "high": 0.75}
        assert decision["answers"]["quality"]["legend"] == {"0": "low", "1": "high"}
        assert decision["selected_branch"] == expected
        assert decision["routed_branch"] == ("" if expected == "review" else expected)
        assert result["result"] == expected
        exported = studio.configuration.export_interop({"document": native}, None)
        assert exported["valid"], exported
        assert exported["bundle"]["bindings"]["decisions"] == {"triage": BINDING}
        assert (
            studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)["document"]
            == native
        )
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


@pytest.mark.parametrize(
    "confidence,skip_review,expected",
    [(0.2, False, ["review", "technical"]), (0.9, False, ["billing"]), (0.2, True, [])],
)
def test_reroute_waits_for_review_or_skips_every_branch(
    tmp_path, confidence, skip_review, expected
):
    calls = []

    async def record(label):
        calls.append(label)
        return {"team": "technical"} if label == "review" else label

    tool_registry.register("interop:decision-record", record, "Synthetic reroute")
    studio = StudioService(
        tmp_path,
        workbench_options={"decision_client": lambda *a, **k: answers(confidence=confidence)},
    )
    try:
        workflow = source("reroute")
        if skip_review:
            workflow["nodes"][-1]["when"] = "false"
        install(studio, workflow)
        result = json.loads(run(studio, "decide", {"ticket": "Synthetic ticket"})["output"])
        assert calls == expected
        assert result["decision"]["routed_branch"] == (
            "technical" if expected == ["review", "technical"] else "billing" if expected else ""
        )
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


@pytest.mark.parametrize(
    "category,expected",
    [
        ("timeout", "completed"),
        ("rate_limited", "completed"),
        ("unreachable", "completed"),
        ("invalid_response", "completed"),
        ("input_too_large", "completed"),
        ("refused", "completed"),
        ("unauthorized", "failed"),
        ("config", "failed"),
    ],
)
def test_only_explicitly_allowed_provider_failures_escalate(tmp_path, category, expected):
    calls = []

    def fail(*args, **kwargs):
        raise DecisionCallError(category)

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:decision-record", record, "Synthetic error fallback")
    studio = StudioService(tmp_path, workbench_options={"decision_client": fail})
    try:
        workflow = source()
        workflow["nodes"][0]["on_provider_error"] = "escalate"
        install(studio, workflow)
        result = run(studio, "decide", {"ticket": "Synthetic ticket"}, expected)
        if expected == "completed":
            decision = json.loads(result["output"])["decision"]
            assert decision["answers"] == {} and decision["provider_error"] == category
            assert decision["escalation_reason"] == "provider_error"
            assert calls == ["review"]
        else:
            assert calls == []
            assert category in result["error"]
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"team": {}},
        {**answers(), "extra": {}},
        {**answers(), "urgent": {"type": "noul", "noul": True}},
        {**answers(), "urgent": {"type": "noul", "noul": float("nan")}},
        {
            **answers(),
            "team": {"type": "choice", "choice": "outside", "confidence": 0.9, "probabilities": {}},
        },
        {
            **answers(),
            "quality": {
                "type": "score",
                "score": 7,
                "confidence": 0.9,
                "probabilities": {"0": 0.2, "1": 0.8},
                "legend": {"0": "Incomplete", "1": "Complete"},
            },
        },
    ],
)
def test_malformed_answers_are_unresolved_errors_not_branch_defaults(bad):
    with pytest.raises(DecisionCallError, match="invalid_response"):
        answers_for(QUESTIONS, bad)


@pytest.mark.parametrize(
    "change",
    [
        lambda w: w["decision"].update(api_key="DO_NOT_ECHO_SECRET"),
        lambda w: w["nodes"][0].update(images=["photo.png"]),
        lambda w: w["nodes"][0].update(retry_strategy={"limit": 0}),
        lambda w: w["nodes"][0].update(routes={"billing": "missing"}),
        lambda w: w["nodes"][0].update(route_on="urgent"),
        lambda w: w["nodes"][0].update(min_confidence=0),
        lambda w: w["nodes"][0].update(min_confidence=True),
        lambda w: w["nodes"][0].update(escalation_mode="unsupported"),
        lambda w: w["nodes"][0].update(escalation_mode="reroute", escalate="billing"),
        lambda w: w["nodes"][0]["questions"]["quality"].update(
            criteria={"low": "Low", "high": "High"}
        ),
    ],
)
def test_bad_sources_reject_whole_document_before_effect(tmp_path, change):
    studio = StudioService(tmp_path)
    try:
        workflow = source()
        change(workflow)
        report = studio.configuration.convert_interop(
            {
                "documents": [workflow],
                "bindings": {
                    "decisions": {"triage": BINDING},
                    "tools": {"record": "text:wordcount"},
                },
            },
            None,
        )
        assert not report["valid"] and report["document"] is None
        assert "DO_NOT_ECHO_SECRET" not in json.dumps(report)
        assert not studio.records("runs") and not studio.records("configuration-plans")
    finally:
        studio.close()


@pytest.mark.parametrize(
    "error", [DecisionCallError("invalid_response"), DecisionCallError("unauthorized")]
)
def test_no_error_fallback_without_explicit_setting(tmp_path, error):
    calls = []

    def fail(*a, **k):
        raise error

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:decision-record", record, "Synthetic no implicit fallback")
    studio = StudioService(tmp_path, workbench_options={"decision_client": fail})
    try:
        install(studio, source())
        run(studio, "decide", {"ticket": "local"}, "failed")
        assert calls == []
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


def test_agent_reroute_schema_and_safe_generation_retry(tmp_path):
    calls, model_calls = [], []

    async def record(label):
        calls.append(label)
        return label

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            model_calls.append(context.prompt)
            return AgentResult(
                success=True,
                output='{"team":"outside"}' if len(model_calls) == 1 else '{"team":"technical"}',
            )

    tool_registry.register("interop:decision-record", record, "Synthetic reviewed route")
    studio = StudioService(
        tmp_path,
        provider_factory=lambda _: Provider(),
        workbench_options={"decision_client": lambda *a, **k: answers(confidence=0.1)},
    )
    try:
        workflow = source("reroute")
        workflow["nodes"][-1] = {
            "id": "review",
            "type": "agent",
            "agent_name": "reviewer",
            "depends_on": ["triage"],
            "instruction": "Return team as one declared option.",
            "retry_strategy": {
                "limit": 1,
                "retry_policy": "OnError",
                "backoff": {"duration": "1ms"},
            },
            "output_schema_override": {
                "type": "object",
                "properties": {
                    "team": {"type": "string", "enum": list(QUESTIONS["team"]["criteria"])}
                },
                "required": ["team"],
            },
        }
        request = {
            "documents": [
                {"agent_name": "reviewer", "instruction": "Review routing only."},
                workflow,
            ],
            "bindings": {
                "agents": {"reviewer": {"provider": "openai"}},
                "decisions": {"triage": BINDING},
                "tools": {"record": "interop:decision-record"},
            },
        }
        report = studio.configuration.convert_interop(request, None)
        assert report["valid"], report
        plan = studio.configuration.plan(report["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        result = json.loads(run(studio, "decide", {"ticket": "local"})["output"])
        assert calls == ["technical"] and len(model_calls) == 2
        assert "Return team" in model_calls[0]
        assert result["decision"]["selected_branch"] == "review"
        assert result["decision"]["routed_branch"] == "technical"
        workflow["nodes"][-1].pop("output_schema_override")
        bad = studio.configuration.convert_interop(request, None)
        assert not bad["valid"] and bad["document"] is None
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


def test_reroute_approval_restart_pins_decision_and_child(tmp_path):
    from tests.test_studio_workflow_v2 import wait

    effects, decisions = [], []

    async def record(label):
        effects.append(label)
        return {"team": "technical"} if label == "review" else label

    def classify(*a, **k):
        decisions.append(True)
        return answers(confidence=0.1)

    tool_registry.register("interop:decision-record", record, "Synthetic reroute recovery")
    studio = StudioService(tmp_path, workbench_options={"decision_client": classify})
    try:
        studio.save_definition(
            "workflows",
            {
                "name": "Review child",
                "graph": {
                    "version": 2,
                    "nodes": [
                        {"id": "gate", "kind": "approval"},
                        {
                            "id": "result",
                            "kind": "tool",
                            "tool_id": "interop:decision-record",
                            "kwargs": {"label": "review"},
                            "depends_on": ["gate"],
                        },
                    ],
                    "output_mapping": {"ref": "nodes.result"},
                },
            },
        )
        workflow = source("reroute")
        workflow["nodes"][-1] = {
            "id": "review",
            "type": "workflow",
            "workflow_name": "Review child",
            "depends_on": ["triage"],
        }
        install(studio, workflow)
        paused = run(studio, "decide", {"ticket": "local"}, "waiting")
        assert len(decisions) == 1 and effects == []
        studio.close()
        (tmp_path / "workflows" / "review-child.yaml").unlink()
        studio = StudioService(
            tmp_path,
            workbench_options={
                "decision_client": lambda *a, **k: (_ for _ in ()).throw(
                    AssertionError("Decision repeated")
                )
            },
        )
        result = wait(studio, studio.graph.resume(paused["id"], "approve"), "completed")
        assert effects == ["review", "technical"] and len(decisions) == 1
        assert json.loads(result["output"])["decision"]["routed_branch"] == "technical"
        assert studio.records("decisions")[0]["result"]["routed_branch"] == "technical"
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


def test_unselected_target_with_other_ready_dependency_stays_skipped(tmp_path):
    calls = []

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:decision-record", record, "Synthetic decision gate")
    studio = StudioService(
        tmp_path, workbench_options={"decision_client": lambda *a, **k: answers()}
    )
    try:
        workflow = source()
        workflow["nodes"].append(
            {"id": "ready", "type": "tool", "tool_name": "record", "input": {"label": "ready"}}
        )
        workflow["nodes"][2]["depends_on"].append("ready")
        install(studio, workflow)
        run(studio, "decide", {"ticket": "local"})
        assert sorted(calls) == ["billing", "ready"]
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


@pytest.mark.parametrize(
    "binding",
    [
        {"backend": "opencode"},
        {"backend": "unknown", "model": "selected"},
        {"backend": "openrouter", "model": "${SECRET}"},
        {"backend": "openrouter", "model": "selected", "api_key": "DO_NOT_ECHO_SECRET"},
    ],
)
def test_decision_binding_is_explicit_and_never_contains_credentials(tmp_path, binding):
    studio = StudioService(tmp_path)
    try:
        report = studio.configuration.convert_interop(
            {
                "documents": [source()],
                "bindings": {
                    "decisions": {"triage": binding},
                    "tools": {"record": "text:wordcount"},
                },
            },
            None,
        )
        assert not report["valid"] and report["document"] is None
        assert "DO_NOT_ECHO_SECRET" not in json.dumps(report)
    finally:
        studio.close()


@pytest.mark.parametrize(
    "change",
    [
        lambda q: q.update(
            {f"q{i}": {"type": "boolean", "instructions": "Check"} for i in range(33)}
        ),
        lambda q: q["quality"].update(criteria=[{"low": "Low"}, {"low": "Same name"}]),
        lambda q: q["urgent"].update(criteria={"true": "True only"}),
        lambda q: q["team"].update(criteria={"only": "One option"}),
        lambda q: q["urgent"].update(instructions="x" * 4001),
    ],
)
def test_question_bounds_and_ordered_level_contract(change):
    questions = copy.deepcopy(QUESTIONS)
    change(questions)
    with pytest.raises(ValueError):
        validate_questions(questions)


def test_boolean_criteria_stay_explicit_and_score_order_is_preserved():
    questions = copy.deepcopy(QUESTIONS)
    questions["urgent"]["criteria"] = {"true": "Needs review", "false": "Routine"}
    validate_questions(questions)
    wire = wire_questions(questions)
    assert (
        wire["urgent"]["type"] == "noul"
        and wire["urgent"]["criteria"] == questions["urgent"]["criteria"]
    )
    assert wire["quality"]["criteria"] == ["Incomplete", "Complete"]


def test_cancellation_does_not_publish_late_answer_or_run_a_branch(tmp_path):
    import time

    from tests.test_studio_workflow_v2 import wait

    started, calls = [], []

    async def classify(*a, **k):
        started.append(True)
        await asyncio.sleep(1.5)
        return answers()

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:decision-record", record, "Synthetic cancellation guard")
    studio = StudioService(tmp_path, workbench_options={"decision_client": classify})
    try:
        install(studio, source())
        task = studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": "decide",
                "task": "Cancel decision",
                "workflow_input": {"ticket": "local"},
            }
        )
        deadline = time.monotonic() + 3
        while not started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert started
        wait(studio, studio.cancel_run(task["id"]), "cancelled")
        assert calls == [] and not studio.records("decisions")
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


def test_routing_edges_cannot_be_removed_in_native_configuration(tmp_path):
    async def record(label):
        return label

    tool_registry.register("interop:decision-record", record, "Synthetic native edge contract")
    studio = StudioService(tmp_path)
    try:
        native = install(studio, source())
        graph = native["definitions"][0]["document"]["graph"]
        graph["edges"] = [e for e in graph["edges"] if not (e.get("choice") == "technical")]
        with pytest.raises(ValueError, match="conditional choice edges"):
            studio.graph.validate(graph)
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)


def test_acknowledged_decision_survives_abrupt_exit_before_node_completion(tmp_path):
    import subprocess
    import sys

    workflow = source()
    for node in workflow["nodes"][1:]:
        node["input"] = {"text": node["id"]}
    studio = StudioService(tmp_path)
    report = studio.configuration.convert_interop(
        {
            "documents": [workflow],
            "bindings": {"decisions": {"triage": BINDING}, "tools": {"record": "text:wordcount"}},
        },
        None,
    )
    assert report["valid"], report
    plan = studio.configuration.plan(report["document"], None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    studio.close()
    program = r"""
import os,sys,time
from pathlib import Path
from nexus.core.studio import StudioService
from tests.test_studio_interop_decisions import answers
root=Path(sys.argv[1])
s=StudioService(root,workbench_options={"decision_client":lambda *a,**k:answers()})
original=s._write
def write(path,record):
 original(path,record)
 if path.parent.name=="decisions" and "result" in record:os._exit(74)
s._write=write
s.start_run({"kind":"workflow","workflow_id":"decide","task":"Synthetic interrupted decision","workflow_input":{"ticket":"local"}})
time.sleep(8)
"""
    process = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path)], timeout=12, capture_output=True, text=True
    )
    assert process.returncode == 74, process.stderr

    def forbidden(*a, **k):
        raise AssertionError("Acknowledged decision repeated")

    studio = StudioService(tmp_path, workbench_options={"decision_client": forbidden})
    try:
        record = studio.records("graph-runs")[0]
        output = json.loads(
            asyncio.run(studio.graph.structured.execute(record["id"], record["payload"]))
        )
        assert output["decision"]["selected_branch"] == "billing" and output["result"] == 1
        assert len(studio.records("decisions")) == 1
        saved = studio.records("decisions")[0]
        saved["owner_id"] = "different-owner"
        studio._write(studio._path("decisions", saved["id"]), saved)
        # The completed graph normally reuses its node checkpoint; the service itself also rejects an owner mismatch.
        from nexus.core.studio_decisions import StudioTypedDecisions

        config = record["graph"]["nodes"][0]["decision"]
        with pytest.raises(PermissionError):
            asyncio.run(
                StudioTypedDecisions(studio.workbench.jobs).execute(
                    config, {"ticket": "local"}, record["payload"], record["id"], saved["id"]
                )
            )
    finally:
        studio.close()


def test_shared_decision_credentials_owner_and_project_scope(team, monkeypatch):
    from nexus.adapters.decisions import jev
    from nexus.core.studio_decisions import FORMAT, StudioTypedDecisions

    studio, policy, manager, _, _ = team
    policy.decision_keys = {
        "alice": {
            "backend": "openrouter",
            "key_env": "ALICE_TYPED_DECISION_KEY",
            "model": "personal",
        }
    }
    monkeypatch.setenv("ALICE_TYPED_DECISION_KEY", "alice-synthetic-only")
    monkeypatch.setenv("OPENROUTER_API_KEY", "host-synthetic-must-not-be-used")
    studio.workbench.jobs.decision_client = None
    calls = []

    def client(*a, **kwargs):
        calls.append(kwargs)
        return answers()

    monkeypatch.setattr(jev, "ask_strict", client)
    for identity in ["alice", "bob"]:
        studio._write(
            studio._path("runs", "typed-" + identity),
            {
                "id": "typed-" + identity,
                "requester_nexus_id": identity,
                "status": "running",
                "kind": "workflow",
                "provider": "decision-openrouter",
                "created_at": "2026-10-19T00:00:00+00:00",
            },
        )
    config = {"format": FORMAT, **BINDING, "questions": QUESTIONS}
    service = StudioTypedDecisions(studio.workbench.jobs)
    output = asyncio.run(
        service.execute(
            config,
            {"ticket": "local"},
            {"requester_nexus_id": "alice"},
            "typed-alice",
            "typed-result",
        )
    )
    assert output["answers"]["team"]["choice"] == "billing"
    assert calls[0]["api_key"] == "alice-synthetic-only"
    with pytest.raises(PermissionError):
        asyncio.run(
            service.execute(
                config,
                {"ticket": "local"},
                {"requester_nexus_id": "bob"},
                "typed-alice",
                "other-result",
            )
        )
    with pytest.raises(DecisionCallError, match="config"):
        asyncio.run(
            service.execute(
                config,
                {"ticket": "local"},
                {"requester_nexus_id": "bob"},
                "typed-bob",
                "bob-result",
            )
        )
    assert len(calls) == 1 and studio.records("decisions")[0]["owner_id"] == "alice"

    manager.revoked.add("alice")
    with pytest.raises(PermissionError):
        asyncio.run(
            service.execute(
                config,
                {"ticket": "local"},
                {"requester_nexus_id": "alice"},
                "typed-alice",
                "typed-result",
            )
        )
    assert len(calls) == 1

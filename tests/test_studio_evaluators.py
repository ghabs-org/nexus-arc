"""Reusable judge definitions, fixed choice scores and copied snapshot isolation."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio_evaluations import additional_checks, validate_case
from nexus.core.studio_evaluators import validate_evaluator
from tests.test_studio_resources import app as app
from tests.test_studio_resources import done
from tests.test_studio_team import team as team

CONFIG = {
    "provider": "codex",
    "rubric": "Judge only the supplied reference evidence",
    "threshold": 0.5,
    "choices": {"Supported": 1, "Partial": 0.5, "Unsupported": 0},
}


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "arbitrary_code"},
        {"kind": "experiment"},
        {"version": 2},
        {"version": True},
        {"unknown": "discard me"},
        {"provider": "missing"},
        {"threshold": True},
        {"threshold": float("nan")},
        {"choices": {}},
        {"choices": {"Only": 1}},
        {"choices": {"Pass": True, "Fail": 0}},
        {"choices": {"Pass": float("inf"), "Fail": 0}},
        {"choices": {" Pass": 1, "Fail": 0}},
        {"choices": {"Pass\nNow": 1, "Fail": 0}},
        {"choices": {"Pass": "1", "Fail": 0}},
    ],
)
def test_invalid_evaluator_definitions_fail_without_execution(changes):
    with pytest.raises(ValueError):
        validate_evaluator({**CONFIG, **changes})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "label,expected,passed",
    [("Supported", 1, True), ("Partial", 0.5, True), ("Unsupported", 0, False)],
)
async def test_choice_score_comes_only_from_saved_map_and_preserves_provider_identity(
    label, expected, passed
):
    judge = SimpleNamespace(
        _invoke=AsyncMock(
            return_value=AgentResult(
                success=True, output=json.dumps({"choice": label, "reason": "Reference evidence"})
            )
        )
    )
    case = validate_case({"input": "Review", "contexts": ["Untrusted reference"], "judge": CONFIG})
    check = (await additional_checks(judge, case, "Untrusted answer", "run", "alice"))[0]
    assert check["type"] == "choice_judge" and check["choice"] == label
    assert check["score"] == expected and check["passed"] is passed
    payload, prompt, run = judge._invoke.call_args.args
    assert payload["provider"] == "codex" and payload["requester_nexus_id"] == "alice"
    assert (
        '"Partial": 0.5' in prompt
        and "Untrusted reference" in prompt
        and "Never return a numeric score" in prompt
    )
    assert run == "run" and judge._invoke.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        {"choice": "Unknown", "reason": "x"},
        {"choice": "Supported"},
        {"score": 1, "reason": "x"},
        {"choice": "Partial", "score": 1, "reason": "x"},
        {"choice": True, "reason": "x"},
        {"choice": "Supported", "reason": ""},
        {"choice": "supported", "reason": "x"},
        [],
        None,
    ],
)
async def test_invalid_choice_answers_fail_without_numeric_fallback_or_retry(answer):
    judge = SimpleNamespace(
        _invoke=AsyncMock(return_value=AgentResult(success=True, output=json.dumps(answer)))
    )
    case = validate_case({"input": "Review", "judge": CONFIG})
    check = (await additional_checks(judge, case, "Answer", "run", None))[0]
    assert check["passed"] is False and "valid choice" in check["error"]
    assert "score" not in check and judge._invoke.await_count == 1


@pytest.mark.asyncio
async def test_legacy_numeric_judge_and_provider_failure_keep_existing_behavior():
    case = validate_case({"input": "Review", "judge": {"provider": "codex", "rubric": "Evidence"}})
    judge = SimpleNamespace(
        _invoke=AsyncMock(
            return_value=AgentResult(success=True, output='{"score":0.8,"reason":"Grounded"}')
        )
    )
    check = (await additional_checks(judge, case, "Answer", "run", None))[0]
    assert check["type"] == "rubric_judge" and check["score"] == 0.8 and check["passed"]
    judge._invoke.side_effect = RuntimeError("Synthetic provider unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        await additional_checks(
            judge, validate_case({"input": "Review", "judge": CONFIG}), "Answer", "run", None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw", ["not JSON", '{"choice":"Unsupported","choice":"Supported","reason":"ambiguous"}']
)
async def test_non_json_or_duplicate_choice_fields_fail(raw):
    judge = SimpleNamespace(_invoke=AsyncMock(return_value=AgentResult(success=True, output=raw)))
    check = (
        await additional_checks(
            judge, validate_case({"input": "Review", "judge": CONFIG}), "Answer", "run", None
        )
    )[0]
    assert not check["passed"] and "score" not in check


def test_history_configuration_and_queued_evaluator_copy_remain_independent(app, monkeypatch):
    studio, _ = app
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(
                success=True,
                output='{"choice":"Partial","reason":"Some evidence"}'
                if "Configured choice scores (JSON)" in context.prompt
                else "Evidence answer",
            )

    studio.provider_factory = lambda _: Provider()
    evaluator = studio.catalog.save("evaluators", {"name": "Evidence judge", "data": CONFIG})
    judge = {k: v for k, v in evaluator["data"].items() if k not in {"kind", "type", "version"}}
    suite = studio.catalog.save(
        "evaluations",
        {
            "name": "Choice regression",
            "data": {
                "target_kind": "agent",
                "target_id": "reviewer",
                "cases": [{"input": "Review", "judge": judge}],
            },
        },
    )
    queued, submit = [], studio.executor.submit
    monkeypatch.setattr(studio.executor, "submit", lambda *args: queued.append(args))
    run = studio.start_run({"kind": "evaluation", "evaluation_id": suite["id"]})
    changed = copy.deepcopy(evaluator["data"])
    changed["choices"]["Partial"] = 0
    studio.catalog.save("evaluators", {**evaluator, "data": changed})
    studio.catalog.archive("evaluators", evaluator["id"], {"revision": 2})
    monkeypatch.setattr(studio.executor, "submit", submit)
    submit(*queued[0])
    assert done(studio, run)["status"] == "completed"
    report = studio._read(studio._path("evaluation-reports", run["id"]))
    assert report["passed"] == report["total"] == 1 and len(calls) == 2
    assert report["results"][0]["checks"][0]["score"] == 0.5
    assert report["results"][0]["checks"][0]["choice"] == "Partial"
    assert studio.catalog.history("evaluators", evaluator["id"])[-1]["revision"] == 1
    document = {
        "format": "nexus-studio-config/v1",
        "definitions": [],
        "resources": [
            {"kind": "evaluators", "id": "imported", "name": "Imported judge", "data": CONFIG}
        ],
    }
    plan = studio.configuration.plan(document, None)
    studio.configuration.apply(plan["id"], plan["digest"], None)
    assert studio.export_definitions()["catalog"]["evaluators"]
    assert studio.catalog.get("evaluators", "imported")["data"] == validate_evaluator(CONFIG)


def test_http_evaluator_permissions_visibility_and_revision_conflicts(team):
    studio, _, manager, request, calls = team
    path = "/api/catalog/evaluators"
    assert request(path)[0] == 401
    for user in ("bob", "viewer"):
        assert request(path, {"name": "Denied", "data": CONFIG}, user=user)[0] == 403
    code, saved = request(path, {"name": "Evidence", "data": CONFIG}, user="alice")
    assert code == 201 and saved["data"]["type"] == "llm_judge"
    assert request(path, {**saved, "revision": 0}, user="alice")[0] == 400
    assert request(path, {**saved}, user="alice", csrf="bad")[0] == 403
    for user in ("bob", "viewer", "owner"):
        assert request(path, user=user)[1]["items"][0]["id"] == saved["id"]
    manager.revoked.add("alice")
    assert request(path, user="alice")[0] == 401
    assert calls == [] and studio.records("runs") == []

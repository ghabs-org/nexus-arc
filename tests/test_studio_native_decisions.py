"""Pure native question/answer and credential contract tests; HTTP evidence is separate."""

import copy

import pytest

from nexus.adapters.decisions.errors import DecisionCallError
from nexus.adapters.decisions.openai import answers_from, questions_for
from nexus.core.studio import StudioService
from nexus.core.studio_decisions import answers_for, validate_binding, wire_questions
from tests.test_studio_interop_decisions import QUESTIONS


def native_answers():
    return {
        "answers": [
            {
                "name": "team",
                "type": "choice",
                "choice": "billing",
                "confidence": 0.9,
                "probabilities": [
                    {"value": key, "probability": 1 if key == "billing" else 0}
                    for key in QUESTIONS["team"]["criteria"]
                ],
            },
            {"name": "urgent", "type": "predicate", "probability": 0.8},
            {
                "name": "quality",
                "type": "score",
                "score": 0.75,
                "confidence": 0.7,
                "probabilities": [
                    {"value": 0, "label": "0", "probability": 0.25},
                    {"value": 1, "label": "1", "probability": 0.75},
                ],
            },
        ]
    }


def test_native_wire_questions_preserve_choices_boolean_and_ordered_score():
    wire = questions_for(wire_questions(QUESTIONS))
    assert [item["type"] for item in wire] == ["choice", "predicate", "score"]
    assert wire[0]["options"] == [
        {"value": k, "description": v} for k, v in QUESTIONS["team"]["criteria"].items()
    ]
    assert wire[2]["levels"] == [
        {"label": "0", "description": "Incomplete"},
        {"label": "1", "description": "Complete"},
    ]
    result = answers_for(QUESTIONS, answers_from(native_answers(), wire_questions(QUESTIONS)))
    assert result["urgent"] == {"value": True, "probability": 0.8}
    assert result["quality"]["probabilities"] == {"low": 0.25, "high": 0.75}


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate",
        "missing",
        "unknown",
        "score-label",
        "bool-index",
        "invalid-probability",
        "refusal",
    ],
)
def test_native_contract_rejects_incomplete_or_untrustworthy_answers(mutation):
    body = native_answers()
    if mutation == "duplicate":
        body["answers"][2] = copy.deepcopy(body["answers"][0])
    elif mutation == "missing":
        body["answers"].pop()
    elif mutation == "unknown":
        body["answers"][0]["name"] = "unknown"
    elif mutation == "score-label":
        body["answers"][2]["probabilities"][0]["label"] = "invented"
    elif mutation == "bool-index":
        body["answers"][2]["probabilities"][0]["value"] = False
    elif mutation == "invalid-probability":
        body["answers"][1]["probability"] = 1.2
    else:
        body["answers"][0] = {"name": "team", "type": "refusal", "reason": "untrusted detail"}
    with pytest.raises(DecisionCallError):
        answers_for(QUESTIONS, answers_from(body, wire_questions(QUESTIONS)))


def test_native_model_is_explicit_and_never_borrows_other_backend_credentials(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENCODE_API_KEY", "must-not-be-borrowed")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-borrowed")
    with pytest.raises(ValueError, match="gpt-6-luna"):
        validate_binding({"backend": "openai", "model": "codex-subscription"})
    studio = StudioService(tmp_path)
    try:
        with pytest.raises(ValueError, match="API key"):
            studio.workbench.jobs.decision_credentials({"backend": "openai", "model": "gpt-6-luna"})
        monkeypatch.setenv("OPENAI_API_KEY", "synthetic-native-only")
        credentials = studio.workbench.jobs.decision_credentials(
            {"backend": "openai", "model": "gpt-6-luna"}
        )
        assert credentials == {
            "api_key": "synthetic-native-only",
            "model": "gpt-6-luna",
            "endpoint": "https://api.openai.com/v1/decisions",
        }
    finally:
        studio.close()

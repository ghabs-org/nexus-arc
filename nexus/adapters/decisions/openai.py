"""Native OpenAI Decisions API mapped to Nexus' shared typed-decision answers."""

import json
import math
import time

from nexus.adapters.decisions.errors import DecisionAnswers, DecisionCallError
from nexus.adapters.decisions.http import metadata, request_json

DECISIONS_URL = "https://api.openai.com/v1/decisions"
DEFAULT_MODEL = "gpt-6-luna"


def questions_for(questions):
    result = []
    for name, item in questions.items():
        kind = item["type"]
        converted = {"name": name, "instructions": item["instructions"]}
        if kind == "choice":
            converted.update(
                type="choice",
                options=[
                    {"value": key, "description": value} for key, value in item["criteria"].items()
                ],
            )
        elif kind == "noul":
            converted["type"] = "predicate"
            if item.get("criteria"):
                converted["instructions"] += "\nBoolean criteria: " + json.dumps(item["criteria"])
        elif kind == "score":
            converted.update(
                type="score",
                levels=[
                    {"label": str(index), "description": value}
                    for index, value in enumerate(item["criteria"])
                ],
            )
        else:
            raise DecisionCallError("config")
        result.append(converted)
    return result


def answers_from(body, questions):
    answers = body.get("answers")
    if not isinstance(answers, list) or len(answers) != len(questions):
        raise DecisionCallError("invalid_response")
    result = DecisionAnswers({})
    for answer in answers:
        if not isinstance(answer, dict):
            raise DecisionCallError("invalid_response")
        name = answer.get("name")
        if not isinstance(name, str) or name not in questions or name in result:
            raise DecisionCallError("invalid_response")
        question = questions[name]
        kind = answer.get("type")
        if kind == "refusal":
            raise DecisionCallError("refused")
        if question["type"] == "noul" and kind == "predicate":
            result[name] = {"type": "noul", "noul": answer.get("probability")}
            continue
        if kind != question["type"] or kind not in {"choice", "score"}:
            raise DecisionCallError("invalid_response")
        distribution = answer.get("probabilities")
        if not isinstance(distribution, list):
            raise DecisionCallError("invalid_response")
        probabilities = {}
        for entry in distribution:
            if not isinstance(entry, dict):
                raise DecisionCallError("invalid_response")
            value = entry.get("value")
            if kind == "score":
                if type(value) is not int or not 0 <= value < len(question["criteria"]):
                    raise DecisionCallError("invalid_response")
                if entry.get("label") != str(value):
                    raise DecisionCallError("invalid_response")
                value = str(value)
            elif not isinstance(value, str) or value not in question["criteria"]:
                raise DecisionCallError("invalid_response")
            if value in probabilities:
                raise DecisionCallError("invalid_response")
            probabilities[value] = entry.get("probability")
        result[name] = {
            "type": kind,
            "confidence": answer.get("confidence"),
            "probabilities": probabilities,
            kind: answer.get(kind),
        }
        if kind == "score":
            result[name]["legend"] = {str(i): value for i, value in enumerate(question["criteria"])}
    return metadata(result, body)


def ask_strict(state, questions, model=None, api_key=None, timeout=20, endpoint=None):
    """One configured account and model, with a bounded total transient retry budget."""
    model = model or DEFAULT_MODEL
    if (
        model != DEFAULT_MODEL
        or not isinstance(api_key, str)
        or not api_key
        or type(timeout) not in {int, float}
        or not math.isfinite(timeout)
        or not 0 < timeout <= 300
    ):
        raise DecisionCallError("config")
    try:
        payload = {
            "model": model,
            "input": state
            if isinstance(state, str)
            else json.dumps(state, ensure_ascii=False, allow_nan=False),
            "questions": questions_for(questions),
        }
    except (ValueError, KeyError, TypeError):
        raise DecisionCallError("config") from None
    deadline = time.monotonic() + timeout
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DecisionCallError("timeout")
        try:
            return answers_from(
                request_json(endpoint or DECISIONS_URL, api_key, payload, remaining), questions
            )
        except DecisionCallError as error:
            delay = 0.1 * 2**attempt
            if (
                error.category not in {"rate_limited", "unreachable"}
                or attempt == 2
                or deadline - time.monotonic() <= delay
            ):
                raise
            time.sleep(delay)

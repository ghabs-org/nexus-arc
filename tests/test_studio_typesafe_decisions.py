"""Direct TypeSafe Decision API (System One) adapter and contract tests."""

import copy
import json
from unittest.mock import MagicMock

import pytest

from nexus.adapters.decisions.errors import DecisionAnswers, DecisionCallError
from nexus.adapters.decisions.typesafe import (
    DEFAULT_MODEL,
    SYSTEMONE_URL,
    ask,
    ask_strict,
    ask_strict_once,
)
from nexus.core.studio import StudioService
from nexus.core.studio_decisions import (
    StudioTypedDecisions,
    answers_for,
    validate_binding,
    validate_decision,
    wire_questions,
)
from nexus.core.studio_team import NexusTeamPolicy
from tests.test_studio_interop_decisions import QUESTIONS


def typesafe_answers():
    return {
        "model": "jev-1.13",
        "answers": {
            "team": {
                "type": "choice",
                "choice": "billing",
                "confidence": 0.92,
                "probabilities": {
                    "billing": 0.92,
                    "technical": 0.05,
                    "other": 0.03,
                },
            },
            "urgent": {
                "type": "noul",
                "noul": 0.84,
            },
            "quality": {
                "type": "score",
                "score": 0.75,
                "confidence": 0.88,
                "probabilities": {
                    "0": 0.25,
                    "1": 0.75,
                },
                "legend": {
                    "0": "Incomplete",
                    "1": "Complete",
                },
            },
        },
        "usage": {
            "input_tokens": 85,
            "output_tokens": 14,
        },
    }


def test_typesafe_binding_validation():
    assert validate_binding({"backend": "typesafe", "model": "jev-1.13"}) == {
        "backend": "typesafe",
        "model": "jev-1.13",
    }
    assert validate_binding({"backend": "typesafe", "model": "jev-latest"}) == {
        "backend": "typesafe",
        "model": "jev-latest",
    }
    with pytest.raises(ValueError, match="Decision alias requires an explicit existing backend"):
        validate_binding({"backend": "typesafe", "model": "invalid model with spaces"})
    with pytest.raises(ValueError, match="Decision alias requires an explicit existing backend"):
        validate_binding({"backend": "unknown-backend", "model": "jev-1.13"})


def test_typesafe_decision_credentials_isolation(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-borrow-openai")
    monkeypatch.setenv("OPENROUTER_API_KEY", "do-not-borrow-openrouter")
    monkeypatch.setenv("OPENCODE_API_KEY", "do-not-borrow-opencode")

    studio = StudioService(tmp_path)
    try:
        with pytest.raises(ValueError, match="Selected decision service needs its configured API key"):
            studio.workbench.jobs.decision_credentials({"backend": "typesafe", "model": "jev-1.13"})

        monkeypatch.setenv("TYPESAFE_API_KEY", "sk-typesafe-synthetic-test")
        creds = studio.workbench.jobs.decision_credentials({"backend": "typesafe", "model": "jev-1.13"})
        assert creds == {
            "api_key": "sk-typesafe-synthetic-test",
            "model": "jev-1.13",
            "endpoint": SYSTEMONE_URL,
        }

        # Default model when omitted
        creds_default = studio.workbench.jobs.decision_credentials({"backend": "typesafe"})
        assert creds_default["model"] == DEFAULT_MODEL
        assert creds_default["endpoint"] == SYSTEMONE_URL
    finally:
        studio.close()


def test_typesafe_ask_strict_once_success(monkeypatch):
    calls = []

    def fake_post(url, json_payload=None, headers=None, timeout=None, stream=False, allow_redirects=False, **kwargs):
        calls.append({"url": url, "json": kwargs.get("json", json_payload), "headers": headers, "timeout": timeout})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content = MagicMock(return_value=[json.dumps(typesafe_answers()).encode()])
        mock_resp.close = MagicMock()
        return mock_resp

    monkeypatch.setattr("requests.post", fake_post)

    result = ask_strict_once(
        "customer has invoice question",
        wire_questions(QUESTIONS),
        model="jev-1.13",
        api_key="ts-key-123",
        timeout=15,
    )
    assert isinstance(result, DecisionAnswers)
    assert result.metadata["model"] == "jev-1.13"
    assert result.metadata["usage"] == {"input_tokens": 85, "output_tokens": 14}
    assert result["team"]["choice"] == "billing"
    assert result["team"]["confidence"] == 0.92
    assert result["urgent"]["noul"] == 0.84

    assert len(calls) == 1
    assert calls[0]["url"] == SYSTEMONE_URL
    assert calls[0]["headers"]["Authorization"] == "Bearer ts-key-123"
    assert calls[0]["json"]["model"] == "jev-1.13"
    assert calls[0]["json"]["state"] == "customer has invoice question"


def test_typesafe_rejects_image_urls():
    with pytest.raises(DecisionCallError) as exc_info:
        ask_strict_once(
            "state",
            wire_questions(QUESTIONS),
            api_key="key",
            image_urls=["data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAA"],
        )
    assert exc_info.value.category == "config"


def test_typesafe_retries_transient_failures(monkeypatch):
    attempts = 0

    def fake_post(url, **kwargs):
        nonlocal attempts
        attempts += 1
        mock_resp = MagicMock()
        if attempts < 3:
            mock_resp.status_code = 503
            mock_resp.iter_content = MagicMock(return_value=[b"Service Unavailable"])
        else:
            mock_resp.status_code = 200
            mock_resp.iter_content = MagicMock(return_value=[json.dumps(typesafe_answers()).encode()])
        mock_resp.close = MagicMock()
        return mock_resp

    monkeypatch.setattr("requests.post", fake_post)

    result = ask_strict(
        "state",
        wire_questions(QUESTIONS),
        model="jev-1.13",
        api_key="ts-key",
        timeout=10,
    )
    assert attempts == 3
    assert result["team"]["choice"] == "billing"


def test_typesafe_fails_fast_on_auth_error(monkeypatch):
    attempts = 0

    def fake_post(url, **kwargs):
        nonlocal attempts
        attempts += 1
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.iter_content = MagicMock(return_value=[b'{"error": {"code": "invalid_api_key"}}'])
        mock_resp.close = MagicMock()
        return mock_resp

    monkeypatch.setattr("requests.post", fake_post)

    with pytest.raises(DecisionCallError) as exc_info:
        ask_strict(
            "state",
            wire_questions(QUESTIONS),
            model="jev-1.13",
            api_key="bad-key",
            timeout=10,
        )
    assert attempts == 1
    assert exc_info.value.category == "unauthorized"


def test_typesafe_safe_ask_fallback(monkeypatch):
    # Missing key returns empty dict
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert ask("state", wire_questions(QUESTIONS)) == {}


@pytest.mark.asyncio
async def test_typesafe_studio_typed_decisions_workflow_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-typesafe-valid")

    studio = StudioService(tmp_path)
    try:
        config = {
            "format": "nexus-typed-decision/v1",
            "backend": "typesafe",
            "model": "jev-1.13",
            "questions": QUESTIONS,
            "route_on": "team",
            "routes": {"billing": "billing_handler", "technical": "tech_handler"},
            "default": "general_handler",
            "min_confidence": 0.8,
            "escalate": "review_handler",
            "escalation_mode": "handoff",
            "on_provider_error": "escalate",
        }
        validate_decision(config)

        # Mock the HTTP call
        def fake_post(url, **kwargs):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.iter_content = MagicMock(return_value=[json.dumps(typesafe_answers()).encode()])
            mock_resp.close = MagicMock()
            return mock_resp

        monkeypatch.setattr("requests.post", fake_post)

        run = studio.start_run({
            "kind": "decision",
            "task": "Payment issue",
            "instructions": "Route payment issue",
            "options": {"billing": "Billing", "tech": "Tech"},
            "default": "billing",
            "backend": "typesafe",
        })
        typed = StudioTypedDecisions(studio.workbench.jobs)

        result = await typed.execute(
            config,
            {"ticket": "Payment charged twice"},
            {},
            run["id"],
            "test-rec-1",
        )
        assert result["selected_branch"] == "billing_handler"
        assert result["escalated"] is False
        assert result["answers"]["team"]["choice"] == "billing"
        assert result["answers"]["urgent"]["value"] is True
    finally:
        studio.close()


def test_typesafe_catalog_decision_model_resource(tmp_path):
    studio = StudioService(tmp_path)
    try:
        dm = studio.catalog.save(
            "decisionmodels",
            {"name": "TypeSafe Fast Router", "data": {"backend": "typesafe", "model": "jev-1.13", "timeout_seconds": 15}},
        )
        assert dm["data"]["backend"] == "typesafe"
        assert dm["data"]["model"] == "jev-1.13"
        assert dm["data"]["timeout_seconds"] == 15

        retrieved = studio.catalog.get("decisionmodels", dm["id"])
        assert retrieved["name"] == "TypeSafe Fast Router"
        assert retrieved["data"]["backend"] == "typesafe"
    finally:
        studio.close()


def test_typesafe_team_decision_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_TYPESAFE_KEY", "sk-typesafe-user")
    policy = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login?next=/",
        bindings={"alice": "builder", "owner_user": "owner"},
        manager=MagicMock(),
        decision_keys={"alice": {"backend": "typesafe", "key_env": "MY_TYPESAFE_KEY", "model": "jev-1.13"}},
    )
    studio = StudioService(
        tmp_path / "project", team_policy=policy, state_dir=tmp_path / "private-state"
    )
    try:
        creds = studio.workbench.jobs.decision_credentials(
            {"backend": "typesafe", "requester_nexus_id": "alice"}
        )
        assert creds == {
            "api_key": "sk-typesafe-user",
            "model": "jev-1.13",
            "endpoint": SYSTEMONE_URL,
        }
    finally:
        studio.close()

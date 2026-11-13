"""Real adapter over the local Postman native-Decisions simulation; explicit prerequisite."""

import json
import os
from urllib.parse import urlsplit

import pytest

from nexus.core.studio import StudioService
from nexus.tools import tool_registry
from tests.test_studio_interop_decisions import source
from tests.test_studio_interop_workflows import run


@pytest.mark.parametrize(
    "mode,status,branch,category",
    [
        ("success", "completed", "billing", None),
        ("recover-once", "completed", "billing", None),
        ("low-confidence", "completed", "review", None),
        ("rate-limited", "completed", "review", "rate_limited"),
        ("unavailable", "completed", "review", "unreachable"),
        ("html-error", "completed", "review", "unreachable"),
        ("malformed", "completed", "review", "invalid_response"),
        ("duplicate", "completed", "review", "invalid_response"),
        ("wrong-score-label", "completed", "review", "invalid_response"),
        ("oversized", "completed", "review", "invalid_response"),
        ("refused", "completed", "review", "refused"),
        ("slow", "completed", "review", "timeout"),
        ("quota", "failed", None, "config"),
        ("unauthorized", "failed", None, "unauthorized"),
        ("redirect", "failed", None, "config"),
    ],
)
def test_native_decision_adapter_over_real_http(
    tmp_path, monkeypatch, mode, status, branch, category
):
    endpoint = os.environ.get("NEXUS_NATIVE_DECISION_SIM_URL", "")
    assert endpoint, (
        "Start postman/simulations/openai-decisions.sim.yaml and set NEXUS_NATIVE_DECISION_SIM_URL"
    )
    parts = urlsplit(endpoint)
    assert parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port
    calls = []

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:native-record", record, "Synthetic native decision branch")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-fixture-key")
    monkeypatch.setattr("nexus.adapters.decisions.openai.DECISIONS_URL", endpoint)
    studio = StudioService(tmp_path)
    try:
        workflow = source()
        workflow["decision"]["timeout_seconds"] = 1
        workflow["nodes"][0]["on_provider_error"] = "escalate"
        converted = studio.configuration.convert_interop(
            {
                "documents": [workflow],
                "bindings": {
                    "decisions": {"triage": {"backend": "openai", "model": "gpt-6-luna"}},
                    "tools": {"record": "interop:native-record"},
                },
            },
            None,
        )
        assert converted["valid"], converted
        review = studio.configuration.plan(converted["document"], None)
        studio.configuration.apply(review["id"], review["digest"], None)
        result = run(studio, "decide", {"ticket": mode}, status)
        assert calls == ([branch] if branch else [])
        assert "DO_NOT_LOG_NATIVE_DETAIL" not in json.dumps(result)
        if status == "completed":
            output = json.loads(result["output"])["decision"]
            assert output.get("provider_error") == category
            if category is None:
                assert output["answers"]["urgent"] == {"value": True, "probability": 0.8}
                assert output["answers"]["quality"]["score"] == 1
                assert (
                    studio.records("decisions")[0]["provider_metadata"]["usage"]["input_tokens"]
                    == 42
                )
        else:
            assert category in result["error"]
        exported = studio.configuration.export_interop({"document": converted["document"]}, None)
        assert exported["valid"], exported
    finally:
        studio.close()
        tool_registry.unregister("interop:native-record", func=record)

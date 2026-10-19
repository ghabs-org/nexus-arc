"""Explicit real HTTP acceptance; start postman/simulations/studio-decisions.sim.yaml first.

No skip on missing prerequisites: set NEXUS_DECISION_SIM_URL to the local decision
endpoint and invoke this file explicitly. No provider account or real API key.
"""

import json
import os
from functools import partial
from urllib.parse import urlsplit

import pytest

from nexus.adapters.decisions.jev import ask_strict
from nexus.core.studio import StudioService
from nexus.tools import tool_registry
from tests.test_studio_interop_decisions import source
from tests.test_studio_interop_workflows import run


@pytest.mark.parametrize(
    "mode,status,branch,category",
    [
        ("success", "completed", "billing", None),
        ("low-confidence", "completed", "review", None),
        ("recover-once", "completed", "billing", None),
        ("too-large", "completed", "review", "input_too_large"),
        ("rate-limited", "completed", "review", "rate_limited"),
        ("unavailable", "completed", "review", "unreachable"),
        ("html-error", "completed", "review", "unreachable"),
        ("malformed", "completed", "review", "invalid_response"),
        ("oversized", "completed", "review", "invalid_response"),
        ("refused", "completed", "review", "refused"),
        ("slow", "completed", "review", "timeout"),
        ("quota", "failed", None, "config"),
        ("unauthorized", "failed", None, "unauthorized"),
        ("forbidden-large", "failed", None, "unauthorized"),
        ("bad-request", "failed", None, "config"),
        ("redirect", "failed", None, "config"),
        ("unreachable-port", "completed", "review", "unreachable"),
    ],
)
def test_real_adapter_over_local_simulation(tmp_path, mode, status, branch, category):
    endpoint = os.environ.get("NEXUS_DECISION_SIM_URL", "")
    assert endpoint, "Start the local Postman decision simulation and set NEXUS_DECISION_SIM_URL"
    parts = urlsplit(endpoint)
    assert parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port
    if mode == "unreachable-port":
        endpoint = "http://127.0.0.1:1/api/alpha/decisions"
    calls = []

    async def record(label):
        calls.append(label)
        return label

    tool_registry.register("interop:decision-record", record, "Synthetic HTTP decision branch")
    client = partial(ask_strict, endpoint=endpoint, api_key="synthetic-fixture-key", model=mode)
    studio = StudioService(tmp_path, workbench_options={"decision_client": client})
    try:
        workflow = source()
        workflow["decision"].update(timeout_seconds=1)
        workflow["nodes"][0]["on_provider_error"] = "escalate"
        converted = studio.configuration.convert_interop(
            {
                "documents": [workflow],
                "bindings": {
                    "decisions": {"triage": {"backend": "openrouter", "model": mode}},
                    "tools": {"record": "interop:decision-record"},
                },
            },
            None,
        )
        assert converted["valid"], converted
        plan = studio.configuration.plan(converted["document"], None)
        studio.configuration.apply(plan["id"], plan["digest"], None)
        result = run(studio, "decide", {"ticket": "Synthetic local ticket"}, status)
        assert calls == ([branch] if branch else [])
        assert "DO_NOT_LOG_PROVIDER_DETAIL" not in json.dumps(result)
        if status == "completed":
            output = json.loads(result["output"])["decision"]
            assert output.get("provider_error") == category
            saved = studio.records("decisions")[0]
            if category is None:
                assert saved["provider_metadata"]["model"] == mode + "-resolved"
                assert saved["provider_metadata"]["usage"] == {
                    "input_tokens": 42,
                    "output_tokens": 0,
                }
            else:
                assert output["answers"] == {}
        else:
            assert category in result["error"]
    finally:
        studio.close()
        tool_registry.unregister("interop:decision-record", func=record)

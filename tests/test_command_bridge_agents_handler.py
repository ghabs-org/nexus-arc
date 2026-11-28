"""Tests for POST /api/v1/agents/run bridge endpoint."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from nexus.core.command_bridge.agents_handler import handle_agents_run


AGENTS = [
    {"name": "Summariser", "description": "Summarises text", "response": "Summary done"},
    {"name": "Reviewer", "description": "Reviews output", "response": "Review OK"},
]

# Patch _get_ai_provider to return None so tests use MockSubAgent deterministically.
# This also prevents real provider availability checks from running in CI.
_no_provider = patch(
    "nexus.core.command_bridge.agents_handler._get_ai_provider",
    new=AsyncMock(return_value=None),
)


def _run(coro):
    return asyncio.run(coro)


@_no_provider
def test_sequential_run():
    result = _run(handle_agents_run({
        "task": "Summarise this document",
        "agent_type": "sequential",
        "agents": AGENTS,
    }))
    assert result["ok"] is True
    assert "Review OK" in result["output"]
    assert "metadata" in result


@_no_provider
def test_parallel_run():
    result = _run(handle_agents_run({
        "task": "Analyse in parallel",
        "agent_type": "parallel",
        "agents": AGENTS,
    }))
    assert result["ok"] is True
    assert "Summary done" in result["output"]
    assert "Review OK" in result["output"]


@_no_provider
def test_loop_run():
    result = _run(handle_agents_run({
        "task": "Keep trying",
        "agent_type": "loop",
        "agents": [{"name": "Worker", "description": "Does work", "response": "done"}],
        "max_iterations": 3,
        "stop_condition": "True",  # stops immediately on first iteration
    }))
    assert result["ok"] is True
    assert result["metadata"]["loop_iterations"] == 1


@_no_provider
def test_loop_requires_single_agent():
    result = _run(handle_agents_run({
        "task": "loop",
        "agent_type": "loop",
        "agents": AGENTS,
    }))
    assert result["ok"] is False
    assert "exactly one" in result["error"]


@_no_provider
def test_missing_task():
    result = _run(handle_agents_run({"agent_type": "sequential", "agents": AGENTS}))
    assert result["ok"] is False
    assert "task is required" in result["error"]


@_no_provider
def test_missing_agents():
    result = _run(handle_agents_run({"task": "do something", "agent_type": "sequential"}))
    assert result["ok"] is False
    assert "agents list" in result["error"]


@_no_provider
def test_unknown_agent_type():
    result = _run(handle_agents_run({
        "task": "task",
        "agent_type": "invalid_type",
        "agents": AGENTS,
    }))
    assert result["ok"] is False
    assert "unknown agent_type" in result["error"]


def test_coordinator_without_provider():
    """Coordinator must return an error when all providers are unavailable."""
    with patch(
        "nexus.core.command_bridge.agents_handler._get_ai_provider",
        new=AsyncMock(return_value=None),
    ):
        result = _run(handle_agents_run({
            "task": "coordinate",
            "agent_type": "coordinator",
            "agents": AGENTS,
        }))
    assert result["ok"] is False
    assert "AI provider" in result["error"]


def test_stop_condition_allows_safe_expression():
    from nexus.core.command_bridge.agents_handler import _make_stop_condition
    from nexus.agents.base import AgentOutput

    cond = _make_stop_condition("content == 'done'")
    assert cond(AgentOutput(content="done")) is True
    assert cond(AgentOutput(content="working")) is False
    cond = _make_stop_condition("'error' in content or content == 'done'")
    assert cond(AgentOutput(content="fatal error")) is True


def test_stop_condition_rejects_code_execution():
    from nexus.core.command_bridge.agents_handler import _make_stop_condition
    from nexus.agents.base import AgentOutput

    witness = []
    for expr in (
        "__import__('os').system('echo PWNED')",
        "(lambda: 1)()",
        "[x for x in range(3)]",
        "output.content.upper()",
        "open('/etc/passwd').read()",
    ):
        cond = _make_stop_condition(expr)
        assert cond(AgentOutput(content="done")) is False, expr
    assert witness == []


@_no_provider
def test_run_applies_default_and_explicit_timeout():
    import asyncio as _asyncio

    from nexus.agents.base import AgentContext, AgentOutput, BaseAgent

    seen = {}

    class _Spy(BaseAgent):
        async def run(self, context):
            seen["parent_timeout"] = self._parent.timeout
            return AgentOutput(content="ok")

    with patch(
        "nexus.core.command_bridge.agents_handler._build_sub_agents",
        return_value=[_Spy("s")],
    ):
        result = _run(handle_agents_run({
            "task": "t", "agent_type": "sequential", "agents": AGENTS,
        }))
        assert result["ok"] is True
        assert seen["parent_timeout"] == 300

        result = _run(handle_agents_run({
            "task": "t", "agent_type": "sequential", "agents": AGENTS, "timeout": 45,
        }))
        assert result["ok"] is True
        assert seen["parent_timeout"] == 45


@_no_provider
def test_requester_reaches_output_metadata():
    result = _run(handle_agents_run({
        "task": "t",
        "agent_type": "sequential",
        "agents": AGENTS,
        "requester": {"source": "openclaw", "user_id": "u9"},
    }))
    assert result["ok"] is True
    assert result["metadata"]["requester"] == {"source": "openclaw", "user_id": "u9"}

"""Tests for Coordinator agent."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.coordinator import Coordinator, _call_nexus_router

# ── Helpers ──────────────────────────────────────────────────────────────────


class FixedAgent(BaseAgent):
    """Simple sub-agent that returns a fixed response."""

    def __init__(self, name: str, description: str, response: str):
        super().__init__(name=name, description=description)
        self.response = response
        self.ran = False

    async def run(self, context: AgentContext) -> AgentOutput:
        self.ran = True
        return AgentOutput(content=self.response, metadata={"agent": self.name})


def make_mock_provider(delegation_response: str = "CodeReviewer"):
    """Return a mock AIProvider that returns a fixed delegation choice."""
    provider = MagicMock()
    result = MagicMock()
    result.success = True
    result.output = delegation_response
    provider.execute_agent = AsyncMock(return_value=result)
    return provider


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_coordinator_delegates_to_correct_agent():
    reviewer = FixedAgent("CodeReviewer", "Reviews code", "LGTM")
    writer = FixedAgent("ContentWriter", "Writes content", "Here is your content")
    provider = make_mock_provider(delegation_response="CodeReviewer")

    coord = Coordinator("coord", [reviewer, writer], provider)
    ctx = AgentContext(task="Review this PR")
    result = asyncio.run(coord.run(ctx))

    assert reviewer.ran is True
    assert writer.ran is False
    assert result.content == "LGTM"
    assert result.metadata["coordinator_selected_agent"] == "CodeReviewer"


def test_coordinator_fallback_on_unknown_agent():
    """If LLM returns unknown agent name, coordinator falls back to first sub-agent."""
    a = FixedAgent("Alpha", "First agent", "alpha response")
    b = FixedAgent("Beta", "Second agent", "beta response")
    provider = make_mock_provider(delegation_response="NonExistentAgent")

    coord = Coordinator("coord", [a, b], provider)
    ctx = AgentContext(task="do something")
    result = asyncio.run(coord.run(ctx))

    assert a.ran is True
    assert result.content == "alpha response"


def test_coordinator_fallback_on_provider_failure():
    """If LLM call fails, coordinator falls back to first sub-agent."""
    a = FixedAgent("Alpha", "First", "alpha")
    provider = MagicMock()
    provider.execute_agent = AsyncMock(side_effect=Exception("LLM failed"))

    coord = Coordinator("coord", [a], provider)
    ctx = AgentContext(task="task")
    result = asyncio.run(coord.run(ctx))

    assert a.ran is True
    assert result.content == "alpha"


def test_coordinator_requires_sub_agents():
    provider = make_mock_provider()
    with pytest.raises(ValueError):
        Coordinator("empty", [], provider)


def test_coordinator_calls_nexus_router():
    """Coordinator should attempt to call nexus-router for model selection."""
    a = FixedAgent("Alpha", "First", "result")
    provider = make_mock_provider("Alpha")

    with patch("nexus.agents.coordinator._call_nexus_router") as mock_router:
        mock_router.return_value = "claude-sonnet"
        coord = Coordinator("coord", [a], provider)
        ctx = AgentContext(task="test")
        result = asyncio.run(coord.run(ctx))

    mock_router.assert_called_once()
    assert result.metadata["coordinator_model"] == "claude-sonnet"


def test_coordinator_works_without_nexus_router():
    """Coordinator must work when nexus-router is unavailable."""
    a = FixedAgent("Alpha", "First", "result")
    provider = make_mock_provider("Alpha")

    with patch("nexus.agents.coordinator._call_nexus_router") as mock_router:
        mock_router.return_value = None  # router unavailable
        coord = Coordinator("coord", [a], provider)
        ctx = AgentContext(task="test")
        result = asyncio.run(coord.run(ctx))

    assert result.content == "result"
    assert result.metadata["coordinator_model"] is None


def test_call_nexus_router_unavailable():
    """_call_nexus_router should return None if router is unreachable."""
    result = _call_nexus_router("http://127.0.0.1:19999", "task")
    assert result is None


def test_coordinator_case_insensitive_name_match():
    """Agent name matching should be case-insensitive."""
    a = FixedAgent("CodeReviewer", "Reviewer", "reviewed")
    provider = make_mock_provider(delegation_response="codereviewer")

    coord = Coordinator("coord", [a], provider)
    ctx = AgentContext(task="review")
    result = asyncio.run(coord.run(ctx))

    assert a.ran is True
    assert result.content == "reviewed"


def test_coordinator_does_not_mutate_sub_agent_model_override():
    """Coordinator must not persist router model onto the shared sub-agent instance."""
    from nexus.agents.coordinator import LLMSubAgent

    mock_provider = make_mock_provider("llm-sub")
    sub = LLMSubAgent(name="llm-sub", description="test sub-agent", ai_provider=mock_provider)
    assert sub.model_override is None

    coord_provider = make_mock_provider("llm-sub")
    coord = Coordinator("coord", [sub], coord_provider)

    with patch("nexus.agents.coordinator._call_nexus_router") as mock_router:
        mock_router.return_value = "claude-sonnet"
        # Patch the actual LLMSubAgent.run to avoid full AIProvider bootstrap
        sub_result = AgentOutput(content="ok", metadata={"success": True, "agent": "llm-sub"})
        with patch.object(sub, "run", new=AsyncMock(return_value=sub_result)):
            asyncio.run(coord.run(AgentContext(task="test")))

    # model_override on the shared instance must be unchanged after the run
    assert sub.model_override is None


def test_coordinator_explicit_model_beats_router():
    """A pinned sub-agent model wins; metadata reports the pin."""
    from nexus.agents.coordinator import LLMSubAgent

    sub = LLMSubAgent(
        name="llm-sub",
        description="pinned",
        ai_provider=make_mock_provider("llm-sub"),
        model_override="opencode/spark-free",
    )
    coord = Coordinator("coord", [sub], make_mock_provider("llm-sub"))
    with patch("nexus.agents.coordinator._call_nexus_router") as mock_router:
        mock_router.return_value = "claude-sonnet"
        sub_result = AgentOutput(content="ok", metadata={"success": True, "agent": "llm-sub"})
        with patch.object(sub, "run", new=AsyncMock(return_value=sub_result)) as mock_run:
            output = asyncio.run(coord.run(AgentContext(task="test")))
    assert output.metadata["coordinator_model"] == "opencode/spark-free"
    sent_context = mock_run.await_args.args[0]
    assert "_router_model" not in sent_context.metadata


def test_plugin_prefers_agent_spec_model_over_profiles():
    """agent_spec_model_resolver beats model_profiles in _resolve_model_for_tool."""
    from nexus.plugins.builtin.ai_runtime_plugin import AIOrchestrator

    orchestrator = AIOrchestrator(
        {
            "tool_preferences": {"writer": {"provider": "opencode", "profile": "fast"}},
            "model_profiles": {"fast": {"opencode": "opencode/big"}},
            "agent_spec_model_resolver": lambda agent, project: "opencode/pinned",
        }
    )
    assert (
        orchestrator._resolve_model_for_tool(
            tool=orchestrator._parse_provider({"provider": "opencode", "profile": "x"}),
            agent_name="writer",
            project_name="nexus",
        )
        == "opencode/pinned"
    )


def test_coordinator_prefers_jev_when_sure(monkeypatch):
    """Sure Jev choice skips the LLM delegation call entirely."""
    from nexus.agents.coordinator import Coordinator

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    calls = []

    def _fake_decide(state, options, instructions, **kwargs):
        calls.append((state, sorted(options)))
        return ("Beta", 0.9, True)

    alpha = FixedAgent("Alpha", "first", "A")
    beta = FixedAgent("Beta", "second", "B")
    provider = make_mock_provider("Alpha")
    with patch("nexus.adapters.decisions.jev.decide_choice", _fake_decide):
        coord = Coordinator("coord", [alpha, beta], provider)
        chosen = asyncio.run(coord._select_agent(AgentContext(task="do it")))
    assert chosen is beta
    provider.execute_agent.assert_not_called()
    assert calls[0][1] == ["Alpha", "Beta"]


def test_coordinator_falls_back_to_llm_when_unsure(monkeypatch):
    from nexus.agents.coordinator import Coordinator

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    def _unsure(state, options, instructions, **kwargs):
        return ("Beta", 0.1, False)

    alpha = FixedAgent("Alpha", "first", "A")
    with patch("nexus.adapters.decisions.jev.decide_choice", _unsure):
        coord = Coordinator("coord", [alpha], make_mock_provider("Alpha"))
        chosen = asyncio.run(coord._select_agent(AgentContext(task="do it")))
    assert chosen is alpha


def test_coordinator_skips_jev_without_key(monkeypatch):
    from nexus.agents.coordinator import Coordinator

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    alpha = FixedAgent("Alpha", "first", "A")
    with patch(
        "nexus.adapters.decisions.jev.decide_choice",
        side_effect=AssertionError("must not be called"),
    ):
        coord = Coordinator("coord", [alpha], make_mock_provider("Alpha"))
        chosen = asyncio.run(coord._select_agent(AgentContext(task="do it")))
    assert chosen is alpha

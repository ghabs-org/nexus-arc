"""Composites bound each child run: child timeout wins, else parent budget.

Timeouts become failed outputs (metadata timeout=True), never raised —
sequential keeps its prior outputs, parallel still merges the survivors.
"""

from __future__ import annotations

import asyncio

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent, run_child
from nexus.agents.loop import LoopAgent
from nexus.agents.parallel import ParallelAgent
from nexus.agents.sequential import SequentialAgent


class _Sleepy(BaseAgent):
    def __init__(self, name="sleepy", delay=30.0, **kwargs):
        super().__init__(name, **kwargs)
        self.delay = delay

    async def run(self, context: AgentContext) -> AgentOutput:
        await asyncio.sleep(self.delay)
        return AgentOutput(content="woke")


class _Quick(BaseAgent):
    def __init__(self, name="quick"):
        super().__init__(name)

    async def run(self, context: AgentContext) -> AgentOutput:
        return AgentOutput(content="fast")


def test_run_child_converts_timeout_to_output():
    out = asyncio.run(run_child(_Sleepy(), AgentContext(task="t"), timeout=0.05))
    assert out.metadata["timeout"] is True
    assert out.metadata["agent"] == "sleepy"


def test_child_timeout_beats_parent_budget():
    out = asyncio.run(run_child(_Sleepy(timeout=0.05), AgentContext(task="t"), timeout=30.0))
    assert out.metadata["timeout"] is True
    assert out.metadata["timeout_seconds"] == 0.05


def test_no_timeout_preserves_behavior():
    out = asyncio.run(run_child(_Quick(), AgentContext(task="t")))
    assert (out.content, out.metadata.get("timeout")) == ("fast", None)


def test_sequential_marks_timeout_and_keeps_priors():
    seq = SequentialAgent("seq", [_Quick("a"), _Sleepy("b")], timeout=0.05)
    out = asyncio.run(seq.run(AgentContext(task="t")))
    assert out.metadata["timeout"] is True
    assert seq.sub_agents[0].name == "a"
    assert out.metadata["sequential_outputs"][0] == "fast"


def test_parallel_isolates_timeout():
    par = ParallelAgent("par", [_Quick("a"), _Sleepy("b")], timeout=0.05)
    out = asyncio.run(par.run(AgentContext(task="t")))
    assert "fast" in out.content


def test_loop_bounds_each_iteration():
    loop = LoopAgent("loop", _Sleepy(), stop_condition=lambda o: True, timeout=0.05)
    out = asyncio.run(loop.run(AgentContext(task="t")))
    assert out.metadata["timeout"] is True

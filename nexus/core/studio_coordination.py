"""Scoped autonomous delegation without reading host-wide Jev/router credentials."""

from __future__ import annotations

import asyncio

from nexus.agents.base import AgentOutput, propagate_requester, run_child
from nexus.agents.context import slice_context
from nexus.agents.coordinator import Coordinator


class StudioCoordinator(Coordinator):
    def __init__(self, studio, children, payload, run_id):
        super().__init__(
            name="studio-coordinator",
            sub_agents=children,
            ai_provider=None,
            router_url="",
            workspace_path=str(studio.root),
            timeout=600,
        )
        self.studio, self.payload, self.run_id = studio, payload, run_id

    async def _select_agent(self, context):
        request = {
            "requester_nexus_id": self.payload.get("requester_nexus_id"),
            "provider": self.payload.get("coordinator_provider") or "codex",
        }
        result = await self.studio._invoke(
            request, self._build_delegation_prompt(context), self.run_id
        )
        choice = result.output.strip().strip('"').strip("'")
        selected = next((child for child in self.sub_agents if child.name == choice), None)
        accepted = selected is not None
        if selected is None:
            selected = next(
                child for child in self.sub_agents if child.name == self.payload["default_agent_id"]
            )
        self.studio._update_run(
            self.run_id,
            coordinator_selected_agent=selected.name,
            coordinator_choice_accepted=accepted,
            coordinator_fallback=self.payload["default_agent_id"],
        )
        return selected

    async def run(self, context):
        selected = await self._select_agent(context)
        sliced = slice_context(context)
        # Router use is opt-in through an embedding callback owned by the server,
        # never an endpoint supplied by a browser/model or global host variables.
        router = self.studio.workbench.router_client
        if router:
            model = await asyncio.to_thread(
                router, self.payload.get("requester_nexus_id"), context.task, selected.name
            )
            if asyncio.iscoroutine(model):
                model = await model
            if not isinstance(model, str) or not model.strip() or len(model) > 200:
                raise ValueError("Configured model router returned an invalid model")
            sliced.metadata["studio_router_model"] = model
        output = await run_child(selected, sliced, self.timeout)
        if output.metadata.get("timeout"):
            raise RuntimeError("Selected specialist timed out")
        return propagate_requester(
            sliced,
            AgentOutput(
                content=f"Selected specialist: {selected.name}\n\n{output.content}",
                metadata={**output.metadata, "coordinator_selected_agent": selected.name},
            ),
        )

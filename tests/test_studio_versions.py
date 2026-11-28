"""Version activation, pinned queued instructions and effect crash recovery."""

from unittest.mock import AsyncMock

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_nodes import StudioNodeTypes


def test_agent_version_activation_requires_latest_revision(tmp_path):
    app = StudioService(tmp_path)
    try:
        first = app.save_definition(
            "agents", {"name": "Reviewer", "instructions": "First instructions"}
        )
        second = app.update_definition(
            "agents",
            "reviewer",
            {
                "name": "Reviewer",
                "instructions": "Second instructions",
                "revision": first["revision"],
            },
        )
        versions = app.versions.list("agents", "reviewer")
        assert len(versions["items"]) == 2
        with pytest.raises(ValueError, match="changed"):
            app.versions.activate(
                "agents",
                "reviewer",
                {"version": first["revision"], "revision": first["revision"]},
                None,
            )
        app.versions.activate(
            "agents",
            "reviewer",
            {"version": first["revision"], "revision": second["revision"]},
            None,
        )
        assert app.definition("agents", "reviewer")["spec"]["purpose"] == "First instructions"
        assert len(app.records("definition-activations")) == 1
    finally:
        app.close()


@pytest.mark.asyncio
async def test_ambiguous_integration_attempt_requires_review_before_retry(tmp_path):
    app = StudioService(tmp_path)
    adapter = AsyncMock()
    adapter.add_comment.side_effect = RuntimeError("Response lost after send")
    app.connections.factories["github"] = lambda *args: adapter
    try:
        app.catalog.save(
            "connectors",
            {
                "name": "Git",
                "data": {"type": "github", "repo": "o/r", "enabled": True, "actions": ["comment"]},
            },
        )
        node = {
            "id": "comment",
            "kind": "integration",
            "connector_id": "git",
            "action": "comment",
            "arguments": {"issue_id": "1", "body": "Reviewed"},
        }
        app._write(app._path("runs", "run"), {"id": "run", "status": "failed"})
        runtime = StudioNodeTypes(app)
        with pytest.raises(ValueError, match="failed"):
            await runtime.execute(node, {"task": "Review"}, "run", [])
        with pytest.raises(ValueError, match="uncertain"):
            await runtime.execute(node, {"task": "Review"}, "run", [])
        assert adapter.add_comment.await_count == 1
        effect = app.records("integration-effects")[0]
        runtime.resolve(
            effect["id"],
            {"outcome": "completed", "confirm": True, "output": "Remote comment exists"},
            None,
        )
        assert await runtime.execute(node, {"task": "Review"}, "run", []) == "Remote comment exists"
        assert adapter.add_comment.await_count == 1
    finally:
        app.close()

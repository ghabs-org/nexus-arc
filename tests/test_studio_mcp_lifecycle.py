"""Real MCP process-tree shutdown and shared execution ownership regression proof."""

import asyncio

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id


@pytest.mark.asyncio
async def test_connector_inside_owned_run_is_not_registered_for_duplicate_cancellation(
    tmp_path, monkeypatch
):
    studio = StudioService(tmp_path)
    task, loop = asyncio.current_task(), asyncio.get_running_loop()
    studio.active_tasks["existing-run"] = (loop, task)
    token = current_run_id.set("existing-run")

    async def invoke(*args, **kwargs):
        assert list(studio.active_tasks) == ["existing-run"]
        return "done"

    monkeypatch.setattr(studio.connections, "_execute", invoke)
    try:
        assert await studio.connections.execute("fixture", {}) == "done"
        assert await asyncio.create_task(studio.connections.execute("fixture", {})) == "done"
        assert list(studio.active_tasks) == ["existing-run"]
    finally:
        current_run_id.reset(token)
        studio.active_tasks.clear()
        studio.close()


@pytest.mark.asyncio
async def test_shutdown_rejects_late_connector_dispatch_without_remote_work(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    studio.close()

    async def forbidden(*args, **kwargs):
        raise AssertionError("A late request must not dispatch")

    monkeypatch.setattr(studio.connections, "_execute", forbidden)
    with pytest.raises(RuntimeError, match="shutting down"):
        await studio.connections.execute("fixture", {})
    assert not studio.active_tasks


@pytest.mark.asyncio
async def test_failed_direct_connector_releases_its_execution_ownership(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)

    async def fail(*args, **kwargs):
        assert len(studio.active_tasks) == 1
        raise PermissionError("fixture failure")

    monkeypatch.setattr(studio.connections, "_execute", fail)
    try:
        with pytest.raises(PermissionError):
            await studio.connections.execute("fixture", {})
        assert not studio.active_tasks
    finally:
        studio.close()

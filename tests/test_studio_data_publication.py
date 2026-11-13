"""Private worker results cannot publish after cancellation or lost input access."""

import asyncio
import base64
from datetime import UTC, datetime

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id
from tests.test_studio_dynamic_embeds import upload


@pytest.mark.parametrize(
    "interruption", ["cancelled", "completed", "cancel-requested", "deleted-input", "closing"]
)
def test_worker_completion_rechecks_run_and_input_before_saving(
    tmp_path, monkeypatch, interruption
):
    studio = StudioService(tmp_path)
    try:
        source = upload(studio, "rows.json", b"[]")
        run = {
            "id": "worker-test",
            "status": "running",
            "owner_id": None,
            "kind": "tool",
            "provider": "local-tool",
            "created_at": datetime.now(UTC).isoformat(),
        }
        studio._write(studio._path("runs", run["id"]), run)

        def finish(*args):
            if interruption == "deleted-input":
                studio.catalog.archive("artifacts", source["id"], {"revision": source["revision"]})
            elif interruption == "closing":
                studio.closing = True
            else:
                studio._update_run(
                    run["id"],
                    **(
                        {"cancel_requested": True}
                        if interruption == "cancel-requested"
                        else {"status": interruption}
                    ),
                )
            return {
                "stdout": "Late result",
                "files": [
                    {"filename": "late.txt", "content_base64": base64.b64encode(b"Late").decode()}
                ],
            }

        monkeypatch.setattr(studio.data_tools.sandbox, "execute", finish)
        token = current_run_id.set(run["id"])
        try:
            with pytest.raises((asyncio.CancelledError, ValueError, FileNotFoundError)):
                asyncio.run(
                    studio.data_tools.execute(
                        "transform_jq", {"artifact_id": source["id"], "expression": "."}
                    )
                )
        finally:
            current_run_id.reset(token)
        assert not any(
            r.get("generated_by_run") == run["id"] for r in studio.catalog.list("artifacts")
        )
    finally:
        studio.close()


@pytest.mark.parametrize("change", ["revoked", "transferred"])
def test_worker_does_not_publish_after_team_access_changes(tmp_path, monkeypatch, change):
    from types import SimpleNamespace

    from nexus.core.studio_connections import execution_identity
    from nexus.core.studio_team import NexusTeamPolicy

    policy = NexusTeamPolicy(
        project_key="worker-test",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login",
        bindings={"alice": "operator", "bob": "operator", "owner": "owner"},
        manager=SimpleNamespace(has_project_access=lambda *args: True),
    )
    studio = StudioService(
        tmp_path / "workspace", state_dir=tmp_path / "private", team_policy=policy
    )
    try:
        source = upload(studio, "rows.json", b"[]", actor="alice")

        def finish(*args):
            if change == "revoked":
                policy.bindings.pop("alice")
            else:
                source["owner_id"] = "bob"
                studio._write(studio.catalog._path("artifacts", source["id"]), source)
            return {"stdout": "Late", "files": [{"filename": "late.txt", "content_base64": "eA=="}]}

        monkeypatch.setattr(studio.data_tools.sandbox, "execute", finish)
        token = execution_identity.set("alice")
        try:
            with pytest.raises(PermissionError):
                asyncio.run(
                    studio.data_tools.execute(
                        "transform_jq", {"artifact_id": source["id"], "expression": "."}
                    )
                )
        finally:
            execution_identity.reset(token)
        assert len(studio.catalog.list("artifacts")) == 1
    finally:
        studio.close()

"""Interactive Studio onboarding guide state, role scoping, versioning, and zero side effects."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_onboarding import GUIDE_STEPS, GUIDE_VERSION
from nexus.core.studio_team import NexusTeamPolicy
from nexus.studio import make_studio_handler


def test_guide_state_lifecycle_and_privacy_guardrails(tmp_path):
    calls = []

    class UnexpectedProvider:
        async def check_availability(self):
            calls.append("check")
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            raise AssertionError("Guide must never execute a provider")

    studio = StudioService(tmp_path, provider_factory=lambda _: UnexpectedProvider())
    try:
        initial = studio.workbench.get(["guide"], None)
        assert initial["version"] == GUIDE_VERSION == 2
        assert initial["status"] == "new"
        assert initial["seen_version"] == 0
        assert len(initial["steps"]) == 15
        assert [step["id"] for step in initial["steps"]] == [s["id"] for s in GUIDE_STEPS]
        assert "no saved agents or workflows yet" in initial["steps"][0]["capability_note"]

        _, started = studio.workbench.post(
            ["guide"],
            {"action": "start", "current_step": "providers", "origin_view": "workflows"},
            None,
        )
        assert started["status"] == "in_progress"
        assert started["current_step"] == "providers"
        assert started["origin_view"] == "workflows"
        assert started["seen_version"] == GUIDE_VERSION

        _, stepped = studio.workbench.post(
            ["guide"],
            {
                "action": "step",
                "current_step": "conversations",
                "completed_step": "providers",
            },
            None,
        )
        assert stepped["status"] == "in_progress"
        assert stepped["current_step"] == "conversations"
        assert "providers" in stepped["completed_steps"]

        _, dismissed = studio.workbench.post(["guide"], {"action": "dismiss"}, None)
        assert dismissed["status"] == "dismissed"
        assert dismissed["dismissed_at"] is not None

        _, completed = studio.workbench.post(["guide"], {"action": "complete"}, None)
        assert completed["status"] == "completed"
        assert completed["completed_at"] is not None
        assert set(completed["completed_steps"]) == {s["id"] for s in GUIDE_STEPS}

        # Dismissing after completion preserves completed status.
        _, after_complete = studio.workbench.post(["guide"], {"action": "dismiss"}, None)
        assert after_complete["status"] == "completed"

        # Sensitive keys are strictly rejected.
        for forbidden_key in ("prompt", "messages", "api_key", "token", "secret", "credentials"):
            with pytest.raises(ValueError, match="progress metadata"):
                studio.workbench.post(["guide"], {"action": "step", forbidden_key: "leak"}, None)

        with pytest.raises(ValueError, match="Unknown interactive guide step"):
            studio.workbench.post(["guide"], {"action": "step", "current_step": "nonexistent"}, None)

        assert calls == []
        assert studio.definitions("agents") == []
        assert studio.definitions("workflows") == []
        assert studio.records("runs") == []
    finally:
        studio.close()


def test_guide_version_upgrade_never_resets_dismissal_or_completion(tmp_path):
    studio = StudioService(tmp_path)
    try:
        studio.workbench.post(["guide"], {"action": "dismiss"}, None)
        key = studio.workbench.settings_key(None)
        path = studio._path("onboarding-guide", key)
        record = studio._read(path)
        record["seen_version"] = 1
        studio._write(path, record)

        upgraded = studio.workbench.get(["guide"], None)
        assert upgraded["status"] == "dismissed"
        assert upgraded["upgrade_available"] is True
        assert upgraded["new_steps"] == ["workspace-files", "studio-assistant"]

        _, acknowledged = studio.workbench.post(
            ["guide"], {"action": "acknowledge_upgrade"}, None
        )
        assert acknowledged["status"] == "dismissed"
        assert acknowledged["upgrade_available"] is False
        assert acknowledged["new_steps"] == []
    finally:
        studio.close()


def test_team_roles_isolate_user_guide_progress_and_explain_permissions(tmp_path):
    class Manager:
        def get_session_and_setup_status(self, session):
            users = {
                "a" * 32: "alice",
                "b" * 32: "bob",
            }
            identity = users.get(session)
            return {
                "exists": bool(identity),
                "status": "completed",
                "nexus_id": identity,
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            }

        def has_project_access(self, identity, project):
            return identity in {"alice", "bob"} and project == "demo"

    policy = NexusTeamPolicy(
        project_key="demo",
        public_url="https://localhost:8443",
        login_url="https://localhost:8443/login",
        bindings={"alice": "owner", "bob": "viewer"},
        manager=Manager(),
        environment_resolver=lambda identity: (
            {"OPENAI_API_KEY": "sk-test"} if identity == "alice" else {},
            None,
        ),
    )
    studio = StudioService(tmp_path / "project", team_policy=policy, state_dir=tmp_path / "state")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "csrf-secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"

    def call(path, session, body=None, csrf=None):
        headers = {
            "Host": "localhost:8443",
            "Origin": "https://localhost:8443",
            "X-Nexus-Session": session,
            "Content-Type": "application/json",
        }
        if csrf:
            headers["X-Nexus-Studio-Token"] = csrf
        req = urllib.request.Request(
            origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    try:
        _, bob_boot = call("/api/bootstrap", "b" * 32)
        _, alice_boot = call("/api/bootstrap", "a" * 32)

        status, bob_guide = call("/api/workbench/guide", "b" * 32)
        assert status == 200
        assert bob_guide["role"] == "viewer"
        by_id = {step["id"]: step for step in bob_guide["steps"]}
        assert by_id["workspace-navigation"]["restricted"] is False
        assert by_id["quick-build"]["restricted"] is True
        assert "requires the builder role" in by_id["quick-build"]["role_note"]
        assert by_id["connectors-knowledge"]["restricted"] is True
        assert "requires the owner role" in by_id["connectors-knowledge"]["role_note"]

        # Viewer can save their own guide progress even though they cannot run or build resources.
        status, bob_saved = call(
            "/api/workbench/guide",
            "b" * 32,
            {"action": "step", "current_step": "workflows", "completed_step": "workspace-navigation"},
            csrf=bob_boot["token"],
        )
        assert status == 200
        assert bob_saved["status"] == "in_progress"
        assert bob_saved["current_step"] == "workflows"

        # Alice's guide state remains untouched ("new") and has owner permissions.
        _, alice_guide = call("/api/workbench/guide", "a" * 32)
        assert alice_guide["status"] == "new"
        assert alice_guide["role"] == "owner"
        assert all(not step["restricted"] for step in alice_guide["steps"])
        providers_step = next(s for s in alice_guide["steps"] if s["id"] == "providers")
        assert "OpenAI API" in providers_step["capability_note"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        studio.close()


def test_nexus_bot_example_consumes_interactive_guide_configuration(tmp_path):
    script = Path(__file__).parents[1] / "examples/nexus-bot/src/studio_experience.py"
    result = subprocess.run(
        [sys.executable, str(script), "--dir", str(tmp_path), "--guide", "start"],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert "Interactive guide: v2 (in_progress, 15 steps)" in result.stdout

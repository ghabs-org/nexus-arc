"""Shared mode reuses Nexus identity, rejects cross-user access and isolates CLIs."""

import asyncio
import base64
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.adapters.ai.base import ExecutionContext
from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_team import ContainerCLIProvider, NexusTeamPolicy
from nexus.studio import make_studio_handler

SESSIONS = {"a" * 32: "alice", "b" * 32: "bob", "c" * 32: "viewer", "d" * 32: "owner"}


def test_release_health_http_enforces_role_scope_and_build_owner_access(team):
    import sqlite3

    from tests.test_studio_build_lifecycle import proposal

    service, policy, _, request, _ = team
    with sqlite3.connect(service.root / "health.db") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
    identity = "healthteam1234"
    normalized = service.builder.normalize(
        proposal(
            resources=[
                {
                    "kind": "connectors",
                    "id": "database",
                    "name": "Database",
                    "data": {"type": "sqlite", "path": "health.db"},
                }
            ]
        ),
        identity,
        "codex",
    )
    service._write(
        service._path("builds", identity),
        {
            "id": identity,
            "owner_id": "alice",
            "status": "draft",
            "revision": 1,
            "provider": "codex",
            "model": "",
            "proposal": normalized,
            "messages": [],
        },
    )
    code, build = request("/api/builds/" + identity + "/apply", {"revision": 1}, user="owner")
    assert code == 201, build
    key = next(
        c["key"]
        for c in service.resource_lifecycle.inspect_build(
            build["id"], {"nexus_id": "alice", "role": "builder"}
        )["components"]
        if c["key"].startswith("connectors:")
    )
    route = "/api/studio/builds/" + build["id"] + "/health"
    payload = {"revision": build["revision"], "release_id": build["release_id"], "component": key}
    assert request(route, payload)[0] == 401
    for user in ("alice", "bob", "viewer"):
        assert request(route, payload, user=user)[0] == 403
    code, result = request(route, payload, user="owner")
    assert code == 200 and result["status"] == "passed", result
    assert result["owner_id"] == "alice"
    policy.bindings["alice"] = "viewer"
    assert request(route, payload, user="owner")[0] == 403
    policy.bindings["alice"] = "builder"


def test_build_lifecycle_http_preserves_ownership_roles_and_original_credentials(team):
    from tests.test_studio_build_lifecycle import connector, proposal

    service, policy, _, request, _ = team
    identity = "teamlifecycle123"
    normalized = service.builder.normalize(proposal(resources=[connector()]), identity, "codex")
    service._write(
        service._path("builds", identity),
        {
            "id": identity,
            "owner_id": "alice",
            "status": "draft",
            "revision": 1,
            "provider": "codex",
            "model": "",
            "proposal": normalized,
            "messages": [],
        },
    )
    base = "/api/studio/builds/" + identity
    assert request(base + "/review", {"revision": 1})[0] == 401
    assert request(base + "/review", {"revision": 1}, user="bob")[0] == 403
    assert request(base + "/review", {"revision": 1}, user="viewer")[0] == 403
    code, review = request(base + "/review", {"revision": 1}, user="owner")
    assert code == 200, review
    code, saved = request(
        "/api/builds/" + identity + "/apply",
        {
            "revision": 1,
            "review_id": review["id"],
            "digest": review["digest"],
        },
        user="owner",
    )
    assert code == 201, saved
    assert request(base + "/lifecycle", user="bob")[0] == 403
    assert request(base + "/lifecycle", user="alice")[0] == 200
    payload = {
        "revision": 1,
        "action": "activate",
        "enable": ["connectors:" + normalized["resources"][1]["id"]],
    }
    assert request(base + "/plan", payload, user="alice")[0] == 403
    policy.environment_resolver = lambda user: (
        {"LIFECYCLE_FIXTURE_TOKEN": "owner-only-fixture-token"} if user == "owner" else {},
        None,
    )
    code, blocked = request(base + "/plan", payload, user="owner")
    assert code == 200, blocked
    assert any(item["reason"] == "missing_credentials" for item in blocked["errors"])
    assert "owner-only-fixture-token" not in json.dumps(blocked)
    policy.environment_resolver = lambda user: ({"LIFECYCLE_FIXTURE_TOKEN": "fixture"}, None)
    code, ready = request(base + "/plan", payload, user="owner")
    assert code == 200 and not ready["errors"], ready
    code, result = request(
        base + "/transition",
        {
            "plan_id": ready["id"],
            "digest": ready["digest"],
        },
        user="owner",
    )
    assert code == 200 and result["status"] == "active", result
    assert service.builder.get(identity)["owner_id"] == "alice"


def test_document_revision_preview_retains_existing_account_and_project_authorization(team):
    service, _, manager, request, _ = team
    artifact = service.catalog.save(
        "artifacts",
        {
            "name": "Private source",
            "requester_nexus_id": "alice",
            "data": {
                "filename": "private.md",
                "content_base64": base64.b64encode(b"Alice source").decode(),
            },
        },
    )
    route = f"/api/catalog/artifacts/{artifact['id']}/preview?revision=1&page=1"
    assert request(route)[0] == 401
    assert request(route, user="bob")[0] == 403
    code, preview = request(route, user="alice")
    assert code == 200 and preview["text"] == "Alice source"
    manager.revoked.add("alice")
    assert request(route, user="alice")[0] == 401


def test_inbox_preserves_privacy_ownership_and_deployment_roles(team):
    service, _, manager, request, _ = team
    for identity in ("alice", "bob"):
        service._write(
            service._path("runs", identity + "-review"),
            {
                "id": identity + "-review",
                "requester_nexus_id": identity,
                "kind": "workflow",
                "status": "waiting",
                "task": identity + " private task",
                "awaiting_approval": [{"id": "gate", "name": "Review"}],
            },
        )
    assert [
        item["source_id"] for item in request("/api/workbench/inbox", user="alice")[1]["items"]
    ] == ["alice-review"]
    assert request("/api/workbench/inbox/gate-bob-review", user="alice")[0] == 404
    owner_items = request("/api/workbench/inbox", user="owner")[1]["items"]
    assert len(owner_items) == 2 and all(not item["actions"] for item in owner_items)
    assert request("/api/workbench/readiness", user="alice")[0] == 403
    assert request("/api/workbench/readiness", user="owner")[0] == 200
    assert request("/api/workbench/onboarding/any/check", {"revision": 1}, user="alice")[0] == 403
    assert (
        request(
            "/api/workbench/inbox/gate-alice-review/review", {"decision": "approve"}, user="viewer"
        )[0]
        == 403
    )
    manager.revoked.add("alice")
    assert request("/api/workbench/inbox", user="alice")[0] == 401


class NexusIdentity:
    def __init__(self):
        self.revoked = set()
        self.expired = set()

    def get_session_and_setup_status(self, session_id):
        identity = SESSIONS.get(session_id)
        return {
            "exists": bool(identity),
            "status": "completed",
            "nexus_id": identity,
            "expires_at": (
                datetime.now(UTC) + timedelta(hours=-1 if identity in self.expired else 1)
            ).isoformat(),
        }

    def has_project_access(self, identity, project):
        return project == "sample" and identity not in self.revoked


@pytest.fixture
def team(tmp_path):
    manager = NexusIdentity()
    policy = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login?next=/",
        bindings={"alice": "builder", "bob": "operator", "viewer": "viewer", "owner": "owner"},
        manager=manager,
        environment_resolver=lambda identity: ({"OPENAI_API_KEY": "user-only-key"}, None),
    )
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(success=True, output="Private evidence reply")

    policy.provider = lambda name, payload, builder=False: Provider()
    service = StudioService(
        tmp_path / "project", team_policy=policy, state_dir=tmp_path / "private-state"
    )
    service.save_definition(
        "agents", {"name": "Reviewer", "provider": "codex", "instructions": "Review evidence"}
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service, "csrf-root-secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, user=None, csrf=None, extra=None):
        headers = {
            "Host": "studio.example.test",
            "Origin": "https://studio.example.test",
            "Content-Type": "application/json",
        }
        if user:
            session = next(s for s, identity in SESSIONS.items() if identity == user)
            headers["Cookie"] = "nexus_web_session=" + session
            headers["X-Nexus-Studio-Token"] = csrf or policy.csrf(
                "csrf-root-secret", {"session_id": session}
            )
        headers.update(extra or {})
        req = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}" + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                content = response.read()
                return response.status, json.loads(
                    content
                ) if response.headers.get_content_type() == "application/json" else content
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    yield service, policy, manager, request, calls
    server.shutdown()
    server.server_close()
    thread.join()
    service.close()


def wait(service, run):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = service.run(run["id"])
        if current["status"] not in {"running", "queued"}:
            return current
        time.sleep(0.01)
    pytest.fail("Run did not finish")


def test_nexus_sessions_project_revocation_roles_and_per_session_csrf(team):
    service, policy, manager, request, _ = team
    assert request("/api/bootstrap")[0] == 401
    assert request("/")[0] == 200
    assert request("/api/auth/status")[1]["authenticated"] is False
    status, bootstrap = request("/api/bootstrap", user="alice")
    assert status == 200 and bootstrap["user"] == {"nexus_id": "alice", "role": "builder"}
    assert bootstrap["token"] != request("/api/bootstrap", user="bob")[1]["token"]
    assert request("/api/runs", {"task": "Test"}, user="bob", csrf=bootstrap["token"])[0] == 403
    assert request("/api/agents", {"name": "Bad"}, user="viewer")[0] == 403
    assert (
        request("/api/catalog/connectors", {"name": "Secret leak", "data": {}}, user="alice")[0]
        == 403
    )
    assert (
        request(
            "/api/runs", {"task": "Test"}, user="alice", extra={"Origin": "https://outside.invalid"}
        )[0]
        == 403
    )
    manager.revoked.add("alice")
    assert request("/api/bootstrap", user="alice")[0] == 401
    manager.expired.add("bob")
    assert request("/api/bootstrap", user="bob")[0] == 401


def test_runs_sessions_and_artifact_context_cannot_cross_user_boundaries(team):
    service, _, _, request, calls = team
    status, run = request(
        "/api/runs",
        {
            "task": "Private task",
            "agent_id": "reviewer",
            "requester_nexus_id": "bob",
            "_role": "owner",
        },
        user="alice",
    )
    assert status == 202 and wait(service, run)["status"] == "completed"
    assert service.run(run["id"])["requester_nexus_id"] == "alice"
    assert request(f"/api/runs/{run['id']}", user="bob")[0] == 403
    assert request("/api/runs", user="bob")[1]["items"] == []
    assert request(f"/api/sessions/{run['session_id']}", user="bob")[0] == 403
    assert (
        request("/api/runs", {"task": "Hijack", "session_id": run["session_id"]}, user="bob")[0]
        == 403
    )
    status, artifact = request(
        "/api/catalog/artifacts",
        {
            "name": "Private note",
            "data": {
                "filename": "note.txt",
                "content_base64": base64.b64encode(b"Private Alice data").decode(),
            },
        },
        user="alice",
    )
    assert status == 201 and artifact["owner_id"] == "alice"
    assert request(f"/api/catalog/artifacts/{artifact['id']}/download", user="bob")[0] == 403
    assert (
        request("/api/runs", {"task": "Read", "artifact_ids": [artifact["id"]]}, user="bob")[0]
        == 403
    )
    assert (
        request(
            "/api/catalog/artifacts",
            {**artifact, "data": {"filename": "bad", "content_base64": "eA=="}},
            user="bob",
        )[0]
        == 403
    )
    assert len(calls) == 1


def test_shared_tools_and_state_require_explicit_boundaries(team, tmp_path):
    service, policy, _, request, _ = team
    assert (
        request(
            "/api/runs",
            {"kind": "tool", "tool": "http:fetch", "args": ["http://169.254.169.254/"]},
            user="bob",
        )[0]
        == 403
    )
    assert (
        request(
            "/api/runs", {"kind": "tool", "tool": "text:wordcount", "args": ["one two"]}, user="bob"
        )[0]
        == 202
    )
    with pytest.raises(ValueError, match="private state"):
        StudioService(tmp_path / "unsafe", team_policy=policy)
    with pytest.raises(ValueError, match="HTTPS"):
        NexusTeamPolicy(
            project_key="sample",
            public_url="http://unsafe",
            login_url="https://unsafe/login",
            bindings={"owner": "owner"},
            manager=NexusIdentity(),
        )


@pytest.mark.parametrize("provider", ["codex", "claude-code"])
def test_shared_cli_never_mounts_workspace_or_passes_tokens_in_arguments(
    tmp_path, monkeypatch, provider
):
    user_home = tmp_path / "user-home"
    codex_home = tmp_path / "codex-home"
    user_home.mkdir()
    codex_home.mkdir()
    environment = {
        "HOME": str(user_home),
        "CODEX_HOME": str(codex_home),
        "OPENAI_API_KEY": "alice-private-key",
        "ANTHROPIC_API_KEY": "alice-private-key",
    }
    monkeypatch.setenv("ANTHROPIC_API_KEY", "host-private-key")
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(
        return_value=(
            b'{"result":"Safe response"}' if provider == "claude-code" else b"Safe response",
            b"",
        )
    )
    cleanup = MagicMock(returncode=0)
    cleanup.wait = AsyncMock(return_value=0)
    with (
        patch("nexus.core.studio_team.shutil.which", return_value="/usr/bin/docker"),
        patch(
            "nexus.core.studio_team.asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=[process, cleanup]),
        ) as spawn,
    ):
        result = asyncio.run(
            ContainerCLIProvider(
                provider, environment, "trusted-worker@sha256:" + "a" * 64
            ).execute_agent(ExecutionContext("test", "private prompt", tmp_path / "server-project"))
        )
    assert result.success
    args = spawn.call_args_list[0].args
    assert str(tmp_path / "server-project") not in " ".join(args)
    assert "alice-private-key" not in " ".join(args) and "private prompt" not in " ".join(args)
    env = spawn.call_args_list[0].kwargs["env"]
    selected_key = "OPENAI_API_KEY" if provider == "codex" else "ANTHROPIC_API_KEY"
    other_key = "ANTHROPIC_API_KEY" if provider == "codex" else "OPENAI_API_KEY"
    assert env[selected_key] == "alice-private-key" and other_key not in env
    assert "--read-only" in args and "--pull" in args
    if provider == "claude-code":
        assert not any("CLAUDE_CONFIG_DIR=" in arg for arg in args)
        assert "--strict-mcp-config" in args and "--tools" in args
    else:
        assert "--ignore-user-config" in args and "read-only" in args
    assert spawn.call_args_list[1].args[1:3] == ("rm", "--force")
    process.communicate.assert_awaited_once_with(b"private prompt")


def test_evaluations_and_approval_runs_keep_creator_identity(team):
    service, policy, manager, request, calls = team
    status, suite = request(
        "/api/catalog/evaluations",
        {
            "name": "Team suite",
            "data": {
                "target_kind": "agent",
                "target_id": "reviewer",
                "cases": [{"input": "Private case", "contains": ["evidence"], "excludes": []}],
            },
        },
        user="alice",
    )
    assert status == 201
    status, run = request(
        "/api/runs", {"kind": "evaluation", "evaluation_id": suite["id"]}, user="alice"
    )
    assert status == 202 and wait(service, run)["status"] == "completed"
    assert request("/api/evaluation-reports", user="bob")[1]["items"] == []
    assert request("/api/evaluation-reports", user="alice")[1]["items"][0]["owner_id"] == "alice"
    graph = {
        "nodes": [
            {"id": "approve", "name": "Approve", "kind": "approval"},
            {
                "id": "count",
                "name": "Count",
                "kind": "tool",
                "tool_id": "text:wordcount",
                "args": ["one two"],
                "kwargs": {},
            },
        ],
        "edges": [
            {"source": "START", "target": "approve"},
            {"source": "approve", "target": "count"},
        ],
    }
    status, _ = request("/api/workflows", {"name": "Private gate", "graph": graph}, user="alice")
    assert status == 201
    status, run = request(
        "/api/runs",
        {"kind": "workflow", "workflow_id": "private-gate", "task": "Review"},
        user="alice",
    )
    assert status == 202 and wait(service, run)["status"] == "waiting"
    assert request(f"/api/runs/{run['id']}/resume", {"decision": "approve"}, user="bob")[0] == 403
    manager.revoked.add("alice")
    assert request(f"/api/runs/{run['id']}/resume", {"decision": "approve"}, user="owner")[0] == 403
    assert service.run(run["id"])["status"] == "waiting"
    assert len(calls) == 1


def test_downgraded_user_cannot_resolve_execution_credentials(team):
    _, policy, _, _, _ = team
    assert policy.environment("alice", "openai")["OPENAI_API_KEY"] == "user-only-key"
    policy.bindings["alice"] = "viewer"
    with pytest.raises(PermissionError):
        policy.environment("alice", "openai")


def test_team_broker_configuration_rejects_plaintext_and_literal_credentials(tmp_path, monkeypatch):
    from nexus.core.studio_team import load_team_configuration

    monkeypatch.setattr("nexus.core.studio_team.NexusTeamPolicy", lambda **kwargs: object())
    transport = MagicMock()
    monkeypatch.setattr(
        "nexus.adapters.transport.create_transport", lambda kind, **kwargs: transport
    )
    path = tmp_path / "config.json"
    for broker in [
        {"kind": "mqtt", "options": {"tls": False}},
        {"kind": "mqtt", "options": {"tls": True, "password": "literal-secret"}},
        {"kind": "kafka", "options": {"security_protocol": "PLAINTEXT"}},
        {
            "kind": "kafka",
            "options": {"security_protocol": "SASL_SSL", "ssl_check_hostname": False},
        },
    ]:
        path.write_text(json.dumps({"broker": broker}))
        with pytest.raises(ValueError):
            load_team_configuration(path)
    monkeypatch.setenv("TEST_STUDIO_MQTT_PASSWORD", "resolved-secret")
    path.write_text(
        json.dumps(
            {
                "broker": {
                    "kind": "mqtt",
                    "options": {"tls": True, "password": {"env": "TEST_STUDIO_MQTT_PASSWORD"}},
                }
            }
        )
    )
    assert load_team_configuration(path)[1] is transport


def test_busy_team_history_does_not_hide_another_users_own_runs(team):
    service, _, _, request, _ = team
    own = {
        "id": "alice-history",
        "requester_nexus_id": "alice",
        "status": "completed",
        "updated_at": "2026-01-01",
    }
    service._write(service._path("runs", own["id"]), own)
    for index in range(101):
        foreign = {
            "id": f"bob-{index}",
            "requester_nexus_id": "bob",
            "status": "completed",
            "updated_at": "2026-02-01",
        }
        service._write(service._path("runs", foreign["id"]), foreign)
    assert request("/api/runs", user="alice")[1]["items"] == [own]


def test_workbench_routes_enforce_owner_boundaries_roles_and_spoof_protection(team):
    service, _, _, request, _ = team
    status, run = request("/api/runs", {"task": "Alice private history"}, user="alice")
    assert status == 202
    run = wait(service, run)
    session = run["session_id"]
    assert request("/api/workbench/memory")[0] == 401
    assert request("/api/workbench/memory", user="bob")[1]["items"] == []
    for suffix in ["", "/export"]:
        assert request(f"/api/workbench/memory/{session}{suffix}", user="bob")[0] == 403
    assert (
        request(f"/api/workbench/memory/{session}/edit", {"title": "Stolen"}, user="bob")[0] == 403
    )
    assert (
        request(f"/api/workbench/memory/{session}/edit", {"title": "Read only"}, user="viewer")[0]
        == 403
    )
    assert (
        request(
            f"/api/workbench/memory/{session}/edit",
            {"title": "Alice notes", "archived": True},
            user="alice",
        )[0]
        == 200
    )
    assert request("/api/sessions", user="alice")[1]["items"] == []
    settings = request("/api/workbench/controls", user="alice")[1]
    status, saved = request(
        "/api/workbench/controls/save",
        {
            **settings,
            "owner_id": "bob",
            "requester_nexus_id": "bob",
            "_role": "owner",
            "prompt_budget_enabled": True,
            "max_prompt_chars": 2048,
            "summary_max_chars": 300,
        },
        user="alice",
    )
    assert status == 200 and saved["owner_id"] == "alice"
    assert request("/api/workbench/controls", user="bob")[1]["prompt_budget_enabled"] is False
    campaign = {"name": "Private draft", "channels": {"linkedin": {"content": "Evidence only"}}}
    assert request("/api/workbench/campaigns/save", campaign, user="bob")[0] == 403
    status, draft = request(
        "/api/workbench/campaigns/save", {**campaign, "owner_id": "bob"}, user="alice"
    )
    assert status == 201 and draft["owner_id"] == "alice"
    assert request("/api/workbench/campaigns", user="bob")[1]["items"] == []
    assert request("/api/workbench/campaigns/" + draft["id"], user="bob")[0] == 403
    assert (
        request(
            "/api/workbench/campaigns/" + draft["id"] + "/validate", {"revision": 1}, user="owner"
        )[0]
        == 403
    )
    assert request("/api/workbench/campaigns/" + draft["id"], user="owner")[0] == 200
    for collection in ["transcriptions", "decisions", "checkpoint-index"]:
        service._write(
            service._path(collection, "private-result"),
            {"id": "private-result", "owner_id": "alice", "text": "Private"},
        )
    for area in ["transcriptions", "decisions", "checkpoints"]:
        assert request("/api/workbench/" + area, user="bob")[1]["items"] == []
    analytics = request("/api/workbench/analytics", user="bob")[1]
    assert analytics["run_count"] == 0 and analytics["events"] == []
    assert request("/api/workbench/analytics", user="alice")[1]["run_count"] == 1


def test_shared_workbench_uses_personal_api_credentials_and_never_host_fallback(team, monkeypatch):
    service, policy, _, request, _ = team
    monkeypatch.setenv("OPENROUTER_API_KEY", "host-must-not-be-used")
    monkeypatch.setenv("OPENAI_API_KEY", "host-must-not-be-used")
    policy.decision_keys = {
        "alice": {
            "backend": "openrouter",
            "key_env": "ALICE_DECISION_KEY",
            "model": "personal-model",
        }
    }
    monkeypatch.setenv("ALICE_DECISION_KEY", "alice-only")
    credentials = service.workbench.jobs.decision_credentials(
        {"requester_nexus_id": "alice", "backend": "openrouter"}
    )
    assert credentials["api_key"] == "alice-only" and credentials["model"] == "personal-model"
    with pytest.raises(ValueError, match="not configured"):
        service.workbench.jobs.decision_credentials(
            {"requester_nexus_id": "bob", "backend": "openrouter"}
        )
    policy.environment_resolver = lambda identity: ({}, None)
    status, run = request(
        "/api/workbench/transcriptions",
        {
            "filename": "clip.wav",
            "content_base64": base64.b64encode(b"audio").decode(),
            "owner_id": "alice",
        },
        user="bob",
    )
    assert status == 202
    result = wait(service, run)
    assert result["status"] == "failed" and "host-must-not-be-used" not in json.dumps(result)
    assert not list((service.state / "audio-bytes").glob("*"))
    assert request("/api/workbench/transcriptions", user="alice")[1]["items"] == []
    assert (
        request(
            "/api/workbench/agents/delegate",
            {
                "agent_ids": ["reviewer"],
                "strategy": "parallel",
                "task": "Scoped delegation",
                "requester_nexus_id": "alice",
            },
            user="bob",
        )[0]
        == 202
    )


def test_checkpoint_inspection_and_resume_keep_original_owner_credentials(team):
    service, _, _, request, _ = team
    graph = {
        "nodes": [
            {"id": "review", "name": "Review", "agent_id": "reviewer", "prompt": "{task}"},
            {"id": "approve", "name": "Approval", "kind": "approval"},
        ],
        "edges": [
            {"source": "START", "target": "review"},
            {"source": "review", "target": "approve"},
        ],
    }
    service.save_definition("workflows", {"name": "Owned checkpoint", "graph": graph})
    status, run = request(
        "/api/runs",
        {"kind": "workflow", "workflow_id": "owned-checkpoint", "task": "Private workflow"},
        user="alice",
    )
    assert status == 202 and wait(service, run)["status"] == "waiting"
    route = "/api/workbench/checkpoints/" + run["id"]
    snapshot = request(route, user="alice")[1]
    assert request(route, user="bob")[0] == 403
    assert request(route, user="owner")[0] == 200
    payload = {"digest": snapshot["digest"], "decision": "approve"}
    assert request(route + "/resume", payload, user="owner")[0] == 403
    assert request(route + "/resume", payload, user="bob")[0] == 403
    status, resumed = request(route + "/resume", payload, user="alice")
    assert status == 202 and wait(service, resumed)["status"] == "completed"


def test_expanded_roles_and_credentials_remain_identity_scoped(team, monkeypatch):
    from types import SimpleNamespace

    service, policy, manager, request, calls = team
    service.catalog.save(
        "connectors", {"name": "Git", "data": {"type": "github", "repo": "o/r", "enabled": True}}
    )
    with (
        patch(
            "nexus.core.auth.credential_store.get_user_credentials",
            side_effect=lambda identity: SimpleNamespace(github_token_enc="enc-" + identity),
        ),
        patch(
            "nexus.core.auth.credential_crypto.decrypt_secret",
            side_effect=lambda value: value.removeprefix("enc-"),
        ),
    ):
        config = service.catalog.get("connectors", "git")["data"]
        assert service.connections.credentials(config, "alice") == "alice"
        assert service.connections.credentials(config, "bob") == "bob"
    alice_mcp = service.connections.mcp_environment({"env": {}}, "alice")
    bob_mcp = service.connections.mcp_environment({"env": {}}, "bob")
    assert alice_mcp["HOME"] != bob_mcp["HOME"]
    assert alice_mcp["HOME"].startswith(str(service.state))
    assert "OPENAI_API_KEY" not in alice_mcp
    monkeypatch.setenv("HOST_BOT_TOKEN", "service-only")
    with pytest.raises(PermissionError, match="own"):
        service.connections.credentials({"type": "slack", "token_env": "HOST_BOT_TOKEN"}, "alice")
    assert (
        service.connections.credentials(
            {"type": "slack", "token_env": "HOST_BOT_TOKEN", "credential_scope": "service"}, "alice"
        )
        == "service-only"
    )
    assert (
        request(
            "/api/catalog/externalagents",
            {"name": "Remote", "data": {"url": "https://agent.test"}},
            user="alice",
        )[0]
        == 403
    )
    assert (
        request(
            "/api/workbench/storage/save", {"name": "Store", "kind": "filesystem"}, user="alice"
        )[0]
        == 403
    )
    revision = service.versions.list("agents", "reviewer")["current_revision"]
    assert (
        request(
            "/api/workbench/versions/agents/reviewer/activate",
            {"version": revision, "revision": revision},
            user="bob",
        )[0]
        == 403
    )
    assert (
        request(
            "/api/workbench/versions/agents/reviewer/activate",
            {"version": revision, "revision": revision},
            user="alice",
        )[0]
        == 200
    )
    manager.revoked.add("alice")
    with pytest.raises(PermissionError):
        service.connections.credentials(
            {"type": "slack", "token_env": "HOST_BOT_TOKEN", "credential_scope": "service"}, "alice"
        )


def test_signed_channels_map_execution_and_ignore_spoofed_identity(team, monkeypatch):
    service, policy, manager, request, calls = team
    monkeypatch.setenv("CHANNEL_SECRET", "s" * 32)
    service.catalog.save(
        "entrypoints",
        {
            "name": "Telegram",
            "data": {
                "target_kind": "agent",
                "target_id": "reviewer",
                "type": "telegram",
                "enabled": True,
                "token_env": "CHANNEL_SECRET",
                "identity_map": {"tg-alice": "alice"},
                "allowed_channels": ["room"],
            },
        },
    )
    body = {
        "update_id": 1,
        "requester_nexus_id": "owner",
        "message": {"from": {"id": "tg-alice"}, "chat": {"id": "room"}, "text": "Private review"},
    }
    status, result = request(
        "/api/channel-events/telegram", body, extra={"X-Telegram-Bot-Api-Secret-Token": "s" * 32}
    )
    assert status == 200, result
    service.messaging.tick()
    run = service.records("runs")[0]
    assert run["requester_nexus_id"] == "alice"
    assert request("/api/runs/" + run["id"], user="bob")[0] == 403
    manager.revoked.add("alice")
    body["update_id"] = 2
    assert (
        request(
            "/api/channel-events/telegram",
            body,
            extra={"X-Telegram-Bot-Api-Secret-Token": "s" * 32},
        )[0]
        == 403
    )


def test_parity_projects_feedback_and_snapshot_grants_are_scoped_to_nexus_identity(team):
    service, _, manager, request, _ = team
    status, project = request(
        "/api/studio/projects", {"name": "Alice knowledge", "owner_id": "bob"}, user="alice"
    )
    assert status == 201 and project["owner_id"] == "alice"
    assert request("/api/studio/projects/" + project["id"], user="bob")[0] == 403
    assert request("/api/studio/projects", user="bob")[1]["items"] == []
    assert request("/api/studio/projects", {"name": "Forbidden"}, user="viewer")[0] == 403
    service._write(
        service._path("sessions", "private-chat"),
        {
            "id": "private-chat",
            "owner_id": "alice",
            "title": "Private",
            "messages": [{"role": "assistant", "content": "Private response"}],
        },
    )
    status, share = request(
        "/api/studio/shares", {"session_id": "private-chat", "recipients": ["bob"]}, user="alice"
    )
    assert status == 201
    assert request("/api/studio/shares/" + share["id"], user="bob")[0] == 200
    assert request("/api/studio/shares/" + share["id"], user="viewer")[0] == 403
    assert request("/api/studio/shares/" + share["id"])[0] == 401
    assert request("/api/studio/sharing", {"enabled": True}, user="alice")[0] == 403
    assert request("/api/studio/sharing", {"enabled": True}, user="owner")[0] == 200
    status, public = request(
        "/api/studio/shares", {"session_id": "private-chat", "public": True}, user="alice"
    )
    assert status == 201
    snapshot = request("/api/public-shares/" + public["token"])
    assert snapshot[0] == 200 and snapshot[1]["messages"][0]["content"] == "Private response"
    assert request("/api/studio/shares/" + public["id"] + "/revoke", {}, user="bob")[0] == 403
    assert request("/api/studio/shares/" + public["id"] + "/revoke", {}, user="alice")[0] == 200
    assert request("/api/public-shares/" + public["token"])[0] == 404
    manager.revoked.add("bob")
    assert request("/api/studio/shares/" + share["id"], user="bob")[0] == 401

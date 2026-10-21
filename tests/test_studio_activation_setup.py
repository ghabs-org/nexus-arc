"""Activation checks local prerequisites without running user actions."""

import json
from importlib.metadata import PackageNotFoundError

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_activation_setup import local_prerequisites, referenced_tools
from tests.test_studio_build_lifecycle import apply, plan, proposal, seed
from tests.test_studio_team import team as team

IMAGE = "sha256:" + "a" * 64


def worker_build(studio, action="python"):
    name = next(n for n in studio.data_tools.names if n.endswith(":" + action))
    document = proposal()
    document["workflow"]["graph"]["nodes"] = [
        {
            "id": "words",
            "kind": "tool",
            "tool_id": name,
            "kwargs": {"code": "raise AssertionError('USER_ACTION_MUST_NOT_RUN')"}
            if action == "python"
            else {},
        }
    ]
    return seed(studio, document)


@pytest.fixture
def worker(tmp_path, monkeypatch):
    studio = StudioService(
        tmp_path / "workspace",
        data_tool_options={
            "profiles": {"python": {"image": IMAGE, "docker_context": "desktop-linux"}}
        },
    )
    calls = []
    monkeypatch.setattr(
        "nexus.core.studio_activation_setup.shutil.which", lambda _, **kwargs: "/installed/docker"
    )

    def inspect(code, **kwargs):
        assert "USER_ACTION_MUST_NOT_RUN" not in code
        calls.append(kwargs)
        return {"stdout": '{"missing":[]}', "files": []}

    monkeypatch.setattr(studio.data_tools.sandbox, "execute", inspect)
    try:
        yield studio, calls
    finally:
        studio.close()


@pytest.mark.parametrize(
    "kind,dialect,missing",
    [
        ("sql", "postgresql", "psycopg2-binary"),
        ("sql", "mysql", "PyMySQL"),
        ("sql", "mariadb", "PyMySQL"),
        ("sql", "mssql", "pymssql"),
        ("sql", "oracle", "oracledb"),
        ("mongodb", None, "pymongo"),
        ("mcp", None, "mcp"),
        ("bedrock", None, "boto3"),
        ("openapi", None, "referencing"),
        ("slack", None, "slack-sdk"),
    ],
)
def test_missing_connector_software_is_reported_without_loading_sdk(
    worker, monkeypatch, kind, dialect, missing
):
    studio, calls = worker

    def installed(name):
        if name == missing:
            raise PackageNotFoundError(name)
        return "1.0"

    monkeypatch.setattr("nexus.core.studio_activation_setup.version", installed)
    result = local_prerequisites(
        studio, "connectors:test", {"data": {"type": kind, "dialect": dialect}}
    )
    assert result["issues"] == ["optional_dependency_missing:" + missing]
    assert not calls


def test_passive_review_never_runs_workers_and_activation_rechecks(worker):
    studio, calls = worker
    build = worker_build(studio)
    assert studio.resource_lifecycle.inspect_build(build["id"])["components"]
    assert not calls
    reviewed = plan(studio, build, "activate")
    assert not reviewed["errors"] and len(calls) == 1
    assert apply(studio, build, reviewed)["status"] == "active"
    assert len(calls) == 2 and not studio.records("runs")


def test_missing_worker_profile_blocks_activation_and_preserves_saved_build(tmp_path):
    studio = StudioService(tmp_path)
    try:
        build = worker_build(studio)
        reviewed = plan(studio, build, "activate")
        assert any("worker_profile_missing:python" in e["reason"] for e in reviewed["errors"])
        with pytest.raises(ValueError, match="readiness"):
            apply(studio, build, reviewed)
        assert studio.builder.get(build["id"]) == build
        assert not studio.records("runs")
    finally:
        studio.close()


@pytest.mark.parametrize("outcome", ["missing", "malformed", "failed"])
def test_failed_worker_checks_block_activation_without_persisting_diagnostics(
    worker, monkeypatch, outcome
):
    studio, _ = worker

    def probe(*args, **kwargs):
        if outcome == "failed":
            raise ValueError("SECRET_DIAGNOSTIC_DO_NOT_PERSIST")
        return {
            "stdout": '{"missing":["required software"]}' if outcome == "missing" else "not json",
            "files": [],
        }

    monkeypatch.setattr(studio.data_tools.sandbox, "execute", probe)
    build = worker_build(studio)
    reviewed = plan(studio, build, "activate")
    assert any("worker_prerequisites_missing:python" in e["reason"] for e in reviewed["errors"])
    assert "SECRET_DIAGNOSTIC" not in json.dumps(reviewed)
    with pytest.raises(ValueError):
        apply(studio, build, reviewed)
    assert studio.builder.get(build["id"]) == build


def test_changed_worker_profile_requires_fresh_review_before_any_new_probe(worker):
    studio, calls = worker
    build = worker_build(studio)
    reviewed = plan(studio, build, "activate")
    studio.data_tools.sandbox.profiles["python"]["timeout"] = 45
    with pytest.raises(ValueError, match="setup changed"):
        apply(studio, build, reviewed)
    assert len(calls) == 1 and studio.builder.get(build["id"]) == build


def test_removed_image_or_software_at_apply_is_rechecked(worker, monkeypatch):
    studio, _ = worker
    build = worker_build(studio)
    reviewed = plan(studio, build, "activate")
    monkeypatch.setattr(
        studio.data_tools.sandbox,
        "execute",
        lambda *a, **kw: {"stdout": '{"missing":["image/software"]}', "files": []},
    )
    with pytest.raises(ValueError, match="prerequisites changed"):
        apply(studio, build, reviewed)
    assert studio.builder.get(build["id"]) == build


def test_changed_selected_model_invalidates_review(worker, tmp_path):
    studio, calls = worker
    model = tmp_path / "model.pt"
    model.write_bytes(b"original")
    studio.data_tools.sandbox.profiles["vision"] = {"image": IMAGE, "model_path": str(model)}
    build = worker_build(studio, "detect_objects")
    reviewed = plan(studio, build, "activate")
    model.write_bytes(b"replacement")
    with pytest.raises(ValueError, match="setup changed"):
        apply(studio, build, reviewed)
    assert len(calls) == 1


def test_nested_handlers_and_agent_toolsets_are_checked_as_typed_tool_references():
    body = {
        "nodes": [{"tool_id": "selected:tool", "body": {"nodes": [{"tool_id": "nested:tool"}]}}],
        "on_error": {"nodes": [{"tool_id": "handler:tool"}]},
        "interop_exit_handlers": [{"body": {"nodes": [{"tool_id": "exit:tool"}]}}],
    }
    assert referenced_tools("workflows", {"graph": body}) == [
        "exit:tool",
        "handler:tool",
        "nested:tool",
        "selected:tool",
    ]
    assert referenced_tools(
        "agents", {"spec": {"tool_ids": ["agent:tool"], "instructions": "ignored:tool"}}
    ) == ["agent:tool"]
    assert referenced_tools("toolsets", {"data": {"tools": ["toolset:tool"]}}) == ["toolset:tool"]


def test_sdk_version_change_invalidates_review_without_connecting(worker, monkeypatch):
    import sys

    studio, calls = worker
    installed = ["1.0"]
    monkeypatch.setattr("nexus.core.studio_activation_setup.version", lambda _: installed[0])
    build = seed(
        studio,
        proposal(
            resources=[
                {
                    "kind": "connectors",
                    "id": "server",
                    "name": "Server",
                    "data": {"type": "mcp", "command": sys.executable, "actions": ["discover"]},
                }
            ]
        ),
    )
    key = next(
        "connectors:" + item["id"]
        for item in build["proposal"]["resources"]
        if item["kind"] == "connectors"
    )
    reviewed = plan(studio, build, "activate", enable=[key])
    assert not reviewed["errors"]
    installed[0] = "2.0"
    with pytest.raises(ValueError, match="setup changed"):
        apply(studio, build, reviewed)
    assert not calls


def test_completed_activation_receipt_never_repeats_worker_probes(worker, monkeypatch):
    studio, calls = worker
    build = worker_build(studio)
    reviewed = plan(studio, build, "activate")
    first = apply(studio, build, reviewed)

    def forbidden(*args, **kwargs):
        raise AssertionError("Receipt replay probed again")

    monkeypatch.setattr(studio.data_tools.sandbox, "execute", forbidden)
    assert apply(studio, build, reviewed) == first and len(calls) == 2


def test_connector_tool_nodes_include_connector_prerequisites():
    from nexus.core.studio_resource_lifecycle import dependencies

    assert dependencies(
        "workflows",
        {"graph": {"nodes": [{"kind": "tool", "tool_id": "connector:workspace:database"}]}},
    ) == ["connectors:database"]


def test_missing_model_does_not_start_worker(worker):
    studio, calls = worker
    studio.data_tools.sandbox.profiles["vision"] = {"image": IMAGE}
    reviewed = plan(studio, worker_build(studio, "detect_objects"), "activate")
    assert any("worker_model_missing:vision" in item["reason"] for item in reviewed["errors"])
    assert not calls


def test_revoked_execution_access_does_not_start_probe(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from nexus.core.studio_team import NexusTeamPolicy

    policy = NexusTeamPolicy(
        project_key="test",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login",
        bindings={"alice": "owner"},
        manager=SimpleNamespace(has_project_access=lambda *a: False),
    )
    studio = StudioService(
        tmp_path / "workspace",
        state_dir=tmp_path / "private",
        team_policy=policy,
        data_tool_options={"profiles": {"python": {"image": IMAGE}}},
    )
    try:
        name = next(n for n in studio.data_tools.names if n.endswith(":python"))

        def forbidden(*args, **kwargs):
            raise AssertionError("Revoked user started a worker")

        monkeypatch.setattr(studio.data_tools.sandbox, "execute", forbidden)
        result = studio.resource_lifecycle.readiness(
            "workflows:test",
            {"graph": {"nodes": [{"tool_id": name}]}},
            {"nexus_id": "alice", "role": "owner"},
            probe_workers=True,
        )
        assert "execution_access_revoked" in result["issues"]
    finally:
        studio.close()


def test_activation_http_keeps_owner_review_and_reports_missing_setup(team):
    studio, _, _, request, _ = team
    tool = next(n for n in studio.data_tools.names if n.endswith(":python"))
    value = proposal()
    value["workflow"]["graph"]["nodes"] = [
        {
            "id": "words",
            "kind": "tool",
            "tool_id": tool,
            "kwargs": {"code": "raise AssertionError('not executed')"},
        }
    ]
    build = seed(studio, value, owner="owner")
    path = f"/api/studio/builds/{build['id']}"
    payload = {"revision": build["revision"], "action": "activate"}
    assert request(path + "/plan", payload, user="viewer")[0] == 403
    status, reviewed = request(path + "/plan", payload, user="owner")
    assert status == 200 and any(
        "worker_profile_missing:python" in item["reason"] for item in reviewed["errors"]
    )
    assert (
        request(
            path + "/transition",
            {"plan_id": reviewed["id"], "digest": reviewed["digest"]},
            user="owner",
        )[0]
        == 400
    )
    assert studio.builder.get(build["id"]) == build
    assert not studio.records("runs")


def test_chart_activation_requires_validated_renderer_resource_allowance(worker):
    studio, _ = worker
    studio.data_tools.sandbox.profiles["data"] = {"image": IMAGE}
    build = worker_build(studio, "chart")
    reviewed = plan(studio, build, "activate")
    assert any("worker_chart_limits_required" in item["reason"] for item in reviewed["errors"])
    studio.data_tools.sandbox.profiles["data"].update(pids_limit=256, open_files=256)
    assert not plan(studio, build, "activate")["errors"]

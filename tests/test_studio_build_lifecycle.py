"""Reviewed lifecycle uses real resources, shared release journals and launch gates."""

import copy
import sys

import pytest

from nexus.core.studio import StudioService


def proposal(content="Use local evidence", resources=None):
    return {
        "name": "Local lifecycle",
        "summary": "A reviewed local tool workflow.",
        "assumptions": [],
        "agents": [],
        "workflow": {
            "id": "count",
            "name": "Count",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "words",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": {"ref": "input"}},
                    }
                ],
                "output_mapping": {"ref": "nodes.words"},
            },
        },
        "resources": [
            {"kind": "prompts", "id": "evidence", "name": "Evidence", "data": {"content": content}},
            *(resources or []),
        ],
    }


def connector(identity="remote"):
    return {
        "kind": "connectors",
        "id": identity,
        "name": identity,
        "data": {
            "type": "http",
            "url": "https://example.invalid/data",
            "token_env": "LIFECYCLE_FIXTURE_TOKEN",
        },
    }


def seed(studio, value=None, identity="lifecycle1234", owner=None):
    normalized = studio.builder.normalize(value or proposal(), identity, "codex")
    studio._write(
        studio._path("builds", identity),
        {
            "id": identity,
            "owner_id": owner,
            "status": "draft",
            "revision": 1,
            "provider": "codex",
            "model": "",
            "proposal": normalized,
            "messages": [],
        },
    )
    return studio.builder.apply(identity, {"revision": 1, "requester_nexus_id": owner})


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def plan(studio, build, action, **extra):
    return studio.resource_lifecycle.plan_build(
        build["id"], {"revision": build["revision"], "action": action, **extra}
    )


def apply(studio, build, reviewed):
    return studio.resource_lifecycle.transition_build(
        build["id"], {"plan_id": reviewed["id"], "digest": reviewed["digest"]}
    )


def test_review_activation_deactivation_and_receipt_are_durable(studio):
    from tests.test_studio_workflow_v2 import wait

    build = seed(studio)
    initial = studio.resource_lifecycle.inspect_build(build["id"])
    assert len(initial["components"]) == 2 and initial["live_health"] == "not_checked"
    reviewed = plan(studio, build, "deactivate")
    assert not reviewed["errors"]
    assert studio.builder.get(build["id"]) == build
    result = apply(studio, build, reviewed)
    assert result["status"] == "inactive"
    assert apply(studio, build, reviewed) == result
    with pytest.raises(ValueError, match="inactive"):
        studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": build["proposal"]["workflow"]["id"],
                "task": "one two",
            }
        )
    build = studio.builder.get(build["id"])
    reviewed = plan(studio, build, "activate")
    assert not reviewed["errors"]
    apply(studio, build, reviewed)
    result = wait(
        studio,
        studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": build["proposal"]["workflow"]["id"],
                "task": "one two",
            }
        ),
    )
    assert result["output"] == "2"


def test_credentials_and_explicit_enabling_are_separate_and_rechecked(studio, monkeypatch):
    build = seed(studio, proposal(resources=[connector()]))
    identity = build["proposal"]["resources"][1]["id"]
    key = "connectors:" + identity
    reviewed = plan(studio, build, "activate", enable=[key])
    assert any(item["reason"] == "missing_credentials" for item in reviewed["errors"])
    with pytest.raises(ValueError, match="every"):
        apply(studio, build, reviewed)
    assert not studio.catalog.get("connectors", identity)["data"]["enabled"]
    monkeypatch.setenv("LIFECYCLE_FIXTURE_TOKEN", "synthetic-local-token")
    reviewed = plan(studio, build, "activate")
    assert any(item["reason"] == "explicit_enable_required" for item in reviewed["errors"])
    reviewed = plan(studio, build, "activate", enable=[key])
    assert not reviewed["errors"]
    assert "synthetic-local-token" not in str(reviewed)
    monkeypatch.delenv("LIFECYCLE_FIXTURE_TOKEN")
    with pytest.raises(ValueError, match="prerequisites"):
        apply(studio, build, reviewed)
    monkeypatch.setenv("LIFECYCLE_FIXTURE_TOKEN", "synthetic-local-token")
    apply(studio, build, reviewed)
    assert studio.catalog.get("connectors", identity)["data"]["enabled"]
    assert (
        next(
            item
            for item in studio.resource_lifecycle.inspect_build(build["id"])["components"]
            if item["key"] == key
        )["status"]
        == "available"
    )


def test_review_rejects_file_and_build_drift(studio):
    build = seed(studio)
    reviewed = plan(studio, build, "deactivate")
    path = studio.root / "workflows" / (build["proposal"]["workflow"]["id"] + ".yaml")
    path.write_text(path.read_text() + "\n# manual edit\n")
    with pytest.raises(ValueError, match="changed"):
        apply(studio, build, reviewed)
    reviewed = plan(studio, build, "deactivate")
    studio.builder.reopen(build["id"], {"revision": build["revision"]})
    with pytest.raises(ValueError, match="changed"):
        apply(studio, build, reviewed)


def test_rollback_restores_selected_release_and_keeps_new_exposures_disabled(studio):
    build = seed(studio)
    first_release = build["release_id"]
    draft = studio.builder.reopen(build["id"], {"revision": 1})
    modified = copy.deepcopy(draft["proposal"])
    modified["resources"][0]["data"]["content"] = "Revised content"
    modified["resources"].append(connector())
    build = studio.builder.apply(build["id"], {"revision": draft["revision"], "proposal": modified})
    reviewed = plan(studio, build, "rollback", release=first_release)
    assert any("/data/content" in item["paths"] for item in reviewed["changes"])
    result = apply(studio, build, reviewed)
    assert result["status"] == "inactive"
    updated = studio.builder.get(build["id"])
    assert updated["revision"] == build["revision"] + 1
    assert updated["proposal"]["resources"][0]["data"]["content"] == "Use local evidence"
    identity = build["proposal"]["resources"][1]["id"]
    assert not studio.catalog.get("connectors", identity)["data"]["enabled"]


def test_transition_failure_restores_resources_and_build_receipt(studio, monkeypatch):
    monkeypatch.setenv("LIFECYCLE_FIXTURE_TOKEN", "synthetic-local-token")
    build = seed(studio, proposal(resources=[connector("first"), connector("second")]))
    enable = [
        "connectors:" + r["id"] for r in build["proposal"]["resources"] if r["kind"] == "connectors"
    ]
    reviewed = plan(studio, build, "activate", enable=enable)
    before = {key: studio.resource_lifecycle.current(key) for key in enable}
    save = studio.catalog.save
    calls = 0

    def interrupted(kind, payload):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("Synthetic disk interruption")
        return save(kind, payload)

    monkeypatch.setattr(studio.catalog, "save", interrupted)
    with pytest.raises(OSError, match="Synthetic"):
        apply(studio, build, reviewed)
    assert {key: studio.resource_lifecycle.current(key) for key in enable} == before
    assert studio.builder.get(build["id"]) == build
    assert "result" not in studio._read(studio._path("resource-plans", reviewed["id"]))


def test_dependency_graph_detects_missing_and_cyclic_workflows_before_mutation(studio):
    document = {
        "format": "nexus-studio-config/v1",
        "resources": [],
        "definitions": [
            {
                "collection": "workflows",
                "id": "parent",
                "document": {
                    "kind": "Workflow",
                    "graph": {
                        "nodes": [{"id": "child", "kind": "workflow", "workflow_id": "missing"}]
                    },
                },
            }
        ],
    }
    assert studio.resource_lifecycle.graph(document)["errors"] == [
        {"component": "workflows:missing", "reason": "missing_dependency"}
    ]
    document["definitions"][0]["document"]["graph"]["nodes"][0]["workflow_id"] = "parent"
    assert studio.resource_lifecycle.graph(document)["errors"] == [
        {"component": "workflows:parent", "reason": "dependency_cycle"}
    ]


def test_reviewed_save_requires_activation_and_exact_unchanged_proposal(studio):
    normalized = studio.builder.normalize(proposal(), "review1234", "codex")
    draft = {
        "id": "review1234",
        "owner_id": None,
        "status": "draft",
        "revision": 1,
        "provider": "codex",
        "model": "",
        "proposal": normalized,
        "messages": [],
    }
    studio._write(studio._path("builds", draft["id"]), draft)
    reviewed = studio.builder.review(draft["id"], {"revision": 1})
    assert not studio.definitions("workflows")
    modified = copy.deepcopy(normalized)
    modified["summary"] = "Changed after review"
    with pytest.raises(PermissionError, match="exact"):
        studio.builder.apply(
            draft["id"],
            {
                "revision": 1,
                "proposal": modified,
                "review_id": reviewed["id"],
                "digest": reviewed["digest"],
            },
        )
    saved = studio.builder.apply(
        draft["id"], {"revision": 1, "review_id": reviewed["id"], "digest": reviewed["digest"]}
    )
    assert saved["lifecycle_status"] == "inactive"
    with pytest.raises(ValueError, match="inactive"):
        studio.builder.test(saved["id"], {"task": "one two"})
    apply(studio, saved, plan(studio, saved, "activate"))
    assert studio.builder.test(saved["id"], {"task": "one two"})["status"] in {"queued", "running"}


def test_managed_manual_edit_cannot_run_under_old_activation(studio):
    build = seed(studio)
    apply(studio, build, plan(studio, build, "activate"))
    current = studio.definition("workflows", build["proposal"]["workflow"]["id"])
    current["graph"]["nodes"][0]["kwargs"]["text"] = "unreviewed"
    import yaml

    path = studio.root / "workflows" / (build["proposal"]["workflow"]["id"] + ".yaml")
    path.write_text(yaml.safe_dump(current))
    with pytest.raises(ValueError, match="changed since activation"):
        studio.builder.test(build["id"], {"task": "one two"})
    reviewed = plan(studio, studio.builder.get(build["id"]), "activate")
    assert any(item["reason"] == "unreviewed_changes" for item in reviewed["errors"])
    with pytest.raises(ValueError, match="every"):
        apply(studio, build, reviewed)


def test_individual_enable_cannot_bypass_build_review_or_consume_schedule(studio, monkeypatch):
    from datetime import UTC, datetime

    from nexus.core.studio_schedules import StudioSchedules

    monkeypatch.setenv("LIFECYCLE_FIXTURE_TOKEN", "synthetic-fixture-token-long-enough")
    studio.save_definition(
        "agents", {"id": "existing", "name": "Existing", "instructions": "Review"}
    )
    build = seed(
        studio,
        proposal(
            resources=[
                connector(),
                {
                    "kind": "entrypoints",
                    "id": "incoming",
                    "name": "Incoming",
                    "data": {
                        "target_kind": "agent",
                        "target_id": "existing",
                        "token_env": "LIFECYCLE_FIXTURE_TOKEN",
                    },
                },
                {
                    "kind": "schedules",
                    "id": "later",
                    "name": "Later",
                    "data": {
                        "target_kind": "agent",
                        "target_id": "existing",
                        "task": "Do not dispatch",
                        "run_at": "2100-01-01T12:00:00+00:00",
                    },
                },
            ]
        ),
    )
    items = {
        item["kind"]: studio.catalog.get(item["kind"], item["id"])
        for item in build["proposal"]["resources"]
        if item["kind"] != "prompts"
    }
    for kind, item in items.items():
        studio.catalog.save(kind, {**item, "data": {**item["data"], "enabled": True}})
    with pytest.raises(ValueError, match="inactive"):
        studio.operations.call_connector(items["connectors"]["id"], {})
    with pytest.raises(ValueError, match="inactive"):
        studio.operations.trigger_entrypoint(items["entrypoints"]["id"], {"task": "Do not launch"})
    assert not studio.operations.authorize_entrypoint(
        items["entrypoints"]["id"], "synthetic-fixture-token-long-enough"
    )
    # The target is independently available, so the schedule's own marker must block it.
    StudioSchedules(studio).tick(datetime(2100, 1, 2, tzinfo=UTC))
    assert not studio.records("runs") and not studio.records("schedule-occurrences")
    assert not studio.records("schedule-state")


def test_reviewed_refinement_retires_removed_connections_atomically(studio, monkeypatch):
    monkeypatch.setenv("LIFECYCLE_FIXTURE_TOKEN", "synthetic-local-token")
    build = seed(studio, proposal(resources=[connector()]))
    key = "connectors:" + build["proposal"]["resources"][1]["id"]
    apply(studio, build, plan(studio, build, "activate", enable=[key]))
    draft = studio.builder.reopen(build["id"], {"revision": 1})
    value = copy.deepcopy(draft["proposal"])
    value["resources"] = value["resources"][:1]
    review = studio.builder.review(build["id"], {"revision": draft["revision"], "proposal": value})
    assert any(change["resource"] == key for change in review["changes"])
    assert studio.resource_lifecycle.current(key)["data"]["enabled"]
    studio.builder.apply(
        build["id"],
        {
            "revision": draft["revision"],
            "proposal": value,
            "review_id": review["id"],
            "digest": review["digest"],
        },
    )
    assert not studio.resource_lifecycle.current(key)["data"]["enabled"]
    assert not studio.resource_lifecycle.marker(key)["active"]


def test_activation_cannot_borrow_an_inactive_shared_dependency(studio):
    first = seed(studio)
    apply(studio, first, plan(studio, first, "deactivate"))
    value = proposal()
    value["workflow"]["graph"] = {
        "version": 2,
        "nodes": [
            {"id": "child", "kind": "workflow", "workflow_id": first["proposal"]["workflow"]["id"]}
        ],
        "output_mapping": {"ref": "nodes.child"},
    }
    second = seed(studio, value, identity="secondbuild123")
    review = plan(studio, second, "activate")
    assert {
        "component": "workflows:" + first["proposal"]["workflow"]["id"],
        "reason": "inactive",
    } in review["errors"]
    with pytest.raises(ValueError, match="every"):
        apply(studio, second, review)


def test_launch_rechecks_activation_after_snapshot_before_queue(studio, monkeypatch):
    build = seed(studio)
    apply(studio, build, plan(studio, build, "activate"))
    from nexus.core import studio_agent_settings

    pin = studio_agent_settings.pin_agents

    def concurrent_deactivation(service, payload):
        pin(service, payload)
        apply(service, build, plan(service, build, "deactivate"))

    monkeypatch.setattr(studio_agent_settings, "pin_agents", concurrent_deactivation)
    with pytest.raises(ValueError, match="inactive"):
        studio.builder.test(build["id"], {"task": "must not queue"})
    assert not studio.records("runs")


def test_mcp_environment_mapping_is_required_for_activation(studio, monkeypatch):
    monkeypatch.delenv("LIFECYCLE_MCP_SECRET", raising=False)
    record = studio.catalog.save(
        "connectors",
        {
            "id": "mapped",
            "name": "Mapped MCP",
            "data": {
                "type": "mcp",
                "command": sys.executable,
                "env": {"SERVER_TOKEN": "LIFECYCLE_MCP_SECRET"},
            },
        },
    )
    readiness = studio.resource_lifecycle.readiness("connectors:mapped", record, None)
    assert readiness["missing_environment"] == ["LIFECYCLE_MCP_SECRET"]
    monkeypatch.setenv("LIFECYCLE_MCP_SECRET", "synthetic")
    assert studio.resource_lifecycle.readiness("connectors:mapped", record, None)["ready"]


def test_every_configuration_resource_can_be_authored_refined_and_activated(studio, monkeypatch):
    from nexus.core.studio_catalog import KINDS

    value = proposal(
        resources=[
            {
                "kind": "skills",
                "id": "skills",
                "name": "Evidence skill",
                "data": {"content": "Cite evidence"},
            },
            {
                "kind": "models",
                "id": "model",
                "name": "Model",
                "data": {"provider": "openai", "model": "fixture-model"},
            },
            {
                "kind": "decisionmodels",
                "id": "decision-model",
                "name": "Decision model",
                "data": {
                    "backend": "openrouter",
                    "model": "anthropic/claude-3.5-sonnet",
                    "timeout_seconds": 15,
                },
            },
            {
                "kind": "toolsets",
                "id": "tools",
                "name": "Local tools",
                "data": {"tools": ["text:wordcount"]},
            },
            connector(),
            {
                "kind": "entrypoints",
                "id": "incoming",
                "name": "Incoming",
                "data": {
                    "target_kind": "workflow",
                    "target_id": "count",
                    "token_env": "LIFECYCLE_FIXTURE_TOKEN",
                },
            },
            {
                "kind": "schedules",
                "id": "later",
                "name": "Later",
                "data": {
                    "target_kind": "workflow",
                    "target_id": "count",
                    "task": "Future fixture",
                    "run_at": "2100-01-01T12:00:00+00:00",
                },
            },
            {
                "kind": "evaluations",
                "id": "quality",
                "name": "Quality",
                "data": {
                    "target_kind": "agent",
                    "target_id": "reviewer",
                    "cases": [{"input": "one two", "contains": ["2"]}],
                },
            },
            {
                "kind": "datasets",
                "id": "quality-fixtures",
                "name": "Quality fixtures",
                "data": {"cases": [{"input": "one two", "contains": ["2"]}]},
            },
            {
                "kind": "evaluators",
                "id": "evidence-judge",
                "name": "Evidence judge",
                "data": {
                    "provider": "codex",
                    "rubric": "Assess evidence",
                    "choices": {"Pass": 1, "Fail": 0},
                },
            },
            {
                "kind": "experiments",
                "id": "quality-experiment",
                "name": "Quality experiment",
                "data": {
                    "evaluation_id": "quality",
                    "evaluation_revision": 1,
                    "runsPerExample": 2,
                    "variants": [{"name": "Baseline"}],
                },
            },
            {
                "kind": "externalagents",
                "id": "remote-agent",
                "name": "Remote agent",
                "data": {
                    "url": "https://example.invalid/a2a",
                    "token_env": "LIFECYCLE_FIXTURE_TOKEN",
                },
            },
        ]
    )
    value["agents"] = [
        {
            "id": "reviewer",
            "name": "Reviewer",
            "description": "Review supplied evidence",
            "instructions": "Use evidence",
            "skill_ids": ["skills"],
            "model_id": "model",
            "toolset_ids": ["tools"],
            "max_model_calls": 2,
            "output_schema": {"type": "integer"},
        }
    ]
    build = seed(studio, value)
    assert build["lifecycle_status"] == "inactive"
    inspection = studio.resource_lifecycle.inspect_build(build["id"])
    assert {item["key"].split(":")[0] for item in inspection["components"]} == {
        "agents",
        "workflows",
        *set(KINDS) - {"artifacts"},
    }
    assert len(inspection["dependencies"]) == 7
    draft = studio.builder.reopen(build["id"], {"revision": 1})
    refined = copy.deepcopy(draft["proposal"])
    for item in refined["resources"]:
        item["description"] = "Refined in one reviewed release"
    reviewed = studio.builder.review(
        build["id"], {"revision": draft["revision"], "proposal": refined}
    )
    saved = studio.builder.apply(
        build["id"],
        {
            "revision": draft["revision"],
            "proposal": refined,
            "review_id": reviewed["id"],
            "digest": reviewed["digest"],
        },
    )
    for item in saved["proposal"]["resources"]:
        assert (
            studio.catalog.get(item["kind"], item["id"])["description"]
            == "Refined in one reviewed release"
        )
    monkeypatch.setenv("LIFECYCLE_FIXTURE_TOKEN", "synthetic-fixture-key")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-fixture-key")
    enable = [
        item["key"]
        for item in studio.resource_lifecycle.inspect_build(build["id"])["components"]
        if item["key"].split(":")[0] in {"connectors", "entrypoints", "schedules", "externalagents"}
    ]
    activate = plan(studio, saved, "activate", enable=enable)
    assert not activate["errors"], activate
    apply(studio, saved, activate)
    assert all(
        item["status"] == "available"
        for item in studio.resource_lifecycle.inspect_build(build["id"])["components"]
    )
    # Configuration readiness is not proof that the remote example.invalid services are live.
    assert studio.resource_lifecycle.inspect_build(build["id"])["live_health"] == "not_checked"

"""Whole-build named workflow authoring, preflight, releases and pinned execution."""

import copy

import pytest

from nexus.core.studio import StudioService
from tests.test_studio_build_lifecycle import apply, plan, proposal, seed
from tests.test_studio_workflow_v2 import wait


def bundle():
    value = proposal()
    value["workflow"] = {
        "id": "parent",
        "name": "Parent",
        "graph": {
            "version": 2,
            "nodes": [{"id": "child", "kind": "workflow", "workflow_id": "middle"}],
            "output_mapping": {"ref": "nodes.child"},
        },
    }
    value["workflows"] = [
        {
            "id": "middle",
            "name": "Middle",
            "graph": {
                "version": 2,
                "nodes": [{"id": "child", "kind": "workflow", "workflow_id": "leaf"}],
                "output_mapping": {"ref": "nodes.child"},
            },
        },
        {
            "id": "leaf",
            "name": "Leaf",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "count",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "kwargs": {"text": {"ref": "input"}},
                    }
                ],
                "output_mapping": {"ref": "nodes.count"},
            },
        },
    ]
    return value


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def save_edit(studio, build, value):
    draft = studio.builder.reopen(build["id"], {"revision": build["revision"]})
    review = studio.builder.review(build["id"], {"revision": draft["revision"], "proposal": value})
    return studio.builder.apply(
        build["id"],
        {
            "revision": draft["revision"],
            "proposal": value,
            "review_id": review["id"],
            "digest": review["digest"],
        },
    )


def test_new_named_children_review_activate_run_refine_and_rollback(studio):
    value = bundle()
    value["resources"].append(
        {
            "kind": "evaluations",
            "id": "child-check",
            "name": "Child check",
            "data": {
                "target_kind": "workflow",
                "target_id": "leaf",
                "cases": [{"input": "one two", "contains": ["2"]}],
            },
        }
    )
    build = seed(studio, value)
    assert len(studio.definitions("workflows")) == 3
    assert studio.builder.normalize(build["proposal"], build["id"], "codex") == build["proposal"]
    saved = build["release_id"]
    view = studio.resource_lifecycle.inspect_build(build["id"])
    assert len(view["dependencies"]) == 3
    assert all(c["status"] == "inactive" for c in view["components"])
    reviewed = plan(studio, build, "activate")
    assert not reviewed["errors"]
    apply(studio, build, reviewed)
    assert (
        wait(studio, studio.builder.test(build["id"], {"task": "one two three"}))["output"] == "3"
    )
    edited = copy.deepcopy(build["proposal"])
    edited["workflows"][1]["graph"]["nodes"][0]["kwargs"]["text"] = "replacement words"
    build = save_edit(studio, studio.builder.get(build["id"]), edited)
    apply(studio, build, plan(studio, build, "activate"))
    assert (
        wait(studio, studio.builder.test(build["id"], {"task": "one two three"}))["output"] == "2"
    )
    apply(studio, build, plan(studio, build, "rollback", release=saved))
    restored = studio.builder.get(build["id"])
    assert restored["proposal"]["workflows"][1]["graph"]["nodes"][0]["kwargs"]["text"] == {
        "ref": "input"
    }
    assert all(
        c["status"] == "inactive"
        for c in studio.resource_lifecycle.inspect_build(build["id"])["components"]
    )


@pytest.mark.parametrize(
    "failure", ["duplicate", "cycle", "missing", "two_bodies", "too_many", "legacy_child"]
)
def test_complete_preflight_rejects_bad_bundle_without_writes(studio, failure):
    value = bundle()
    if failure == "duplicate":
        value["workflows"][0]["id"] = "parent"
    elif failure == "cycle":
        value["workflows"][0]["graph"]["nodes"][0]["workflow_id"] = "parent"
    elif failure == "missing":
        value["workflows"][0]["graph"]["nodes"][0]["workflow_id"] = "absent"
    elif failure == "two_bodies":
        value["workflows"][0]["steps"] = []
    elif failure == "too_many":
        value["workflows"] *= 7
    else:
        value["workflows"][1]["graph"] = {"nodes": [{"id": "old", "kind": "approval"}], "edges": []}
    with pytest.raises((ValueError, FileNotFoundError)):
        studio.builder.normalize(value, "bundlefixture123", "codex")
    assert studio.definitions("workflows") == []
    assert studio.records("configuration-releases") == []
    assert studio.catalog.list("prompts") == []


def test_paused_parent_keeps_original_child_revision_after_edit(studio):
    value = bundle()
    value["workflow"]["graph"]["nodes"].insert(0, {"id": "gate", "kind": "approval"})
    value["workflow"]["graph"]["nodes"][1]["depends_on"] = ["gate"]
    build = seed(studio, value)
    apply(studio, build, plan(studio, build, "activate"))
    paused = wait(studio, studio.builder.test(build["id"], {"task": "one two three"}), "waiting")
    assert paused["status"] == "waiting"
    changed = copy.deepcopy(build["proposal"])
    changed["workflows"][1]["graph"]["nodes"][0]["kwargs"]["text"] = "replacement"
    build = save_edit(studio, studio.builder.get(build["id"]), changed)
    apply(studio, build, plan(studio, build, "activate"))
    resumed = wait(studio, studio.graph.resume(paused["id"], "approve"))
    assert resumed["output"] == "3"


def test_child_only_bundle_and_literal_input_identifiers(studio):
    value = bundle()
    value["workflow"]["graph"]["nodes"][0]["input"] = {
        "literal": {"workflow_id": "leaf", "agent_id": "leaf"}
    }
    normalized = studio.builder.normalize(value, "bundlefixture123", "codex")
    literal = normalized["workflow"]["graph"]["nodes"][0]["input"]
    assert literal == {"literal": {"workflow_id": "leaf", "agent_id": "leaf"}}
    value["workflow"] = None
    build = seed(studio, value)
    assert len(studio.definitions("workflows")) == 2
    assert build["proposal"]["workflow"] is None
    with pytest.raises(ValueError, match="no workflow"):
        studio.builder.test(build["id"], {"task": "test"})


def test_configuration_cli_validator_rejects_named_cycle_before_any_plan(studio):
    value = studio.builder.normalize(bundle(), "bundlefixture123", "codex")
    document = {
        "format": "nexus-studio-config/v1",
        "resources": value.get("resources", []),
        "definitions": [
            {"collection": kind, "id": identity, "document": doc}
            for kind, identity, doc in studio.builder.documents(value)
        ],
    }
    leaf = next(i for i in document["definitions"] if i["id"].endswith("-leaf"))
    leaf["document"]["graph"]["nodes"] = [
        {"id": "cycle", "kind": "workflow", "workflow_id": value["workflow"]["id"]}
    ]
    leaf["document"]["graph"]["output_mapping"] = {"ref": "nodes.cycle"}
    with pytest.raises(ValueError, match="recursive cycle"):
        studio.configuration.validate(document, None)
    assert studio.records("configuration-plans") == []
    assert studio.definitions("workflows") == []

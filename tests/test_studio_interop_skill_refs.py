"""Explicit declarative knowledge references remain separate from routing skills."""

import copy

import pytest

from tests.test_studio_interop_capabilities import BINDINGS, source
from tests.test_studio_interop_capabilities import studio as studio
from tests.test_studio_team import team as team


def convert(studio, doc, bindings=None):
    return studio.configuration.convert_interop(
        {"documents": [doc], "bindings": bindings or BINDINGS}, None
    )


def referenced():
    doc = source()
    doc["spec"]["additionalConfigurations"] = {}
    doc["spec"]["skillRefs"] = ["evidence"]
    return doc


def test_bound_skill_refs_convert_roundtrip_without_fetch_or_state_writes(studio):
    doc = referenced()
    before = copy.deepcopy(doc)
    result = convert(studio, doc)
    assert result["valid"], result
    native = result["document"]
    spec = native["definitions"][0]["document"]["spec"]
    assert spec["skill_ids"] == ["knowledge"]
    assert spec["agent_card"]["skills"][0]["name"] == "Risk review"
    exported = studio.configuration.export_interop({"document": native}, None)
    assert exported["valid"], exported
    assert exported["bundle"]["documents"][0]["spec"] == before["spec"]
    again = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
    assert again["valid"] and again["document"] == native
    assert doc == before and not studio.definitions("agents")
    assert not studio.records("configuration-plans") and not studio.records("runs")


def test_ordered_union_deduplicates_aliases_and_existing_knowledge_refs(studio):
    studio.catalog.save(
        "skills", {"id": "second", "name": "Second", "data": {"content": "Secondary instructions"}}
    )
    doc = source()
    doc["spec"]["skillRefs"] = ["another", "evidence", "another"]
    bindings = copy.deepcopy(BINDINGS)
    bindings["skills"]["another"] = "second"
    result = convert(studio, doc, bindings)
    assert result["valid"], result
    assert result["document"]["definitions"][0]["document"]["spec"]["skill_ids"] == [
        "knowledge",
        "second",
    ]


@pytest.mark.parametrize(
    "refs",
    [
        "evidence",
        None,
        [None],
        [{"name": "evidence"}],
        [""],
        ["unbound"],
        ["../private/SKILL.md"],
        ["https://example.invalid/SKILL.md"],
        ["evidence"] * 21,
    ],
)
def test_invalid_or_unbound_references_reject_whole_conversion_without_fetch(studio, refs):
    doc = referenced()
    doc["spec"]["skillRefs"] = refs
    report = convert(studio, doc)
    assert not report["valid"] and report["document"] is None
    assert any("/skillRefs" in item["path"] for item in report["diagnostics"])
    assert not studio.definitions("agents") and not studio.records("runs")


def test_archived_skill_and_external_activation_remain_rejected(studio):
    doc = referenced()
    doc["spec"]["deploy"] = True
    report = convert(studio, doc)
    assert not report["valid"] and any(
        item["code"] == "explicit_activation" for item in report["diagnostics"]
    )
    del doc["spec"]["deploy"]
    studio.catalog.archive("skills", "knowledge", {"revision": 1})
    report = convert(studio, doc)
    assert not report["valid"] and report["document"] is None


def test_combined_skill_limit_is_checked_before_any_plan(studio):
    doc = source()
    doc["spec"]["skillRefs"] = []
    bindings = copy.deepcopy(BINDINGS)
    for index in range(20):
        alias = f"extra-{index}"
        studio.catalog.save("skills", {"id": alias, "name": alias, "data": {"content": "Fixture"}})
        bindings["skills"][alias] = alias
        doc["spec"]["skillRefs"].append(alias)
    report = convert(studio, doc, bindings)
    assert not report["valid"] and report["document"] is None
    assert any(item["code"] == "invalid_skills" for item in report["diagnostics"])
    assert not studio.records("configuration-plans")


def test_review_apply_and_queue_pin_use_native_skill_content(studio, monkeypatch):
    from nexus.core.models import AgentResult
    from tests.test_studio_resources import done

    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context)
            return AgentResult(success=True, output="Reviewed")

    monkeypatch.setattr(studio, "provider_factory", lambda _: Provider())
    converted = convert(studio, referenced())
    assert converted["valid"], converted
    plan = studio.configuration.plan(converted["document"], None)
    assert not studio.definitions("agents") and not calls
    studio.configuration.apply(plan["id"], plan["digest"], None)
    submissions = []
    submit = studio.executor.submit
    monkeypatch.setattr(studio.executor, "submit", lambda *args: submissions.append(args))
    queued = studio.start_run(
        {"kind": "chat", "agent_id": "reviewer", "task": "Review this evidence"}
    )
    old = studio.catalog.get("skills", "knowledge")
    studio.catalog.save(
        "skills", {**old, "data": {"content": "NEW_SKILL_MUST_NOT_REPLACE_QUEUED_CONTENT"}}
    )
    monkeypatch.setattr(studio.executor, "submit", submit)
    submit(*submissions[0])
    result = done(studio, queued)
    assert result["status"] == "completed", result
    assert len(calls) == 1 and "Private local evidence instructions" in calls[0].prompt
    assert "NEW_SKILL_MUST_NOT_REPLACE_QUEUED_CONTENT" not in calls[0].prompt


def test_http_owner_only_conversion_keeps_explicit_bindings_and_no_writes(team):
    studio, _, _, request, calls = team
    studio.catalog.save(
        "skills", {"id": "knowledge", "name": "Evidence", "data": {"content": "Fixture content"}}
    )
    doc = referenced()
    del doc["spec"]["toolsets"]
    path = "/api/studio/configuration/interop/convert"
    data = {"documents": [doc], "bindings": BINDINGS}
    assert request(path, data)[0] == 401
    for user in ("alice", "bob", "viewer"):
        assert request(path, data, user=user)[0] == 403
    status, report = request(path, data, user="owner")
    assert status == 200 and report["valid"], report
    assert report["document"]["definitions"][0]["document"]["spec"]["skill_ids"] == ["knowledge"]
    assert not calls and not studio.records("configuration-plans")


def test_empty_declared_skill_refs_roundtrip_without_expanding_permissions(studio):
    doc = referenced()
    doc["spec"] = {"systemPrompt": "Read supplied evidence", "modelProvider": ["subscription"], "skillRefs": []}
    report = convert(studio, doc)
    assert report["valid"], report
    assert not report["document"]["definitions"][0]["document"]["spec"].get("skill_ids")
    exported = studio.configuration.export_interop({"document": report["document"]}, None)
    assert exported["valid"], exported
    assert exported["bundle"]["documents"][0]["spec"] == doc["spec"]

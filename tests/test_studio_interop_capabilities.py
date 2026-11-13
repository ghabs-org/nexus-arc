"""Declarative routing capabilities remain distinct from knowledge bundles."""

import threading

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_workflow_v2 import wait


def source():
    return {
        "kind": "agent",
        "name": "reviewer",
        "description": "Review supplied evidence",
        "spec": {
            "systemPrompt": "Review supplied evidence",
            "modelProvider": ["subscription"],
            "inputModes": ["text"],
            "outputModes": ["text"],
            "skills": [
                {
                    "name": "Risk review",
                    "description": "Assess supplied risk evidence",
                    "tags": ["risk"],
                    "examples": ["Check these risks"],
                }
            ],
            "toolsets": ["local"],
            "additionalConfigurations": {"skills": [{"name": "evidence"}]},
        },
    }


BINDINGS = {
    "models": {"subscription": {"provider": "codex"}},
    "skills": {"evidence": "knowledge"},
    "toolsets": {"local": "local-tools"},
}


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    service.catalog.save(
        "skills",
        {
            "id": "knowledge",
            "name": "Knowledge",
            "data": {"content": "Private local evidence instructions"},
        },
    )
    service.catalog.save(
        "toolsets",
        {"id": "local-tools", "name": "Local tools", "data": {"tools": ["text:wordcount"]}},
    )
    yield service
    service.close()


def convert(studio, doc=None, bindings=None):
    return studio.configuration.convert_interop(
        {"documents": [doc or source()], "bindings": BINDINGS if bindings is None else bindings},
        None,
    )


def test_declarative_routing_knowledge_and_toolset_roundtrip_without_invocation(studio):
    original = source()
    report = convert(studio, original)
    assert report["valid"], report
    native = report["document"]
    spec = native["definitions"][0]["document"]["spec"]
    assert spec["skill_ids"] == ["knowledge"]
    assert spec["toolset_ids"] == ["local-tools"]
    capability = spec["agent_card"]["skills"][0]
    assert capability["id"].startswith("cap-")
    assert capability["name"] == "Risk review"
    exported = studio.configuration.export_interop({"document": native}, None)
    assert exported["valid"], exported
    assert exported["bundle"]["documents"][0]["spec"] == original["spec"]
    assert "id" not in original["spec"]["skills"][0]
    again = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
    assert again["valid"] and again["document"] == native
    assert studio.definitions("agents") == [] and studio.records("runs") == []


def test_generated_capability_ids_stay_stable_when_reordered(studio):
    doc = source()
    doc["spec"]["skills"].append(
        {"name": "Cost review", "description": "Assess supplied cost evidence"}
    )
    first = convert(studio, doc)
    doc["spec"]["skills"].reverse()
    second = convert(studio, doc)
    cards = [
        r["document"]["definitions"][0]["document"]["spec"]["agent_card"] for r in (first, second)
    ]
    assert {i["name"]: i["id"] for i in cards[0]["skills"]} == {
        i["name"]: i["id"] for i in cards[1]["skills"]
    }


@pytest.mark.parametrize(
    "failure", ["unknown", "duplicate", "missing_description", "unbound_toolset", "mode"]
)
def test_invalid_capabilities_and_unbound_permissions_reject_complete_document(studio, failure):
    doc = source()
    if failure == "unknown":
        doc["spec"]["skills"][0]["permissions"] = ["write"]
    elif failure == "duplicate":
        doc["spec"]["skills"] *= 2
    elif failure == "missing_description":
        del doc["spec"]["skills"][0]["description"]
    elif failure == "unbound_toolset":
        doc["spec"]["toolsets"] = ["unavailable"]
    else:
        doc["spec"]["outputModes"] = ["file"]
    report = convert(studio, doc)
    assert not report["valid"] and report["document"] is None
    assert report["diagnostics"]
    assert studio.definitions("agents") == [] and studio.records("configuration-plans") == []


def test_queued_coordinator_uses_pinned_capability_descriptions(tmp_path):
    calls, release = [], threading.Event()
    barrier = threading.Barrier(5)

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(
                success=True,
                output="reviewer" if "You are a coordinator." in context.prompt else "Reviewed",
            )

    service = StudioService(tmp_path, provider_factory=lambda _: Provider())
    try:
        agent = service.save_definition(
            "agents",
            {
                "name": "Reviewer",
                "instructions": "Use evidence",
                "agent_card": {
                    "skills": [
                        {"id": "risk", "name": "Risk", "description": "ORIGINAL_RISK_CAPABILITY"}
                    ]
                },
            },
        )

        def occupy_worker():
            barrier.wait(timeout=5)
            release.wait(timeout=10)

        workers = [service.executor.submit(occupy_worker) for _ in range(4)]
        barrier.wait(timeout=5)
        run = service.start_run(
            {
                "kind": "delegation",
                "strategy": "automatic",
                "agent_ids": [agent["id"]],
                "default_agent_id": agent["id"],
                "coordinator_provider": "codex",
                "task": "Assess this risk",
            }
        )
        service.update_definition(
            "agents",
            agent["id"],
            {
                "name": "Reviewer",
                "revision": agent["revision"],
                "instructions": "Use new evidence",
                "agent_card": {
                    "skills": [{"id": "risk", "name": "Risk", "description": "NEW_RISK_CAPABILITY"}]
                },
            },
        )
        release.set()
        result = wait(service, run)
        assert result["coordinator_selected_agent"] == agent["id"]
        assert "ORIGINAL_RISK_CAPABILITY" in calls[0]
        assert "NEW_RISK_CAPABILITY" not in calls[0]
        for worker in workers:
            worker.result(timeout=5)
    finally:
        release.set()
        service.close()

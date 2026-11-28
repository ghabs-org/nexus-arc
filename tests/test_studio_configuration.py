import json

import pytest

from nexus.core.studio import StudioService


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def document(name="Reviewer", purpose="Review sources"):
    return {
        "format": "nexus-studio-config/v1",
        "definitions": [
            {
                "collection": "agents",
                "id": "reviewer",
                "document": {
                    "apiVersion": "nexus-arc/v1",
                    "kind": "Agent",
                    "metadata": {"name": name},
                    "spec": {"agent_type": "reviewer", "provider": "codex", "purpose": purpose},
                },
            }
        ],
        "resources": [
            {
                "kind": "prompts",
                "id": "evidence",
                "name": "Evidence",
                "data": {"content": "Use sources"},
            }
        ],
    }


def test_validate_does_not_persist_a_plan_or_modify_resources(studio):
    result = studio.configuration.validate(document(), None)
    assert result["valid"] and result["changes"]
    assert not studio.records("configuration-plans")
    assert not studio.catalog.list("prompts") and not studio.definitions("agents")


def test_preflight_is_non_executing_and_apply_checks_reviewed_account_and_revision(studio):
    plan = studio.configuration.plan(document(), None)
    assert not studio.definitions("agents") and not studio.catalog.list("prompts")
    assert plan["changes"][0]["action"] == "create"
    with pytest.raises(PermissionError, match="Review"):
        studio.configuration.apply(plan["id"], "wrong-digest", None)
    release = studio.configuration.apply(plan["id"], plan["digest"], None)
    assert release["status"] == "active"
    assert studio.definition("agents", "reviewer")["spec"]["purpose"] == "Review sources"
    assert studio.configuration.export(None)["resources"][0]["data"]["content"] == "Use sources"
    with pytest.raises(ValueError, match="changed"):
        studio.configuration.apply(plan["id"], plan["digest"], None)


def test_failed_release_rolls_back_created_files_and_resources(studio, monkeypatch):
    plan = studio.configuration.plan(document(), None)
    original = studio.configuration.replace_file
    monkeypatch.setattr(
        studio.configuration,
        "replace_file",
        lambda path, text: (_ for _ in ()).throw(OSError("test disk error")),
    )
    with pytest.raises(OSError):
        studio.configuration.apply(plan["id"], plan["digest"], None)
    assert not studio.definitions("agents")
    assert not studio.catalog.list("prompts")
    journal = json.loads((studio.configuration.journal_dir / (plan["id"] + ".json")).read_text())
    assert journal["status"] == "rolled-back"
    monkeypatch.setattr(studio.configuration, "replace_file", original)


def test_recovery_preserves_unrelated_definition_edits(studio):
    plan = studio.configuration.plan(document(), None)
    journal = studio.configuration.journal_dir / (plan["id"] + ".json")
    studio._write(
        journal,
        {**studio._read(studio._path("configuration-plans", plan["id"])), "status": "applying"},
    )
    path = studio.root / "agents" / "reviewer.yaml"
    path.parent.mkdir()
    path.write_text("unrelated manual edit")
    with pytest.raises(ValueError, match="outside"):
        studio.configuration.recover()
    assert path.read_text() == "unrelated manual edit"


def test_external_resources_import_disabled_and_unknown_fields_are_reported(studio):
    doc = document()
    doc["resources"].append(
        {
            "kind": "connectors",
            "id": "service",
            "name": "Service",
            "data": {"type": "http", "url": "https://example.test", "enabled": True},
        }
    )
    plan = studio.configuration.plan(doc, None)
    assert not next(r for r in plan["document"]["resources"] if r["kind"] == "connectors")["data"][
        "enabled"
    ]
    doc["definitions"][0]["document"]["spec"]["unknown_setting"] = True
    with pytest.raises(ValueError, match="discard"):
        studio.configuration.plan(doc, None)

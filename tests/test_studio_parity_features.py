"""Behavioral contracts for additive external-equivalent Studio features."""

import base64
import time
from datetime import UTC, datetime

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_calendar import next_occurrence
from nexus.core.studio_prompts import render_prompt, validate_prompt


@pytest.fixture
def studio(tmp_path):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            return AgentResult(success=True, output="Grounded response [source:1]", metadata={})

    service = StudioService(tmp_path, provider_factory=lambda _: Provider())
    yield service
    service.close()


def wait(studio, run):
    for _ in range(500):
        record = studio.run(run["id"])
        if record["status"] not in {"queued", "running"}:
            assert record["status"] == "completed", record
            return record
        time.sleep(0.01)
    pytest.fail("Job did not finish")


def artifact(studio, text=b"Refund policy allows returns within thirty days."):
    return studio.catalog.save(
        "artifacts",
        {
            "name": "Policy",
            "data": {"filename": "policy.md", "content_base64": base64.b64encode(text).decode()},
        },
    )


def test_schema_upgrade_is_idempotent_and_rejects_future_version(studio):
    from nexus.core.studio_schema import upgrade

    assert upgrade(studio) == studio.schema
    assert len(studio.records("schema-history")) == 1
    studio._write(studio._path("system", "schema"), {"version": 999})
    with pytest.raises(ValueError, match="compatible"):
        upgrade(studio)


def test_prompt_variables_defaults_shortcuts_and_no_recursive_substitution(studio):
    data = validate_prompt(
        {
            "content": "Review {{Module Name}} for {{Concern}}",
            "variables": {"Concern": "security"},
            "shortcut": "review",
            "tags": ["code"],
        }
    )
    with pytest.raises(ValueError, match="Module Name"):
        render_prompt(data)
    assert render_prompt(data, {"Module Name": "{{Concern}}"}) == "Review {{Concern}} for security"
    prompt = studio.catalog.save("prompts", {"name": "Review template", "data": data})
    payload = studio.catalog.expand(
        {"task": "/review", "prompt_values": {prompt["id"]: {"Module Name": "billing"}}}
    )
    assert payload["expanded_task"] == "Review billing for security"


def test_calendar_weekdays_spring_gap_fall_fold_and_invalid_dates():
    assert next_occurrence(
        "0 9 * * 1-5", "Europe/Rome", datetime(2026, 10, 16, 8, tzinfo=UTC)
    ) == datetime(2026, 10, 19, 7, tzinfo=UTC)
    # 02:30 does not exist on the spring-forward date.
    assert next_occurrence(
        "30 2 * * *", "Europe/Rome", datetime(2026, 3, 28, 2, tzinfo=UTC)
    ) == datetime(2026, 3, 30, 0, 30, tzinfo=UTC)
    # Second 02:30 on fall-back day must not run again.
    assert next_occurrence(
        "30 2 * * *", "Europe/Rome", datetime(2026, 10, 25, 0, 31, tzinfo=UTC)
    ) == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)
    with pytest.raises(ValueError, match="no occurrence"):
        next_occurrence("0 1 30 2 *", "UTC", datetime.now(UTC))


def test_knowledge_index_search_owner_checks_and_pinned_citations(studio):
    project = studio.knowledge.save({"name": "Policies", "instructions": "Cite documents"}, None)
    file = artifact(studio)
    doc = studio.knowledge.attach(project["id"], {"artifact_id": file["id"]}, None)
    wait(studio, {"id": doc["run_id"]})
    found = studio.knowledge.search(project["id"], "refund policy", None)
    assert found["items"][0]["artifact_revision"] == 1
    with pytest.raises(PermissionError):
        studio.knowledge.get(project["id"], {"nexus_id": "other", "role": "operator"})
    run = wait(
        studio,
        studio.start_run(
            {"task": "What is the refund policy?", "knowledge_project_id": project["id"]}
        ),
    )
    assert run["citations"][0]["artifact_revision"] == 1
    assert studio.session(run["session_id"])["knowledge_project_id"] == project["id"]
    studio.catalog.save(
        "artifacts",
        {
            "id": file["id"],
            "revision": 1,
            "name": "Policy",
            "data": {
                "filename": "policy.md",
                "content_base64": base64.b64encode(b"Changed policy").decode(),
            },
        },
    )
    assert (
        "thirty days" in studio.knowledge.search(project["id"], "refund", None)["items"][0]["text"]
    )
    assert studio.run(run["id"])["citations"][0]["artifact_revision"] == 1


def test_semantic_fallback_is_local_and_explicit(studio):
    project = studio.knowledge.save({"name": "Notes"}, None)
    result = studio.knowledge.search(project["id"], "anything", None, "hybrid")
    assert result["mode"] == "lexical" and "No embedding provider" in result["notice"]
    studio.knowledge.embeddings = lambda texts, actor: (_ for _ in ()).throw(
        ValueError("provider down")
    )
    with pytest.raises(ValueError, match="provider down"):
        studio.knowledge.search(project["id"], "anything", None, "semantic")


def test_feedback_deduplication_and_public_snapshot_revocation(studio):
    run = wait(studio, studio.start_run({"task": "Hello"}))
    feedback = studio.collaboration.feedback({"run_id": run["id"], "rating": 1}, None)
    with pytest.raises(ValueError, match="changed"):
        studio.collaboration.feedback({"run_id": run["id"], "rating": -1}, None)
    assert len(studio.collaboration.list_feedback(None)) == 1
    assert feedback["revision"] == 1
    with pytest.raises(ValueError, match="enable public"):
        studio.collaboration.share({"session_id": run["session_id"], "public": True}, None)
    studio.collaboration.configure({"enabled": True}, None)
    share = studio.collaboration.share({"session_id": run["session_id"], "public": True}, None)
    snapshot = studio.collaboration.get_share(share["token"], public=True)
    assert "token_hash" not in snapshot and "owner_id" not in snapshot
    session = studio.session(run["session_id"])
    session["messages"].append({"role": "user", "content": "Later private message"})
    studio._write(studio._path("sessions", session["id"]), session)
    assert len(studio.collaboration.get_share(share["token"], public=True)["messages"]) == 2
    studio.collaboration.revoke(share["id"], None)
    with pytest.raises(FileNotFoundError):
        studio.collaboration.get_share(share["token"], public=True)
    second = studio.collaboration.share({"session_id": run["session_id"], "public": True}, None)
    studio.collaboration.configure({"enabled": False}, None)
    studio.collaboration.configure({"enabled": True}, None)
    with pytest.raises(FileNotFoundError):
        studio.collaboration.get_share(second["token"], public=True)


def test_office_zip_bomb_and_xml_entities_are_rejected():
    import io
    import zipfile

    from nexus.core.studio_documents import extract_document

    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<!DOCTYPE doc [<!ENTITY secret SYSTEM "file:///etc/passwd">]><doc>&secret;</doc>',
        )
    with pytest.raises(ValueError, match="entities"):
        extract_document("file.docx", data.getvalue())

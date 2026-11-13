"""Portable knowledge respects immutable bytes, target ownership and full preflight."""

import base64
import copy
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_knowledge_packages import FORMAT, StudioKnowledgePackages
from nexus.core.studio_team import NexusTeamPolicy
from nexus.studio import make_studio_handler


def wait(studio, document):
    for _ in range(500):
        run = studio.run(document["run_id"])
        if run["status"] not in {"queued", "running"}:
            assert run["status"] == "completed", run
            return
        time.sleep(0.01)
    pytest.fail("Indexing did not finish")


@pytest.fixture
def studio(tmp_path):
    app = StudioService(tmp_path)
    yield app
    app.team = None
    app.close()


def package(content=b"Portable source", filename="source.md"):
    return {
        "format": FORMAT,
        "project": {
            "name": "Imported knowledge",
            "instructions": "Use provided sources",
            "retrieval_mode": "lexical",
        },
        "documents": [
            {
                "filename": filename,
                "content_base64": base64.b64encode(content).decode(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "source": {"artifact_id": "foreign-source-id", "revision": 7},
            }
        ],
    }


def save(studio, content, **values):
    return studio.catalog.save(
        "artifacts",
        {
            "name": "Source",
            **values,
            "data": {"filename": "source.md", "content_base64": base64.b64encode(content).decode()},
        },
    )


def test_portable_round_trip_uses_attached_revision_and_fresh_target_ids(studio, tmp_path):
    original = save(studio, b"Original portable evidence")
    project = studio.knowledge.save(
        {"name": "Evidence", "instructions": "Stay grounded", "retrieval_mode": "hybrid"}, None
    )
    doc = studio.knowledge.attach(project["id"], {"artifact_id": original["id"]}, None)
    wait(studio, doc)
    save(studio, b"Replacement bytes", id=original["id"], revision=1)
    exported = StudioKnowledgePackages(studio).export(project["id"], None)
    assert (
        base64.b64decode(exported["documents"][0]["content_base64"])
        == b"Original portable evidence"
    )
    assert exported["documents"][0]["source"]["revision"] == 1
    target = StudioService(tmp_path / "other-workspace")
    try:
        imported = StudioKnowledgePackages(target).import_package(exported, None)
        assert imported["id"] != project["id"] and imported["owner_id"] == "desktop"
        documents = target.knowledge.documents(imported["id"], None)
        assert len(documents) == 1 and documents[0]["artifact_id"] != original["id"]
        wait(target, documents[0])
        found = target.knowledge.search(imported["id"], "portable", None)
        assert found["items"][0]["artifact_revision"] == 1
        assert found["items"][0]["text"] == "Original portable evidence"
        assert target.records("sessions") == []
    finally:
        target.close()


@pytest.mark.parametrize(
    "problem",
    ["hash", "path", "base64", "extra", "settings", "agent", "count", "provenance", "mode"],
)
def test_entire_import_preflight_precedes_any_record_or_job(studio, problem):
    data = package()
    data["documents"].append(copy.deepcopy(data["documents"][0]))
    doc = data["documents"][-1]
    if problem == "hash":
        doc["sha256"] = "wrong"
    if problem == "path":
        doc["filename"] = "../../private.txt"
    if problem == "base64":
        doc["content_base64"] = "!notbase64"
    if problem == "extra":
        doc["installation_hook"] = "bad"
    if problem == "settings":
        data["project"]["credentials"] = "bad"
    if problem == "agent":
        data["project"]["default_agent_id"] = "missing-agent"
    if problem == "count":
        data["documents"] *= 51
    if problem == "provenance":
        doc["source"]["revision"] = True
    if problem == "mode":
        data["project"]["retrieval_mode"] = []
    with pytest.raises((ValueError, FileNotFoundError)):
        StudioKnowledgePackages(studio).import_package(data, None)
    assert studio.knowledge.list(None) == []
    assert studio.catalog.list("artifacts") == []
    assert studio.records("knowledge-documents") == []
    assert studio.records("runs") == []


def test_package_limits_total_decoded_bytes_before_publishing(studio, monkeypatch):
    monkeypatch.setattr("nexus.core.studio_knowledge_packages.PACKAGE_BYTES", 4)
    with pytest.raises(ValueError, match="document bytes"):
        StudioKnowledgePackages(studio).import_package(package(b"12345"), None)
    assert studio.knowledge.list(None) == [] and studio.catalog.list("artifacts") == []


def test_storage_failure_removes_new_catalog_records_before_project_publication(
    studio, monkeypatch
):
    data = package()
    data["documents"] *= 2
    put, calls = studio.artifacts.put, []

    def fail_second(record):
        calls.append(record["id"])
        if len(calls) == 2:
            raise ValueError("Storage unavailable")
        return put(record)

    monkeypatch.setattr(studio.artifacts, "put", fail_second)
    with pytest.raises(ValueError, match="Storage unavailable"):
        StudioKnowledgePackages(studio).import_package(data, None)
    assert studio.knowledge.list(None) == [] and studio.catalog.list("artifacts") == []


def test_import_cannot_reuse_provenance_as_target_or_transfer_ownership(studio):
    actor = {"nexus_id": "alice", "role": "builder"}
    imported = StudioKnowledgePackages(studio).import_package(package(), actor)
    assert imported["owner_id"] == "alice"
    record = studio.knowledge.documents(imported["id"], actor)[0]
    assert record["artifact_id"] != "foreign-source-id"
    assert studio.catalog.get("artifacts", record["artifact_id"])["owner_id"] == "alice"
    with pytest.raises(PermissionError):
        StudioKnowledgePackages(studio).export(
            imported["id"], {"nexus_id": "bob", "role": "builder"}
        )


def test_revoked_document_does_not_reappear_through_index_or_portable_export(studio):
    alice = {"nexus_id": "alice", "role": "builder"}
    artifact = save(studio, b"Private evidence", requester_nexus_id="alice")
    project = studio.knowledge.save({"name": "Private project"}, alice)
    doc = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, alice)
    wait(studio, doc)
    studio.team = SimpleNamespace(visible=NexusTeamPolicy.visible)
    assert studio.knowledge.search(project["id"], "Private", alice)["items"]
    current = studio.catalog.get("artifacts", artifact["id"])
    studio._write(studio.catalog._path("artifacts", artifact["id"]), {**current, "owner_id": "bob"})
    assert studio.knowledge.search(project["id"], "Private", alice)["items"] == []
    assert StudioKnowledgePackages(studio).export(project["id"], alice)["documents"] == []


def test_new_questions_switch_to_new_ready_revision_old_citations_survive(studio):
    old = save(studio, b"Original evidence")
    project = studio.knowledge.save({"name": "Revision project"}, None)
    wait(studio, studio.knowledge.attach(project["id"], {"artifact_id": old["id"]}, None))
    new = save(studio, b"Replacement evidence", id=old["id"], revision=1)
    wait(studio, studio.knowledge.attach(project["id"], {"artifact_id": new["id"]}, None))
    found = studio.knowledge.search(project["id"], "evidence", None)
    assert len(found["items"]) == 1 and found["items"][0]["artifact_revision"] == 2
    assert (
        studio.artifacts.preview(studio.artifacts.revision(old["id"], 1))["text"]
        == "Original evidence"
    )


def test_default_search_settings_are_pinned_and_old_clients_preserve_them(studio):
    project = studio.knowledge.save({"name": "Settings", "retrieval_mode": "hybrid"}, None)
    prepared = studio.knowledge.prepare({"task": "Evidence", "knowledge_project_id": project["id"]})
    edited = studio.knowledge.save(
        {**project, "retrieval_mode": "lexical", "instructions": "New"}, None
    )
    assert prepared["knowledge_snapshot"]["project"]["retrieval_mode"] == "hybrid"
    assert prepared["knowledge_snapshot"]["project"]["instructions"] == ""
    del edited["retrieval_mode"]
    assert studio.knowledge.save(edited, None)["retrieval_mode"] == "lexical"


def test_http_import_accepts_portable_bytes_above_old_body_limit(studio):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps(package(b"x" * 800000)).encode()
        assert len(payload) > 1024 * 1024
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/studio/projects/import",
            data=payload,
            headers={"Content-Type": "application/json", "X-Nexus-Studio-Token": "csrf"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 201
            project = json.load(response)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/studio/projects/{project['id']}/package",
            timeout=10,
        ) as response:
            assert (
                json.load(response)["documents"][0]["sha256"]
                == hashlib.sha256(b"x" * 800000).hexdigest()
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_interrupted_index_publication_keeps_old_source_until_explicit_retry(studio, monkeypatch):
    artifact = save(studio, b"Original evidence")
    project = studio.knowledge.save({"name": "Recover indexing"}, None)
    wait(studio, studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None))
    replacement = save(studio, b"Replacement evidence", id=artifact["id"], revision=1)
    write = studio._write
    injected = []

    def fail_ready(path, value):
        if (
            "knowledge-documents" in str(path)
            and value.get("artifact_revision") == 2
            and value.get("status") == "ready"
            and not injected
        ):
            injected.append(True)
            raise OSError("Interrupted publication after FTS commit")
        return write(path, value)

    monkeypatch.setattr(studio, "_write", fail_ready)
    document = studio.knowledge.attach(project["id"], {"artifact_id": replacement["id"]}, None)
    for _ in range(500):
        if studio.run(document["run_id"])["status"] == "failed":
            break
        time.sleep(0.01)
    assert injected
    found = studio.knowledge.search(project["id"], "evidence", None)
    assert len(found["items"]) == 1 and found["items"][0]["artifact_revision"] == 1
    retried = studio.knowledge.retry(project["id"], document["id"], None)
    wait(studio, retried)
    found = studio.knowledge.search(project["id"], "evidence", None)
    assert len(found["items"]) == 1 and found["items"][0]["artifact_revision"] == 2
    with pytest.raises(ValueError, match="already indexed"):
        studio.knowledge.retry(project["id"], document["id"], None)


def test_index_retry_rejects_foreign_documents_and_duplicate_running_jobs(studio):
    project = studio.knowledge.save({"name": "Owner project"}, None)
    studio._write(
        studio._path("runs", "busy-index"),
        {
            "id": "busy-index",
            "status": "running",
            "kind": "knowledge-index",
            "provider": "codex",
            "created_at": "2026-10-18T00:00:00+00:00",
        },
    )
    artifact = save(studio, b"Queued evidence")
    document = {
        "id": "queued-document",
        "owner_id": "desktop",
        "project_id": project["id"],
        "artifact_id": artifact["id"],
        "artifact_revision": 1,
        "filename": "source.md",
        "status": "indexing",
        "run_id": "busy-index",
    }
    studio._write(studio._path("knowledge-documents", document["id"]), document)
    with pytest.raises(ValueError, match="already in progress"):
        studio.knowledge.retry(project["id"], document["id"], None)
    other = studio.knowledge.save({"name": "Other project"}, None)
    with pytest.raises(FileNotFoundError):
        studio.knowledge.retry(other["id"], document["id"], None)

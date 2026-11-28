"""Queued knowledge pins, cancellation fencing and restart/permission acceptance."""

import base64
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_state import LostLeaseError, job_fence
from nexus.core.studio_team import NexusTeamPolicy
from tests.test_studio_knowledge_vectors import attach, done
from tests.test_studio_team import NexusIdentity


def options(tmp_path, workers):
    return {
        "url": "sqlite:///" + str(tmp_path / "state.sqlite"),
        "namespace": "knowledge-recovery",
        "workers": workers,
    }


def service(tmp_path, workers, policy=None, calls=None):
    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            calls.append(context.prompt)
            return AgentResult(success=True, output="Pinned source answer")

    if policy:
        policy.provider = lambda *args, **kwargs: Provider()
    return StudioService(
        tmp_path / "project",
        state_dir=tmp_path / "private",
        state_options=options(tmp_path, workers),
        team_policy=policy,
        provider_factory=lambda _: Provider(),
    )


def test_queued_chat_keeps_original_settings_and_revision_across_workers(tmp_path):
    calls = []
    app = service(tmp_path, 1, calls=calls)
    project = app.knowledge.save({"name": "Pinned", "instructions": "ORIGINAL_INSTRUCTIONS"}, None)
    artifact, _ = attach(app, project, "Source", "Original refund rule")
    app.close()
    controller = service(tmp_path, 0, calls=calls)
    run = controller.start_run({"task": "refund", "knowledge_project_id": project["id"]})
    controller.knowledge.save(
        {**project, "instructions": "NEW_INSTRUCTIONS", "retrieval_mode": "hybrid"}, None
    )
    updated = controller.catalog.save(
        "artifacts",
        {
            "id": artifact["id"],
            "revision": 1,
            "name": "Source",
            "data": {
                "filename": "Source.md",
                "content_base64": base64.b64encode(b"Replacement refund rule").decode(),
            },
        },
    )
    replacement = controller.knowledge.attach(project["id"], {"artifact_id": updated["id"]}, None)
    controller.close()
    worker = service(tmp_path, 2, calls=calls)
    try:
        result = done(worker, run)
        assert result["status"] == "completed", result
        assert done(worker, {"id": replacement["run_id"]})["status"] == "completed"
        assert len(calls) == 1 and "ORIGINAL_INSTRUCTIONS" in calls[0]
        assert "Original refund rule" in calls[0] and "Replacement refund rule" not in calls[0]
        assert "NEW_INSTRUCTIONS" not in calls[0]
        assert result["citations"][0]["artifact_revision"] == 1
        saved = worker.session(run["session_id"])["messages"][-1]
        assert saved["retrieval"]["requested_mode"] == "lexical"
        assert (
            worker.knowledge.search(project["id"], "refund", None)["items"][0]["artifact_revision"]
            == 2
        )
        fresh = done(
            worker, worker.start_run({"task": "refund", "knowledge_project_id": project["id"]})
        )
        assert fresh["status"] == "completed" and fresh["citations"][0]["artifact_revision"] == 2
        assert "NEW_INSTRUCTIONS" in calls[-1] and "Replacement refund rule" in calls[-1]
        assert worker.run(run["id"])["citations"][0]["artifact_revision"] == 1
    finally:
        worker.close()


@pytest.mark.parametrize(
    "change", ["archive-project", "source-owner", "project-owner", "account-revoked"]
)
def test_queued_revocation_fails_before_any_model_invocation(tmp_path, change):
    manager, calls = NexusIdentity(), []
    policy = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login",
        bindings={"alice": "builder", "bob": "builder", "owner": "owner"},
        manager=manager,
    )
    actor = {"nexus_id": "alice", "role": "builder"}
    app = service(tmp_path, 1, policy, calls)
    project = app.knowledge.save({"name": "Private"}, actor)
    artifact = app.catalog.save(
        "artifacts",
        {
            "name": "Private source",
            "requester_nexus_id": "alice",
            "data": {
                "filename": "source.md",
                "content_base64": base64.b64encode(b"Private refund source").decode(),
            },
        },
    )
    document = app.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, actor)
    assert done(app, {"id": document["run_id"]})["status"] == "completed"
    app.close()
    controller = service(tmp_path, 0, policy, calls)
    run = controller.start_run(
        {"task": "refund", "knowledge_project_id": project["id"], "requester_nexus_id": "alice"}
    )
    if change == "archive-project":
        controller.knowledge.save({**project, "archived": True}, actor)
    if change == "source-owner":
        current = controller.catalog.get("artifacts", artifact["id"])
        controller._write(
            controller.catalog._path("artifacts", artifact["id"]), {**current, "owner_id": "bob"}
        )
    if change == "project-owner":
        controller._write(
            controller._path("knowledge-projects", project["id"]), {**project, "owner_id": "bob"}
        )
    if change == "account-revoked":
        manager.revoked.add("alice")
    controller.close()
    worker = service(tmp_path, 1, policy, calls)
    try:
        result = done(worker, run)
        assert result["status"] == "failed", result
        assert calls == []
        assert worker.records("sessions") == []
    finally:
        worker.close()


def test_cancelled_extraction_cannot_publish_over_a_new_retry(tmp_path, monkeypatch):
    studio = StudioService(tmp_path)
    project = studio.knowledge.save({"name": "Cancellation"}, None)
    artifact, _ = attach(studio, project, "Source", "Original refund rule")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    from nexus.core import studio_knowledge

    extract = studio_knowledge.extract_document
    attempts, old_run = [], []

    def blocked(filename, content, *args):
        if content.startswith(b"Replacement"):
            attempts.append(True)
            if len(attempts) == 1:
                entered.set()
                assert release.wait(8)
        return extract(filename, content, *args)

    index = studio.knowledge.index

    def observed(document_id, run_id=None):
        try:
            return index(document_id, run_id)
        finally:
            if old_run and run_id == old_run[0]:
                finished.set()

    monkeypatch.setattr(studio_knowledge, "extract_document", blocked)
    monkeypatch.setattr(studio.knowledge, "index", observed)
    try:
        replacement = studio.catalog.save(
            "artifacts",
            {
                "id": artifact["id"],
                "revision": 1,
                "name": "Source",
                "data": {
                    "filename": "Source.md",
                    "content_base64": base64.b64encode(b"Replacement refund rule").decode(),
                },
            },
        )
        document = studio.knowledge.attach(project["id"], {"artifact_id": replacement["id"]}, None)
        old_run.append(document["run_id"])
        assert entered.wait(5)
        studio.cancel_run(document["run_id"])
        assert done(studio, {"id": document["run_id"]})["status"] == "cancelled"
        pending = next(
            doc
            for doc in studio.knowledge.documents(project["id"], None)
            if doc["id"] == document["id"]
        )
        assert pending["retryable"]
        assert (
            studio.knowledge.search(project["id"], "refund", None)["items"][0]["artifact_revision"]
            == 1
        )
        retried = studio.knowledge.retry(project["id"], document["id"], None)
        assert done(studio, {"id": retried["run_id"]})["status"] == "completed"
        release.set()
        assert finished.wait(5)
        current = studio._read(studio._path("knowledge-documents", document["id"]))
        assert current["run_id"] == retried["run_id"] and current["status"] == "ready"
        assert (
            studio.knowledge.search(project["id"], "refund", None)["items"][0]["artifact_revision"]
            == 2
        )
    finally:
        release.set()
        studio.close()


def test_expired_sql_lease_blocks_index_publication_and_allows_explicit_retry(
    tmp_path, monkeypatch
):
    clock = [time.time()]
    monkeypatch.setattr("nexus.core.studio_state.time.time", lambda: clock[0])
    studio = service(tmp_path, 0, calls=[])
    project = studio.knowledge.save({"name": "Fenced index"}, None)
    artifact = studio.catalog.save(
        "artifacts",
        {
            "name": "Source",
            "data": {
                "filename": "source.md",
                "content_base64": base64.b64encode(b"Fenced refund source").decode(),
            },
        },
    )
    document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
    job = studio.sql_state.claim(60)
    assert job["run_id"] == document["run_id"]
    from nexus.core import studio_knowledge

    extract = studio_knowledge.extract_document

    def expire(*args):
        result = extract(*args)
        clock[0] += 61
        return result

    monkeypatch.setattr(studio_knowledge, "extract_document", expire)
    token = job_fence.set((job["run_id"], job["token"]))
    try:
        studio._update_run(job["run_id"], status="running")
        with pytest.raises(LostLeaseError):
            studio.knowledge.index(document["id"], job["run_id"])
    finally:
        job_fence.reset(token)
    try:
        assert studio.sql_state.reap() == 1
        assert studio.run(job["run_id"])["status"] == "interrupted"
        assert studio.knowledge.documents(project["id"], None)[0]["retryable"]
        assert studio.knowledge.search(project["id"], "refund", None)["items"] == []
        retried = studio.knowledge.retry(project["id"], document["id"], None)
        assert retried["run_id"] != job["run_id"]
    finally:
        studio.close()


def test_client_supplied_knowledge_snapshot_is_never_trusted(tmp_path):
    calls = []
    studio = service(tmp_path, 1, calls=calls)
    try:
        run = studio.start_run(
            {
                "task": "Hello",
                "knowledge_snapshot": {
                    "project": {"instructions": "FORGED_SOURCE"},
                    "items": [{"text": "LEAKED_SOURCE"}],
                },
            }
        )
        assert done(studio, run)["status"] == "completed"
        assert "FORGED_SOURCE" not in calls[0] and "LEAKED_SOURCE" not in calls[0]
    finally:
        studio.close()


@pytest.mark.parametrize("extension", ["txt", "md", "csv", "pdf", "docx", "pptx", "xlsx"])
def test_supported_formats_index_locally_with_source_units(tmp_path, extension):
    import io
    import zipfile

    from tests.test_studio_document_navigation import pdf

    content = b"Grounded invoice evidence"
    if extension == "pdf":
        content = pdf(3)
    elif extension in {"docx", "pptx", "xlsx"}:
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w") as archive:
            path = {
                "docx": "word/document.xml",
                "pptx": "ppt/slides/slide1.xml",
                "xlsx": "xl/sharedStrings.xml",
            }[extension]
            archive.writestr(
                path,
                '<document xmlns:a="urn:fixture"><a:t>Grounded invoice evidence</a:t></document>',
            )
        content = target.getvalue()
    studio = StudioService(tmp_path)
    try:
        project = studio.knowledge.save({"name": "Formats"}, None)
        artifact = studio.catalog.save(
            "artifacts",
            {
                "name": "Source",
                "data": {
                    "filename": "source." + extension,
                    "content_base64": base64.b64encode(content).decode(),
                },
            },
        )
        doc = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
        assert done(studio, {"id": doc["run_id"]})["status"] == "completed"
        result = studio.knowledge.search(
            project["id"], "Evidence" if extension == "pdf" else "invoice", None
        )
        assert result["items"] and all(item["artifact_revision"] == 1 for item in result["items"])
        if extension == "pdf":
            assert {item["page"] for item in result["items"]} == {1, 2, 3}
    finally:
        studio.close()


@pytest.mark.parametrize("kind", ["truncated-pdf", "empty", "oversized-text"])
def test_extraction_limits_fail_visibly_instead_of_publishing_partial_sources(tmp_path, kind):
    from tests.test_studio_document_navigation import pdf

    content, filename = {
        "truncated-pdf": (None, "source.pdf"),
        "empty": (b"", "source.txt"),
        "oversized-text": (b"x" * 256001, "source.txt"),
    }[kind]
    if content is None:
        content = pdf(51)
    studio = StudioService(tmp_path)
    try:
        project = studio.knowledge.save({"name": "Limits"}, None)
        artifact = studio.catalog.save(
            "artifacts",
            {
                "name": "Source",
                "data": {
                    "filename": filename,
                    "content_base64": base64.b64encode(content).decode(),
                },
            },
        )
        document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
        assert done(studio, {"id": document["run_id"]})["status"] == "failed"
        saved = studio.knowledge.documents(project["id"], None)[0]
        assert saved["retryable"] and saved["chunks"] == []
        assert {
            "truncated-pdf": "split",
            "empty": "no extractable text",
            "oversized-text": "256000",
        }[kind] in saved["error"]
        assert studio.knowledge.search(project["id"], "Evidence", None)["items"] == []
    finally:
        studio.close()


def test_workspace_owner_can_index_an_authorized_managed_project(tmp_path):
    manager = NexusIdentity()
    policy = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/login",
        bindings={"alice": "builder", "owner": "owner"},
        manager=manager,
    )
    studio = service(tmp_path, 1, policy, [])
    try:
        alice = {"nexus_id": "alice", "role": "builder"}
        owner = {"nexus_id": "owner", "role": "owner"}
        project = studio.knowledge.save({"name": "Managed sources"}, alice)
        artifact = studio.catalog.save(
            "artifacts",
            {
                "name": "Source",
                "requester_nexus_id": "alice",
                "data": {
                    "filename": "source.md",
                    "content_base64": base64.b64encode(b"Managed refund source").decode(),
                },
            },
        )
        document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, owner)
        assert done(studio, {"id": document["run_id"]})["status"] == "completed"
        assert studio.knowledge.search(project["id"], "refund", alice)["items"]
    finally:
        studio.close()


@pytest.mark.parametrize("boundary", ["before-commit", "before-ready", "after-ready"])
def test_abrupt_process_exit_never_publishes_partial_replacement(tmp_path, boundary):
    studio = StudioService(tmp_path)
    project = studio.knowledge.save({"name": "Crash publication"}, None)
    artifact, _ = attach(studio, project, "Source", "Original refund evidence")
    studio.close()
    # os._exit bypasses exception handlers, context exits and worker shutdown.
    # The three boundaries cover uncommitted FTS, committed FTS with old metadata,
    # and ready metadata published before the job's terminal record.
    code = """
import base64, contextlib, os, sys, threading
from nexus.core.studio import StudioService
app = StudioService(sys.argv[1])
project_id, artifact_id, boundary = sys.argv[2:]
if boundary == 'before-commit':
    original_db = app.knowledge._db
    @contextlib.contextmanager
    def crashing_db():
        with original_db() as db:
            yield db
            os._exit(73)
    app.knowledge._db = crashing_db
else:
    original_write = app._write
    def crashing_write(path, record):
        ready = ('knowledge-documents' in str(path)
                 and record.get('artifact_revision') == 2
                 and record.get('status') == 'ready')
        if ready and boundary == 'before-ready':
            os._exit(73)
        result = original_write(path, record)
        if ready:
            os._exit(73)
        return result
    app._write = crashing_write
replacement = app.catalog.save('artifacts', {
    'id': artifact_id, 'revision': 1, 'name': 'Source',
    'data': {'filename': 'Source.md', 'content_base64':
             base64.b64encode(b'Replacement refund evidence').decode()}})
app.knowledge.attach(project_id, {'artifact_id': replacement['id']}, None)
threading.Event().wait(10)
os._exit(99)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), project["id"], artifact["id"], boundary],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=15,
    )
    assert child.returncode == 73, child.stderr.decode()
    recovered = StudioService(tmp_path)
    try:
        found = recovered.knowledge.search(project["id"], "refund", None)["items"]
        expected = 2 if boundary == "after-ready" else 1
        assert len(found) == 1 and found[0]["artifact_revision"] == expected
        assert found[0]["text"] == (
            "Replacement refund evidence" if expected == 2 else "Original refund evidence"
        )
        replacement = next(
            doc
            for doc in recovered.knowledge.documents(project["id"], None)
            if doc["artifact_revision"] == 2
        )
        if expected == 1:
            assert replacement["retryable"]
            retry = recovered.knowledge.retry(project["id"], replacement["id"], None)
            assert done(recovered, {"id": retry["run_id"]})["status"] == "completed"
        assert (
            recovered.knowledge.search(project["id"], "refund", None)["items"][0][
                "artifact_revision"
            ]
            == 2
        )
        assert (
            recovered.artifacts.preview(recovered.artifacts.revision(artifact["id"], 1))["text"]
            == "Original refund evidence"
        )
    finally:
        recovered.close()


@pytest.mark.parametrize("extension", ["pdf", "docx", "pptx", "xlsx"])
def test_corrupt_documents_fail_indexing_and_remain_retryable(tmp_path, extension):
    studio = StudioService(tmp_path)
    try:
        project = studio.knowledge.save({"name": "Corrupt input"}, None)
        artifact = studio.catalog.save(
            "artifacts",
            {
                "name": "Corrupt source",
                "data": {
                    "filename": "source." + extension,
                    "content_base64": base64.b64encode(b"not a valid document").decode(),
                },
            },
        )
        doc = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
        assert done(studio, {"id": doc["run_id"]})["status"] == "failed"
        saved = studio.knowledge.documents(project["id"], None)[0]
        assert saved["retryable"] and saved["error"] and saved["chunks"] == []
        assert studio.knowledge.search(project["id"], "document", None)["items"] == []
    finally:
        studio.close()

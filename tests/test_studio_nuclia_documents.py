"""Canonical cited-file reads, private snapshots, recovery and publication fences."""

import asyncio
from datetime import UTC, datetime

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_nuclia import answer_sources, publish_answer
from nexus.core.studio_nuclia_documents import download_original, original_reference, queue_original
from tests.test_studio_nuclia import response
from tests.test_studio_team import team as team


def source_run(studio, identity=None, *, url="https://example.org", allow_private=False):
    record = studio.catalog.save(
        "connectors",
        {
            "name": "Original knowledge",
            "data": {
                "type": "nuclia",
                "url": url,
                "allow_private": allow_private,
                "knowledge_box": "selected",
                "token_env": "OPENAI_API_KEY",
                "actions": ["ask", "download_original"],
                "enabled": True,
            },
        },
    )
    studio._write(
        studio._path("runs", "source"),
        {
            "id": "source",
            "kind": "tool",
            "provider": "local-tool",
            "status": "running",
            "owner_id": identity,
            "requester_nexus_id": identity,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    token = current_run_id.set("source")
    try:
        publish_answer(studio, answer_sources(response()), identity, record["data"], record["id"])
    finally:
        current_run_id.reset(token)
    studio._update_run("source", status="completed")
    return record, {"source_run_id": "source", "source_index": 0}


@pytest.fixture
def documents(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-original-token")
    studio = StudioService(tmp_path)
    calls = []
    studio.research.request = lambda *args, **kwargs: (
        calls.append((args, kwargs))
        or {"content": b"Original policy", "content_type": "text/plain"}
    )
    record, arguments = source_run(studio)
    try:
        yield studio, record, arguments, calls
    finally:
        studio.close()


def test_canonical_read_ignores_no_urls_and_reuses_exact_private_revision(documents):
    studio, record, arguments, calls = documents
    first = download_original(studio, record["data"], None, arguments, record["id"])
    assert len(calls) == 1
    args, options = calls[0]
    assert args == (
        "GET",
        "https://example.org/api/v1/kb/selected/resource/r/file/file/download/field",
        {"X-NUCLIA-SERVICEACCOUNT": "Bearer synthetic-original-token"},
    )
    assert options["binary"] is True and options["response_limit"] == 16 * 1024 * 1024
    assert first["remote_revision_pinned"] is False and first["cached"] is False
    artifact = first["artifacts"][0]
    assert studio.artifacts.get(studio.artifacts.revision(artifact["id"], 1)) == b"Original policy"
    second = download_original(studio, record["data"], None, arguments, record["id"])
    assert second["cached"] is True and second["artifacts"] == first["artifacts"]
    assert len(calls) == 1
    assert "synthetic-original-token" not in str(studio.records("source-originals", limit=None))


@pytest.mark.parametrize(
    "change",
    [
        {"url": "https://evil.invalid"},
        {"source_index": True},
        {"source_index": -1},
        {"source_index": 40},
        {"source_index": 1},
        {"source_run_id": "missing"},
    ],
)
def test_arbitrary_or_invalid_source_selection_fails_before_network(documents, change):
    studio, record, arguments, calls = documents
    with pytest.raises((ValueError, FileNotFoundError)):
        download_original(studio, record["data"], None, {**arguments, **change}, record["id"])
    assert not calls


@pytest.mark.parametrize(
    "change",
    [
        {"field_id": "t/file"},
        {"field_id": "f/../../escape"},
        {"resource_id": "https://evil.invalid"},
        {"resource_id": "r?token=secret"},
    ],
)
def test_nonfile_or_unsafe_provider_ids_never_advertise_original_access(change):
    assert (
        original_reference({}, "knowledge", {"field_id": "f/file", "resource_id": "r", **change})
        is None
    )


@pytest.mark.parametrize(
    "mode",
    [
        "disabled",
        "changed",
        "source-running",
        "permission",
        "cancel",
        "capacity",
        "oversized",
        "empty",
        "partial-format",
    ],
)
def test_read_failures_and_late_fences_do_not_publish_partial_files(documents, mode):
    studio, record, arguments, calls = documents
    before = len(studio.catalog.list("artifacts"))
    token = None

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        if mode in {"disabled", "changed"}:
            studio.catalog.save(
                "connectors",
                {
                    **record,
                    "data": {
                        **record["data"],
                        **(
                            {"enabled": False} if mode == "disabled" else {"knowledge_box": "other"}
                        ),
                    },
                },
            )
        if mode == "source-running":
            studio._update_run("source", status="running")
        if mode == "permission":
            raise PermissionError("Denied")
        if mode == "cancel":
            studio._update_run("download", cancel_requested=True)
        if mode == "oversized":
            return {"content": b"x" * (16 * 1024 * 1024 + 1), "content_type": "application/pdf"}
        if mode == "empty":
            return {"content": b"", "content_type": "application/pdf"}
        if mode == "partial-format":
            return {"content": "not-bytes", "content_type": "text/plain"}
        return {"content": b"Original", "content_type": "text/plain"}

    studio.research.request = request
    if mode in {"cancel", "capacity"}:
        studio._write(
            studio._path("runs", "download"),
            {
                "id": "download",
                "kind": "tool",
                "provider": "local-tool",
                "status": "running",
                "created_at": datetime.now(UTC).isoformat(),
                "structured_content": [{}] * (40 if mode == "capacity" else 0),
            },
        )
        token = current_run_id.set("download")
    try:
        with pytest.raises((ValueError, PermissionError)):
            download_original(studio, record["data"], None, arguments, record["id"])
        assert len(studio.catalog.list("artifacts")) == before
        assert not studio.records("source-originals", limit=None)
    finally:
        if token is not None:
            current_run_id.reset(token)
        if mode == "source-running":
            studio._update_run("source", status="completed")


def test_queue_reuses_existing_tool_job_and_second_request_does_not_redownload(documents):
    import time

    studio, _, arguments, calls = documents
    for _ in range(2):
        run = queue_original(studio, arguments, None)
        for _ in range(500):
            run = studio.run(run["id"])
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert run["status"] == "completed", run
        assert len(run["structured_content"]) == 1
    assert len(calls) == 1


def test_private_source_and_saved_original_cannot_cross_users(team):
    studio, _, _, request, _ = team
    record, arguments = source_run(studio, "alice")
    calls = []
    studio.research.request = lambda *a, **k: (
        calls.append(a) or {"content": b"Private original", "content_type": "text/plain"}
    )
    denied = request("/api/studio/original-document", arguments, user="bob")
    assert denied[0] in {403, 404}, denied
    assert not calls
    result = asyncio.run(
        studio.connections.execute(
            record["id"], {"action": "download_original", "arguments": arguments}, "alice"
        )
    )
    artifact = result["artifacts"][0]
    path = f"/api/catalog/artifacts/{artifact['id']}/versions/{artifact['revision']}"
    assert request(path, user="alice")[0] == 200
    assert request(path, user="bob")[0] in {403, 404}
    assert len(calls) == 1


def test_saved_connection_changes_block_cached_access_and_forged_citations(documents):
    studio, record, arguments, calls = documents
    download_original(studio, record["data"], None, arguments, record["id"])
    changed = studio.catalog.save(
        "connectors", {**record, "data": {**record["data"], "knowledge_box": "other"}}
    )
    with pytest.raises(ValueError, match="changed"):
        download_original(studio, changed["data"], None, arguments, record["id"])
    assert len(calls) == 1


def test_completed_original_receipt_survives_workspace_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-original-token")
    studio = StudioService(tmp_path)
    try:
        record, arguments = source_run(studio)
        studio.research.request = lambda *a, **k: {
            "content": b"Pinned original",
            "content_type": "text/plain",
        }
        saved = download_original(studio, record["data"], None, arguments, record["id"])
    finally:
        studio.close()
    resumed = StudioService(tmp_path)
    try:
        resumed.research.request = lambda *a, **k: pytest.fail(
            "Receipt recovery fetched the provider again"
        )
        result = download_original(resumed, record["data"], None, arguments, record["id"])
        assert result["cached"] and result["artifacts"] == saved["artifacts"]
    finally:
        resumed.close()


def test_concurrent_reads_publish_only_one_private_original(documents):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    studio, record, arguments, calls = documents
    barrier = threading.Barrier(2)

    def request(*args, **kwargs):
        calls.append(args)
        barrier.wait(timeout=5)
        return {"content": b"Concurrent original", "content_type": "text/plain"}

    studio.research.request = request
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: download_original(studio, record["data"], None, arguments, record["id"]),
                range(2),
            )
        )
    assert len(calls) == 2 and results[0]["artifacts"] == results[1]["artifacts"]
    assert sorted(result["cached"] for result in results) == [False, True]
    assert len(studio.catalog.list("artifacts")) == 3

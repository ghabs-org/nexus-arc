"""Local vector indexing: privacy, cache identity, ranking and bounded recovery."""

import base64
import math
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_embeddings import LocalEmbeddingsUnavailableError, StudioEmbeddings
from nexus.core.studio_knowledge_vectors import rank_fusion, unit_vector
from nexus.core.studio_team import NexusTeamPolicy


class Embeddings:
    cache_identity = "synthetic-model-v1"
    kind = "local"
    max_batch_size = 4

    def __init__(self):
        self.calls = []

    def __call__(self, texts, actor):
        self.calls.append(list(texts))
        return [
            [1 if "refund" in text.lower() else 0, 1 if "invoice" in text.lower() else 0, 0.01]
            for text in texts
        ]


def done(studio, run):
    for _ in range(800):
        value = studio.run(run["id"])
        if value["status"] not in {"queued", "running"}:
            return value
        time.sleep(0.01)
    pytest.fail("Job did not finish")


def attach(studio, project, name, text):
    artifact = studio.catalog.save(
        "artifacts",
        {
            "name": name,
            "data": {
                "filename": name + ".md",
                "content_base64": base64.b64encode(text.encode()).decode(),
            },
        },
    )
    doc = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
    assert done(studio, {"id": doc["run_id"]})["status"] == "completed"
    return artifact, doc


@pytest.fixture
def indexed(tmp_path):
    provider = Embeddings()
    studio = StudioService(tmp_path, knowledge_options={"embeddings": provider})
    project = studio.knowledge.save({"name": "Sources"}, None)
    attach(studio, project, "Returns", "A refund needs a receipt.")
    attach(studio, project, "Invoices", "An invoice needs a number.")
    yield studio, project, provider
    studio.team = None
    studio.close()


def test_vectors_build_only_after_explicit_search_then_reuse_documents(indexed):
    studio, project, provider = indexed
    assert provider.calls == []
    studio.knowledge.search(project["id"], "refund", None)
    assert provider.calls == []
    first = studio.knowledge.search(project["id"], "refund", None, "semantic")
    assert first["items"][0]["filename"] == "Returns.md"
    assert first["retrieval"]["document_cache_misses"] == 2
    second = studio.knowledge.search(project["id"], "UNSAVED_QUERY_2398 invoice", None, "semantic")
    assert second["items"][0]["filename"] == "Invoices.md"
    assert second["retrieval"]["document_cache_hits"] == 2
    assert provider.calls[-1] == ["UNSAVED_QUERY_2398 invoice"]
    assert b"UNSAVED_QUERY_2398" not in studio.knowledge.index_path.read_bytes()


def test_cache_survives_restart_and_model_change_invalidates_it(indexed):
    studio, project, provider = indexed
    studio.knowledge.search(project["id"], "refund", None, "semantic")
    studio.close()
    second = StudioService(studio.root, knowledge_options={"embeddings": provider})
    try:
        assert (
            second.knowledge.search(project["id"], "refund", None, "semantic")["retrieval"][
                "document_cache_hits"
            ]
            == 2
        )
        provider.cache_identity = "replacement-model"
        assert (
            second.knowledge.search(project["id"], "refund", None, "semantic")["retrieval"][
                "document_cache_misses"
            ]
            == 2
        )
    finally:
        second.close()


def test_project_and_requesting_account_scopes_do_not_share_vectors(indexed):
    studio, project, _ = indexed
    studio.knowledge.search(project["id"], "refund", None, "semantic")
    admin = {"nexus_id": "admin", "role": "owner"}
    assert (
        studio.knowledge.search(project["id"], "refund", admin, "semantic")["retrieval"][
            "document_cache_hits"
        ]
        == 0
    )
    other = studio.knowledge.save({"name": "Other project"}, None)
    for artifact in studio.catalog.list("artifacts"):
        doc = studio.knowledge.attach(other["id"], {"artifact_id": artifact["id"]}, None)
        assert done(studio, {"id": doc["run_id"]})["status"] == "completed"
    assert (
        studio.knowledge.search(other["id"], "refund", None, "semantic")["retrieval"][
            "document_cache_hits"
        ]
        == 0
    )


def test_corrupt_cache_recomputes_only_bad_rows_and_respects_lru_bound(indexed, monkeypatch):
    studio, project, provider = indexed
    studio.knowledge.search(project["id"], "refund", None, "semantic")
    with studio.knowledge._db() as db:
        db.execute("UPDATE vectors SET vector='null' WHERE rowid=(SELECT min(rowid) FROM vectors)")
    found = studio.knowledge.search(project["id"], "refund", None, "semantic")
    assert found["retrieval"]["document_cache_hits"] == 1
    assert found["retrieval"]["document_cache_misses"] == 1
    assert len(provider.calls[-1]) == 2  # fresh query + the one bad document
    monkeypatch.setattr("nexus.core.studio_knowledge_vectors.CACHE_ROWS", 1)
    provider.cache_identity = "evict-cache"
    studio.knowledge.search(project["id"], "refund", None, "semantic")
    with studio.knowledge._db() as db:
        assert db.execute("SELECT count(*) FROM vectors").fetchone()[0] == 1


def test_numeric_value_and_byte_caps_fail_or_evict_explicitly(indexed, monkeypatch):
    studio, project, _ = indexed
    monkeypatch.setattr("nexus.core.studio_knowledge_vectors.MAX_VALUES", 2)
    with pytest.raises(ValueError, match="numeric-value limit"):
        studio.knowledge.search(project["id"], "refund", None, "semantic")
    with studio.knowledge._db() as db:
        assert db.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0
    monkeypatch.setattr("nexus.core.studio_knowledge_vectors.MAX_VALUES", 100)
    monkeypatch.setattr("nexus.core.studio_knowledge_vectors.CACHE_BYTES", 1)
    studio.knowledge.search(project["id"], "refund", None, "semantic")
    with studio.knowledge._db() as db:
        assert db.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0


def test_legacy_or_lost_derived_index_rebuilds_without_duplicate_chunks(indexed):
    studio, project, _ = indexed
    with sqlite3.connect(studio.knowledge.index_path) as db:
        db.execute("DROP TABLE index_metadata")
        db.execute("INSERT INTO chunks SELECT * FROM chunks")
    with ThreadPoolExecutor(2) as pool:
        results = list(
            pool.map(lambda _: studio.knowledge.search(project["id"], "refund", None), range(2))
        )
    assert all(len(found["items"]) == 1 for found in results)
    with studio.knowledge._db() as db:
        assert db.execute("SELECT count(*) FROM chunks").fetchone()[0] == 2
    studio.knowledge.index_path.unlink()
    assert (
        studio.knowledge.search(project["id"], "invoice", None)["items"][0]["filename"]
        == "Invoices.md"
    )


@pytest.mark.parametrize(
    "invalid", [[], [True], ["1"], [float("nan")], [float("inf")], [10**1000], None]
)
def test_invalid_vectors_are_errors_not_fallback(invalid):
    with pytest.raises(ValueError):
        unit_vector(invalid)


def test_stable_cosine_normalization_and_hybrid_rank_fusion():
    assert unit_vector([1e308, 1e308]) == pytest.approx([1 / math.sqrt(2)] * 2)
    assert unit_vector([0, 0]) == [0, 0]
    semantic = [{"chunk_id": "b", "score": 0.99}, {"chunk_id": "a", "score": 0.1}]
    lexical = [{"chunk_id": "a", "score": 10000}, {"chunk_id": "c", "score": 9999}]
    assert rank_fusion(semantic, lexical)[0]["chunk_id"] == "a"


def test_local_unavailable_is_visible_but_other_provider_errors_propagate(indexed):
    studio, project, _ = indexed

    def unavailable(texts, actor):
        raise LocalEmbeddingsUnavailableError(
            "Local embedding service is unavailable; using local lexical search"
        )

    unavailable.kind = "local"
    studio.knowledge.embeddings = unavailable
    result = studio.knowledge.search(project["id"], "refund", None, "hybrid")
    assert result["mode"] == "lexical" and "Local embedding" in result["notice"]
    assert result["items"][0]["filename"] == "Returns.md"
    studio.knowledge.embeddings = lambda texts, actor: [[True]] * len(texts)
    with pytest.raises(ValueError, match="numeric"):
        studio.knowledge.search(project["id"], "refund", None, "semantic")


def test_revocation_during_embedding_call_prevents_return_and_cache_publication(indexed):
    studio, project, provider = indexed
    entered, release = threading.Event(), threading.Event()
    doc = studio.knowledge.documents(project["id"], None)[0]
    for artifact in studio.catalog.list("artifacts"):
        studio._write(
            studio.catalog._path("artifacts", artifact["id"]), {**artifact, "owner_id": "desktop"}
        )
    studio.team = SimpleNamespace(visible=NexusTeamPolicy.visible)
    actor = {"nexus_id": "desktop", "role": "builder"}

    class Blocking(Embeddings):
        def __call__(self, texts, actor):
            entered.set()
            assert release.wait(5)
            return super().__call__(texts, actor)

    studio.knowledge.embeddings = Blocking()
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                studio.knowledge.search, project["id"], "refund", actor, "semantic"
            )
            assert entered.wait(5)
            current = studio.catalog.get("artifacts", doc["artifact_id"])
            studio._write(
                studio.catalog._path("artifacts", current["id"]), {**current, "owner_id": "other"}
            )
            release.set()
            with pytest.raises(PermissionError):
                future.result(timeout=5)
    finally:
        release.set()
        studio.team = None
    with studio.knowledge._db() as db:
        assert db.execute("SELECT count(*) FROM vectors").fetchone()[0] == 0


def test_embedding_cache_revision_changes_identity_without_resolving_secret():
    config = {
        "kind": "local",
        "url": "http://127.0.0.1:9999/v1",
        "allow_private": True,
        "model": "same-name",
        "token_env": "UNRESOLVED_EMBEDDING_SECRET",
    }
    first = StudioEmbeddings(config)
    second = StudioEmbeddings({**config, "cache_revision": "2"})
    assert first.cache_identity != second.cache_identity
    assert len(first.cache_identity) == 64


def test_project_listing_discloses_api_source_processing_without_secrets(indexed):
    import json

    studio, project, _ = indexed
    studio.knowledge.embeddings = StudioEmbeddings(
        {
            "kind": "api",
            "url": "https://private-provider.example/v1",
            "model": "private-model",
            "token_env": "UNRESOLVED_PRIVATE_TOKEN",
        }
    )
    listing = studio.features.get(["projects"], {}, None)
    assert listing["items"][0]["id"] == project["id"]
    options = listing["retrieval_options"]
    assert options["embedding_kind"] == "api" and "API usage charges" in options["notice"]
    assert "selected source text" in options["notice"]
    assert "private-provider" not in json.dumps(options) and "UNRESOLVED" not in json.dumps(options)


def test_reference_configuration_export_preserves_optional_search_mode(indexed):
    studio, project, _ = indexed
    studio.knowledge.save({**project, "retrieval_mode": "hybrid"}, None)
    exported = studio.features.get(["projects", project["id"], "export"], {}, None)
    assert exported["format"] == "nexus-knowledge-project/v1"
    code, imported = studio.features.post(["projects", "import"], exported, None)
    assert code == 201 and imported["retrieval_mode"] == "hybrid"
    del exported["project"]["retrieval_mode"]
    _, legacy = studio.features.post(["projects", "import"], exported, None)
    assert legacy["retrieval_mode"] == "lexical"


def test_local_tls_verification_error_does_not_become_lexical_fallback(indexed, tmp_path):
    import ssl
    import subprocess
    from http.server import ThreadingHTTPServer

    studio, project, _ = indexed
    config = tmp_path / "tls.conf"
    config.write_text(
        "[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\n"
    )
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-config",
            str(config),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args: None)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        studio.knowledge.embeddings = StudioEmbeddings(
            {
                "kind": "local",
                "url": f"https://localhost:{server.server_port}/v1",
                "model": "synthetic",
            }
        )
        with pytest.raises(ValueError, match="TLS verification failed"):
            studio.knowledge.search(project["id"], "refund", None, "semantic")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

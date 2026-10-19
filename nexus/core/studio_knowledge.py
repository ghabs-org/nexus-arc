"""Owner-scoped knowledge projects, immutable documents and local FTS retrieval."""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from nexus.core.studio_catalog import _text
from nexus.core.studio_documents import DocumentExtractionError, extract_document
from nexus.core.studio_embeddings import LocalEmbeddingsUnavailableError
from nexus.core.studio_knowledge_vectors import StudioKnowledgeVectors, rank_fusion


def owner(actor):
    return actor["nexus_id"] if actor else "desktop"


class IndexInterruptedError(ValueError):
    """Extraction may finish, but its stopped/superseded job must not publish."""


class StudioKnowledge:
    def __init__(self, studio, embeddings=None):
        self.studio, self.embeddings = studio, embeddings
        self.vectors = StudioKnowledgeVectors(self)
        namespace = studio.sql_state.namespace if studio.sql_state else str(studio.root)
        self.index_path = studio.state / (
            "knowledge-index-" + hashlib.sha256(namespace.encode()).hexdigest()[:16] + ".sqlite"
        )

    def visible(self, record, actor):
        return record.get("owner_id") == owner(actor) or bool(
            actor and actor.get("role") == "owner"
        )

    def get(self, identity, actor, archived=False):
        record = self.studio._read(self.studio._path("knowledge-projects", identity))
        if not self.visible(record, actor):
            raise PermissionError("Knowledge project belongs to another account")
        if record.get("archived") and not archived:
            raise ValueError("Restore this knowledge project before use")
        return record

    def list(self, actor):
        return [
            r
            for r in self.studio.records("knowledge-projects", limit=None)
            if self.visible(r, actor)
        ]

    def retrieval_options(self):
        """Disclose source/billing boundaries without exposing credentials or URLs."""
        provider = self.embeddings
        kind = getattr(provider, "kind", "configured") if provider is not None else "none"
        if kind == "api":
            notice = "Semantic and hybrid search send selected source text to the configured API model. API usage charges may apply. Local text search keeps documents on this server."
        elif kind == "local":
            notice = "Semantic and hybrid search use the configured local model. If it is unavailable, search visibly falls back to local text search."
        elif kind == "none":
            notice = "No embedding service is configured. Search uses local text matching."
        else:
            notice = "Semantic and hybrid search use this workspace's configured embedding adapter. Ask the workspace owner where it processes source text."
        return {
            "version": 1,
            "embedding_kind": kind if kind in {"api", "local", "none"} else "configured",
            "notice": notice,
        }

    def save(self, data, actor):
        identity = data.get("id") or uuid.uuid4().hex
        with self.studio.lock:
            path = self.studio._path("knowledge-projects", identity)
            previous = self.get(identity, actor, True) if path.exists() else {}
            if type(data.get("revision", 0)) is not int or data.get("revision", 0) != previous.get(
                "revision", 0
            ):
                raise ValueError("Project changed; reopen its latest revision")
            settings = self.settings(data, previous)
            record = {
                **settings,
                "id": identity,
                "version": 1,
                "owner_id": previous.get("owner_id", owner(actor)),
                "revision": previous.get("revision", 0) + 1,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            if previous:
                self.studio._write(
                    self.studio._path(
                        "knowledge-project-history", f"{identity}-{previous['revision']}"
                    ),
                    previous,
                )
            self.studio._write(path, record)
            return record

    def settings(self, data, previous=None):
        """Validate reusable settings without writing records or calling a model."""
        previous = previous or {}
        agent_id = data.get("default_agent_id") or None
        if agent_id:
            self.studio.definition("agents", agent_id)
        instructions = data.get("instructions", "")
        if not isinstance(instructions, str) or len(instructions) > 16000:
            raise ValueError("Project instructions must be text up to 16000 characters")
        archived = data.get("archived", False)
        if type(archived) is not bool:
            raise ValueError("Archived must be true or false")
        mode = data.get("retrieval_mode", previous.get("retrieval_mode", "lexical"))
        if not isinstance(mode, str) or mode not in {"lexical", "semantic", "hybrid"}:
            raise ValueError("Choose lexical, semantic or hybrid search")
        return {
            "name": _text(data.get("name"), "Project name", 200),
            "instructions": instructions,
            "default_agent_id": agent_id,
            "archived": archived,
            "retrieval_mode": mode,
        }

    def documents(self, identity, actor):
        project = self.get(identity, actor)
        result = []
        for record in self.studio.records("knowledge-documents", limit=None):
            if record["project_id"] != identity or record.get("owner_id") != project["owner_id"]:
                continue
            try:
                artifact = self.studio.catalog.get(
                    "artifacts", record["artifact_id"], archived=True
                )
                self.studio.collaboration.owned(artifact, actor)
                revision = self.studio.artifacts.revision(
                    record["artifact_id"], record["artifact_revision"]
                )
                self.studio.collaboration.owned(revision, actor)
            except (PermissionError, FileNotFoundError, ValueError):
                continue
            retryable = record["status"] in {"failed", "interrupted"}
            if record["status"] != "ready" and record.get("run_id"):
                try:
                    retryable = self.studio.run(record["run_id"])["status"] in {
                        "cancelled",
                        "interrupted",
                        "failed",
                    }
                except FileNotFoundError:
                    retryable = True
            result.append({**record, "retryable": retryable})
        return result

    def attach(self, identity, data, actor):
        project = self.get(identity, actor)
        artifact = self.studio.catalog.get("artifacts", data.get("artifact_id"))
        if self.studio.team and not self.studio.team.visible(artifact, actor):
            raise PermissionError("Artifact belongs to another account")
        artifact = self.studio.artifacts.revision(artifact["id"], data.get("artifact_revision"))
        self.studio.collaboration.owned(artifact, actor)
        key = hashlib.sha256(
            f"{identity}:{artifact['id']}:{artifact['revision']}".encode()
        ).hexdigest()
        with self.studio.lock:
            path = self.studio._path("knowledge-documents", key)
            if path.exists():
                return self.studio._read(path)
            record = {
                "id": key,
                "version": 1,
                "project_id": identity,
                "owner_id": project["owner_id"],
                "artifact_id": artifact["id"],
                "artifact_revision": artifact["revision"],
                "filename": artifact["data"]["filename"],
                "status": "queued",
                "chunks": [],
            }
            self.studio._write(path, record)
            run = self.studio.start_run(
                {
                    "kind": "knowledge-index",
                    "task": "Index " + record["filename"],
                    "document_id": key,
                    "requester_nexus_id": actor["nexus_id"] if actor else None,
                }
            )
            record["run_id"] = run["id"]
            self.studio._write(path, record)
        return record

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.index_path, timeout=15)
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(id UNINDEXED, document_id UNINDEXED, text)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS index_metadata (key TEXT PRIMARY KEY, value INTEGER)"
                )
                db.execute(
                    "CREATE TABLE IF NOT EXISTS vectors (scope TEXT, document_id TEXT, chunk_id TEXT, content_hash TEXT, vector TEXT, used_at INTEGER, PRIMARY KEY(scope,document_id,chunk_id))"
                )
                db.execute("CREATE INDEX IF NOT EXISTS vectors_lru ON vectors(used_at)")
                if not db.execute(
                    "SELECT value FROM index_metadata WHERE key='chunks-v1'"
                ).fetchone():
                    db.execute("DELETE FROM chunks")
                    for doc in self.studio.records("knowledge-documents", limit=None):
                        if doc.get("status") == "ready":
                            db.executemany(
                                "INSERT INTO chunks VALUES(?,?,?)",
                                [(c["id"], doc["id"], c["text"]) for c in doc["chunks"]],
                            )
                    db.execute("INSERT INTO index_metadata VALUES('chunks-v1',1)")
                yield db
        finally:
            db.close()

    def retry(self, identity, document_id, actor):
        """Retry local indexing explicitly, never duplicate a live index job."""
        with self.studio.lock:
            documents = {record["id"]: record for record in self.documents(identity, actor)}
            if document_id not in documents:
                raise FileNotFoundError("Knowledge document not found")
            record = documents[document_id]
            if record.get("run_id"):
                run = self.studio.run(record["run_id"])
                if run["status"] in {"running", "queued"}:
                    raise ValueError("Document indexing is already in progress")
            if record["status"] == "ready":
                raise ValueError("This document revision is already indexed")
            run = self.studio.start_run(
                {
                    "kind": "knowledge-index",
                    "task": "Index " + record["filename"],
                    "document_id": document_id,
                    "requester_nexus_id": actor["nexus_id"] if actor else None,
                }
            )
            record = {**record, "status": "queued", "error": None, "run_id": run["id"]}
            self.studio._write(self.studio._path("knowledge-documents", document_id), record)
            return record

    def index(self, document_id, run_id=None):
        path = self.studio._path("knowledge-documents", document_id)

        def authorize(record):
            if run_id:
                run = self.studio.run(run_id)
                if (
                    record.get("run_id") != run_id
                    or run.get("cancel_requested")
                    or run["status"] != "running"
                    or self.studio.closing
                ):
                    raise IndexInterruptedError("Index job stopped or superseded; retry explicitly")
                if self.studio.sql_state:
                    with self.studio.sql_state.transaction() as connection:
                        self.studio.sql_state.check_fence(connection)
                actor = (
                    {
                        "nexus_id": run.get("requester_nexus_id"),
                        "role": self.studio.team.bindings.get(run.get("requester_nexus_id")),
                    }
                    if self.studio.team
                    else None
                )
                if actor is None and run.get("requester_nexus_id"):
                    actor = {"nexus_id": run["requester_nexus_id"], "role": "operator"}
                if self.studio.team:
                    self.studio.team.assert_execution(run.get("requester_nexus_id"))
                self.validate_snapshot(
                    {
                        "project": {"id": record["project_id"]},
                        "items": [
                            {
                                "document_id": record["id"],
                                "artifact_id": record["artifact_id"],
                                "artifact_revision": record["artifact_revision"],
                            }
                        ],
                    },
                    actor,
                )

        with self.studio.lock:
            record = self.studio._read(path)
            authorize(record)
            record.update(status="indexing", error=None)
            self.studio._write(path, record)
        try:
            artifact = self.studio.artifacts.revision(
                record["artifact_id"], record["artifact_revision"]
            )
            pages = extract_document(
                record["filename"],
                self.studio.artifacts.get(artifact),
                self.studio.artifacts.pdf_text,
            )
            chunks = []
            for page in pages:
                for offset in range(0, len(page["text"]), 1200):
                    text = page["text"][offset : offset + 1600]
                    if text.strip():
                        chunks.append(
                            {
                                "id": f"{document_id}-{len(chunks)}",
                                "page": page["page"],
                                "text": text,
                            }
                        )
            if len(chunks) > 256:
                raise DocumentExtractionError("Document exceeds 256 search chunks")
            if not chunks:
                raise DocumentExtractionError(
                    "Document has no extractable text; provide a text or OCR copy"
                )
            with self.studio.lock:
                authorize(self.studio._read(path))
                with self._db() as db:
                    db.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
                    db.executemany(
                        "INSERT INTO chunks VALUES(?,?,?)",
                        [(c["id"], document_id, c["text"]) for c in chunks],
                    )
                record.update(status="ready", chunks=chunks)
                self.studio._write(path, record)
            return json.dumps({"document_id": document_id, "chunks": len(chunks)})
        except Exception as exc:
            with self.studio.lock:
                current = self.studio._read(path)
                if run_id is None or current.get("run_id") == run_id:
                    current.update(
                        status="interrupted"
                        if isinstance(exc, IndexInterruptedError)
                        else "failed",
                        error="Indexing interrupted; retry explicitly"
                        if isinstance(exc, IndexInterruptedError)
                        else str(exc)
                        if isinstance(exc, DocumentExtractionError)
                        else "Document indexing failed; inspect its format and extraction limits",
                    )
                    self.studio._write(path, current)
            raise

    def validate_snapshot(self, snapshot, actor):
        """Recheck current access while retaining pinned content and settings."""
        project = self.get(snapshot["project"]["id"], actor)
        for source in snapshot["items"]:
            document = self.studio._read(
                self.studio._path("knowledge-documents", source["document_id"])
            )
            if (
                document["project_id"] != project["id"]
                or document.get("owner_id") != project["owner_id"]
                or document["artifact_id"] != source["artifact_id"]
                or document["artifact_revision"] != source["artifact_revision"]
            ):
                raise PermissionError(
                    "Pinned knowledge source is no longer available to this project"
                )
            current = self.studio.catalog.get("artifacts", source["artifact_id"], archived=True)
            self.studio.collaboration.owned(current, actor)
            revision = self.studio.artifacts.revision(
                source["artifact_id"], source["artifact_revision"]
            )
            self.studio.collaboration.owned(revision, actor)

    def search(self, identity, query, actor, mode="lexical"):
        project = self.get(identity, actor)
        query = _text(query, "Search query", 2000)
        if not isinstance(mode, str) or mode not in {"lexical", "semantic", "hybrid"}:
            raise ValueError("Choose lexical, semantic or hybrid search")
        # Publish metadata only after FTS commit; old ready revisions survive
        # replacement failure. Keep old bytes for existing pinned citations.
        latest = {}
        for record in self.documents(identity, actor):
            if record["status"] == "ready" and record["artifact_revision"] > latest.get(
                record["artifact_id"], {}
            ).get("artifact_revision", 0):
                latest[record["artifact_id"]] = record
        documents = list(latest.values())
        by_id = {record["id"]: record for record in documents}
        references = [
            {
                "document_id": doc["id"],
                "artifact_id": doc["artifact_id"],
                "artifact_revision": doc["artifact_revision"],
            }
            for doc in documents
        ]

        def authorize():
            self.validate_snapshot({"project": project, "items": references}, actor)

        terms = re.findall(r"\w+", query)[:30]
        results, notice = [], None
        settings = {"version": 1, "requested_mode": mode, "effective_mode": mode}
        if terms and documents:
            with self._db() as db:
                placeholders = ",".join("?" for _ in by_id)
                rows = db.execute(
                    f"SELECT id,document_id,text,bm25(chunks) FROM chunks WHERE chunks MATCH ? AND document_id IN ({placeholders}) ORDER BY bm25(chunks),id LIMIT 8",
                    [
                        " OR ".join(
                            '"' + term + '"' + ("*" if len(term) >= 3 else "") for term in terms
                        ),
                        *by_id,
                    ],
                ).fetchall()
            results = [
                {"chunk_id": row[0], "document_id": row[1], "text": row[2], "score": -row[3]}
                for row in rows
            ]
        if mode != "lexical":
            if self.embeddings is None:
                notice = "No embedding provider configured; using local lexical search"
            else:
                all_chunks = [(doc, chunk) for doc in documents for chunk in doc["chunks"]]
                try:
                    query_vector, vectors, stats = self.vectors.fetch(
                        identity, query, all_chunks, actor, authorize
                    )
                    settings.update(stats)
                    ranked = sorted(
                        [
                            {
                                "chunk_id": chunk["id"],
                                "document_id": doc["id"],
                                "text": chunk["text"],
                                "score": sum(
                                    a * b for a, b in zip(query_vector, vector, strict=True)
                                ),
                            }
                            for (doc, chunk), vector in zip(all_chunks, vectors, strict=True)
                        ],
                        key=lambda item: (-item["score"], item["chunk_id"]),
                    )
                    results = ranked[:8] if mode == "semantic" else rank_fusion(ranked[:8], results)
                except LocalEmbeddingsUnavailableError as exc:
                    if getattr(self.embeddings, "kind", None) != "local":
                        raise
                    notice = str(exc)
        for result in results:
            document = by_id[result["document_id"]]
            chunk = next(chunk for chunk in document["chunks"] if chunk["id"] == result["chunk_id"])
            result.update(
                artifact_id=document["artifact_id"],
                artifact_revision=document["artifact_revision"],
                filename=document["filename"],
                page=chunk["page"],
            )
        authorize()
        settings["effective_mode"] = mode if not notice else "lexical"
        return {
            "items": results,
            "notice": notice,
            "mode": settings["effective_mode"],
            "retrieval": settings,
        }

    def prepare(self, payload):
        # Only this service constructs authorized pinned retrieval context.
        payload.pop("knowledge_snapshot", None)
        identity = payload.get("knowledge_project_id")
        if not identity:
            return payload
        actor = (
            {"nexus_id": payload.get("requester_nexus_id"), "role": payload.get("_role")}
            if self.studio.team
            else None
        )
        project = self.get(identity, actor)
        found = self.search(
            identity,
            payload.get("expanded_task", payload.get("task")),
            actor,
            payload.get("retrieval_mode", project.get("retrieval_mode", "lexical")),
        )
        payload["knowledge_snapshot"] = {"project": project, **found}
        if not payload.get("agent_id") and project.get("default_agent_id"):
            payload["agent_id"] = project["default_agent_id"]
        return payload

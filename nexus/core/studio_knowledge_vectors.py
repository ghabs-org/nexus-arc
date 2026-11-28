"""Bounded derived document vectors over the existing private knowledge index."""

import hashlib
import json
import math
import time

CACHE_ROWS = 4096
CACHE_BYTES = 64 * 1024 * 1024
MAX_CHUNKS = 4096
MAX_DIMENSIONS = 8192
MAX_VALUES = 4 * 1024 * 1024


def unit_vector(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_DIMENSIONS:
        raise ValueError("Embedding vectors need 1–8192 numeric dimensions")
    try:
        if any(type(number) not in {int, float} or not math.isfinite(number) for number in value):
            raise ValueError
        scale = max(abs(number) for number in value)
        scaled = [float(number / scale) for number in value] if scale else [0.0] * len(value)
        norm = math.hypot(*scaled)
        return [number / norm for number in scaled] if norm else scaled
    except (TypeError, OverflowError, ValueError):
        raise ValueError("Embedding provider returned invalid finite numeric vectors") from None


class StudioKnowledgeVectors:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    def fetch(self, project_id, query, chunks, actor, authorize):
        if len(chunks) > MAX_CHUNKS:
            raise ValueError("Vector search supports up to 4096 current chunks; use lexical search")
        provider = self.knowledge.embeddings
        identity = getattr(provider, "cache_identity", None)
        cacheable = isinstance(identity, str) and 1 <= len(identity) <= 256
        scope = hashlib.sha256(
            json.dumps(
                [
                    identity if cacheable else None,
                    project_id,
                    actor["nexus_id"] if actor else "desktop",
                ]
            ).encode()
        ).hexdigest()
        vectors, missing, hits = {}, [], 0
        values = 0
        if cacheable and chunks:
            with self.knowledge._db() as db:
                for doc, chunk in chunks:
                    digest = hashlib.sha256(chunk["text"].encode()).hexdigest()
                    row = db.execute(
                        "SELECT content_hash, vector FROM vectors WHERE scope=? AND document_id=? AND chunk_id=?",
                        (scope, doc["id"], chunk["id"]),
                    ).fetchone()
                    if row and row[0] == digest:
                        try:
                            cached = unit_vector(json.loads(row[1]))
                        except (ValueError, TypeError, OverflowError, RecursionError):
                            db.execute(
                                "DELETE FROM vectors WHERE scope=? AND document_id=? AND chunk_id=?",
                                (scope, doc["id"], chunk["id"]),
                            )
                        else:
                            values += len(cached)
                            if values > MAX_VALUES:
                                raise ValueError(
                                    "Vector search exceeds its numeric-value limit; use lexical search"
                                )
                            vectors[chunk["id"]] = cached
                            hits += 1
                            db.execute(
                                "UPDATE vectors SET used_at=? WHERE scope=? AND document_id=? AND chunk_id=?",
                                (time.time_ns(), scope, doc["id"], chunk["id"]),
                            )
                            continue
                    missing.append((doc, chunk))
        else:
            missing = list(chunks)
        # Query vectors are deliberately never persisted. Batch with missing
        # documents where possible, without exceeding the adapter's old limits.
        requests = [(None, {"id": "query", "text": query}), *missing]
        generated, dimensions = {}, None
        batch_size = getattr(provider, "max_batch_size", 128)
        if type(batch_size) is not int or not 1 <= batch_size <= 128:
            raise ValueError("Embedding adapter batch size must be 1–128")
        for offset in range(0, len(requests), batch_size):
            batch = requests[offset : offset + batch_size]
            authorize()
            raw = provider([chunk["text"] for _, chunk in batch], actor)
            if not isinstance(raw, list) or len(raw) != len(batch):
                raise ValueError("Embedding provider returned unexpected vector count")
            validated = [unit_vector(vector) for vector in raw]
            values += sum(len(vector) for vector in validated)
            if values > MAX_VALUES:
                raise ValueError(
                    "Vector search exceeds its numeric-value limit; use lexical search"
                )
            if dimensions is None:
                dimensions = len(validated[0])
            if any(len(vector) != dimensions for vector in validated):
                raise ValueError("Embedding provider returned mixed vector dimensions")
            for (_doc, chunk), vector in zip(batch, validated, strict=True):
                generated[chunk["id"]] = vector
        if any(len(vector) != dimensions for vector in vectors.values()):
            raise ValueError(
                "Embedding model dimensions changed; update its cache_revision to rebuild cached vectors"
            )
        authorize()
        if cacheable and missing:
            with self.knowledge._db() as db:
                for doc, chunk in missing:
                    db.execute(
                        "INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?)",
                        (
                            scope,
                            doc["id"],
                            chunk["id"],
                            hashlib.sha256(chunk["text"].encode()).hexdigest(),
                            json.dumps(
                                generated[chunk["id"]], allow_nan=False, separators=(",", ":")
                            ),
                            time.time_ns(),
                        ),
                    )
                count, size = db.execute(
                    "SELECT count(*), COALESCE(sum(length(vector)),0) FROM vectors"
                ).fetchone()
                if count > CACHE_ROWS or size > CACHE_BYTES:
                    for rowid, length in db.execute(
                        "SELECT rowid,length(vector) FROM vectors ORDER BY used_at,rowid"
                    ).fetchall():
                        db.execute("DELETE FROM vectors WHERE rowid=?", (rowid,))
                        count -= 1
                        size -= length
                        if count <= CACHE_ROWS and size <= CACHE_BYTES:
                            break
        vectors.update(generated)
        return (
            vectors.pop("query"),
            [vectors[chunk["id"]] for _, chunk in chunks],
            {
                "version": 1,
                "requested_provider": "local"
                if getattr(provider, "kind", None) == "local"
                else "api"
                if getattr(provider, "kind", None) == "api"
                else "configured",
                "provider_fingerprint": hashlib.sha256(identity.encode()).hexdigest()
                if cacheable
                else None,
                "document_cache_hits": hits,
                "document_cache_misses": len(missing),
            },
        )


def rank_fusion(semantic, lexical):
    """Combine ordered ranks instead of comparing incompatible score scales."""
    combined = {}
    for ordered in (semantic, lexical):
        for rank, result in enumerate(ordered, 1):
            key = result["chunk_id"]
            if key not in combined:
                combined[key] = {**result, "score": 0.0}
            combined[key]["score"] += 1.0 / (60 + rank)
    return sorted(combined.values(), key=lambda item: (-item["score"], item["chunk_id"]))[:8]

"""Explicit local or API embedding adapters; never discover or switch providers."""

import hashlib
import json
from urllib.parse import urlparse

from nexus.core.studio_catalog import _text
from nexus.core.studio_runtime import RuntimeServiceUnavailableError, endpoint, request_json


class LocalEmbeddingsUnavailableError(ValueError):
    """Only temporary local availability failures may use lexical fallback."""


class StudioEmbeddings:
    max_batch_size = 4  # Keep even 8192-dimensional responses below the 1 MiB transport bound.

    def __init__(self, config):
        if not isinstance(config, dict) or set(config) - {
            "url",
            "allow_private",
            "model",
            "token_env",
            "kind",
            "cache_revision",
        }:
            raise ValueError("Embedding configuration uses kind, url, model and token_env")
        self.config = dict(config)
        self.kind = config.get("kind", "local")
        if self.kind not in {"local", "api"}:
            raise ValueError("Embedding kind must be local or api")
        self.url = endpoint(config)
        parsed = urlparse(self.url)
        if self.kind == "local" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Local embeddings must use a loopback model endpoint")
        self.model = _text(config.get("model"), "Embedding model", 200)
        revision = config.get("cache_revision", "1")
        _text(revision, "Embedding cache revision", 200)
        self.cache_identity = hashlib.sha256(
            json.dumps({**config, "cache_revision": revision}, sort_keys=True).encode()
        ).hexdigest()
        if config.get("token_env"):
            from nexus.core.studio_connections import env_name

            env_name(config["token_env"])
        self.studio = None

    def __call__(self, texts, actor):
        import os

        if len(texts) > 257 or sum(len(t) for t in texts) > 512000:
            raise ValueError("Embedding batch exceeds document limits")
        headers = {}
        name = self.config.get("token_env")
        if name:
            values = os.environ
            if self.studio and self.studio.team:
                values, error = self.studio.team.environment_resolver(actor["nexus_id"])
                if error:
                    raise PermissionError("Embedding account credentials unavailable")
            token = values.get(name)
            if not token:
                raise PermissionError("Configure embedding provider credentials")
            headers["Authorization"] = "Bearer " + token
        # OpenAI-compatible embedding endpoint; local providers such as Ollama
        # expose the same interface. Provider origin/model are operator-owned.
        try:
            result = request_json(
                "POST",
                self.url + "/embeddings",
                headers=headers,
                body={"model": self.model, "input": texts},
            )
        except RuntimeServiceUnavailableError:
            if self.kind == "local":
                raise LocalEmbeddingsUnavailableError(
                    "Local embedding service is unavailable; using local lexical search"
                ) from None
            raise
        if not isinstance(result, dict):
            raise ValueError("Embedding provider returned invalid data")
        rows = result.get("data", [])
        if (
            not isinstance(rows, list)
            or any(
                not isinstance(row, dict)
                or type(row.get("index")) is not int
                or "embedding" not in row
                for row in rows
            )
            or sorted(row["index"] for row in rows) != list(range(len(texts)))
        ):
            raise ValueError("Embedding provider returned invalid indices")
        return [row["embedding"] for row in sorted(rows, key=lambda r: r["index"])]

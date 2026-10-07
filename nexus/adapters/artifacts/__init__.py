"""Artifact object storage: one interface, local or cloud bucket.

Backends share the :class:`ArtifactStore` shape so workflows run unchanged
locally (filesystem) and in production (S3). GCS/Azure arrive when a caller
needs them — add a subclass plus a factory branch.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path


class ArtifactStore(ABC):
    """Put/get/delete/list bytes under ``<bucket>/<key>``."""

    @abstractmethod
    def put(self, bucket: str, key: str, content: bytes, content_type: str = "") -> None: ...

    @abstractmethod
    def get(self, bucket: str, key: str) -> bytes | None: ...

    @abstractmethod
    def delete(self, bucket: str, key: str) -> None: ...

    @abstractmethod
    def list(self, bucket: str, prefix: str = "") -> list[str]: ...


class FilesystemStore(ArtifactStore):
    """Local parity backend rooted at *root* (``{root}/{bucket}/{key}``)."""

    def __init__(self, root: str | Path):
        self._root = Path(root)

    def _resolve(self, bucket: str, key: str) -> Path:
        candidate = (self._root / bucket / key).resolve()
        if candidate != self._root.resolve() and self._root.resolve() not in candidate.parents:
            raise ValueError(f"artifact key escapes store root: {key}")
        return candidate

    def put(self, bucket: str, key: str, content: bytes, content_type: str = "") -> None:
        path = self._resolve(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def get(self, bucket: str, key: str) -> bytes | None:
        path = self._resolve(bucket, key)
        return path.read_bytes() if path.is_file() else None

    def delete(self, bucket: str, key: str) -> None:
        path = self._resolve(bucket, key)
        if path.is_file():
            path.unlink()

    def list(self, bucket: str, prefix: str = "") -> list[str]:
        base = self._root / bucket
        if not base.is_dir():
            return []
        return sorted(
            str(p.relative_to(base))
            for p in base.rglob(f"{prefix}*")
            if p.is_file()
        )


class S3Store(ArtifactStore):
    """S3 backend; ``boto3`` required only when this backend is constructed."""

    def __init__(
        self,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ):
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for the s3 artifact store. "
                "Install it with: pip install nexus-arc[s3]"
            ) from exc
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    def put(self, bucket: str, key: str, content: bytes, content_type: str = "") -> None:
        kwargs: dict = {"Body": content}
        if content_type:
            kwargs["ContentType"] = content_type
        self._client.put_object(Bucket=bucket, Key=key, **kwargs)

    def get(self, bucket: str, key: str) -> bytes | None:
        try:
            body = self._client.get_object(Bucket=bucket, Key=key)["Body"]
        except Exception:
            return None
        data = body.read()
        return data if isinstance(data, bytes) else bytes(data)

    def delete(self, bucket: str, key: str) -> None:
        self._client.delete_object(Bucket=bucket, Key=key)

    def list(self, bucket: str, prefix: str = "") -> list[str]:
        keys: list[str] = []
        token: dict = {}
        while True:
            page = self._client.list_objects_v2(Bucket=bucket, Prefix=prefix, **token)
            keys.extend(obj["Key"] for obj in page.get("Contents", ()))
            if not page.get("IsTruncated"):
                return sorted(keys)
            token = {"ContinuationToken": page["NextContinuationToken"]}


def create_store(kind: str = "", **kwargs) -> ArtifactStore:
    """Build a store by kind (``filesystem`` default, ``s3``); env overrides.

    Env: ``ARTIFACT_STORE``, ``ARTIFACT_FS_ROOT``, ``S3_ENDPOINT_URL``,
    ``S3_REGION``, ``S3_ACCESS_KEY``, ``S3_SECRET_KEY``.
    """
    resolved = (kind or os.getenv("ARTIFACT_STORE", "filesystem")).strip().lower()
    if resolved == "filesystem":
        root = kwargs.get("root") or os.getenv("ARTIFACT_FS_ROOT", "./data/artifacts")
        return FilesystemStore(root)
    if resolved == "s3":
        return S3Store(
            endpoint_url=kwargs.get("endpoint_url") or os.getenv("S3_ENDPOINT_URL"),
            region=kwargs.get("region") or os.getenv("S3_REGION"),
            access_key=kwargs.get("access_key") or os.getenv("S3_ACCESS_KEY"),
            secret_key=kwargs.get("secret_key") or os.getenv("S3_SECRET_KEY"),
        )
    raise ValueError(f"Unknown artifact store: {resolved}")

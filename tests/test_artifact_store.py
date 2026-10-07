"""Artifact store round-trips and factory behavior."""

import os


def test_filesystem_roundtrip(tmp_path):
    from nexus.adapters.artifacts import FilesystemStore

    store = FilesystemStore(tmp_path)
    assert store.get("b", "missing") is None
    store.put("b", "a/1.txt", b"hello", "text/plain")
    assert store.get("b", "a/1.txt") == b"hello"
    assert store.list("b", "a/") == ["a/1.txt"]
    store.delete("b", "a/1.txt")
    assert store.get("b", "a/1.txt") is None


def test_filesystem_rejects_traversal(tmp_path):
    import pytest

    from nexus.adapters.artifacts import FilesystemStore

    store = FilesystemStore(tmp_path)
    with pytest.raises(ValueError, match="escapes store root"):
        store.put("b", "../../evil.txt", b"x")


def test_factory_defaults_and_env(tmp_path, monkeypatch):
    from nexus.adapters.artifacts import FilesystemStore, create_store

    monkeypatch.delenv("ARTIFACT_STORE", raising=False)
    assert isinstance(create_store(), FilesystemStore)
    monkeypatch.setenv("ARTIFACT_STORE", "filesystem")
    monkeypatch.setenv("ARTIFACT_FS_ROOT", str(tmp_path))
    store = create_store()
    assert isinstance(store, FilesystemStore)
    assert store._root == tmp_path


def test_factory_rejects_unknown(monkeypatch):
    import pytest

    from nexus.adapters.artifacts import create_store

    monkeypatch.setenv("ARTIFACT_STORE", "gcs")
    with pytest.raises(ValueError, match="Unknown artifact store"):
        create_store()


def test_s3_store_needs_boto3():
    import pytest

    from nexus.adapters.artifacts import S3Store

    try:
        import boto3  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="boto3"):
            S3Store()
    else:
        pytest.skip("boto3 installed; missing-dep path not testable here")


def test_s3_store_roundtrip_with_fake_client(monkeypatch):
    from nexus.adapters.artifacts import S3Store

    import sys
    import types

    objects: dict = {}

    class _Body:
        def __init__(self, data):
            self._data = data

        def read(self):
            return self._data

    class _Client:
        def put_object(self, Bucket, Key, **kwargs):
            objects[(Bucket, Key)] = kwargs["Body"]

        def get_object(self, Bucket, Key):
            if (Bucket, Key) not in objects:
                raise KeyError(Key)
            return {"Body": _Body(objects[(Bucket, Key)])}

        def delete_object(self, Bucket, Key):
            objects.pop((Bucket, Key), None)

        def list_objects_v2(self, Bucket, Prefix="", **kwargs):
            keys = sorted(k for (b, k) in objects if b == Bucket and k.startswith(Prefix))
            return {"Contents": [{"Key": k} for k in keys], "IsTruncated": False}

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *a, **k: _Client()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    store = S3Store()
    store.put("b", "k", b"v")
    assert store.get("b", "k") == b"v"
    assert store.list("b") == ["k"]
    assert store.get("b", "gone") is None
    store.delete("b", "k")
    assert store.list("b") == []

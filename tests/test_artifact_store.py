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

    monkeypatch.setenv("ARTIFACT_STORE", "ceph")
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


def test_gcs_roundtrip_with_fake_client(monkeypatch):
    import sys
    import types

    from nexus.adapters.artifacts import GCSStore, create_store

    objects: dict = {}
    storage = types.ModuleType("google.cloud.storage")

    class _Blob:
        def __init__(self, name):
            self.name = name

        def upload_from_string(self, content, content_type=None):
            objects[self.name] = bytes(content)

        def download_as_bytes(self):
            if self.name not in objects:
                raise FileNotFoundError(self.name)
            return objects[self.name]

        def delete(self):
            objects.pop(self.name, None)

    class _Bucket:
        def blob(self, key):
            return _Blob(key)

    class _Client:
        def __init__(self, project=None):
            pass

        def bucket(self, bucket):
            return _Bucket()

        def list_blobs(self, bucket, prefix=""):
            return [_Blob(name) for name in sorted(objects) if name.startswith(prefix)]

    storage.Client = _Client
    cloud = types.ModuleType("google.cloud")
    cloud.storage = storage
    google = types.ModuleType("google")
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage)

    store = GCSStore(project="test")
    assert store.get("b", "missing") is None
    store.put("b", "a/1.txt", b"hello", "text/plain")
    assert store.get("b", "a/1.txt") == b"hello"
    assert store.list("b", "a/") == ["a/1.txt"]
    store.delete("b", "a/1.txt")
    assert store.get("b", "a/1.txt") is None
    assert isinstance(create_store("gcs", project="test"), GCSStore)


def test_gcs_missing_dep_raises_helpfully(monkeypatch):
    import builtins
    import sys

    import pytest

    for mod in ("google", "google.cloud", "google.cloud.storage"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    real_import = builtins.__import__

    def _guarded(name, *args, **kwargs):
        if name == "google.cloud.storage" or name.startswith("google.cloud.storage."):
            raise ImportError("No module named 'google'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded)
    from nexus.adapters.artifacts import GCSStore

    with pytest.raises(ImportError, match="nexus-arc\\[gcs\\]"):
        GCSStore()


def test_azure_roundtrip_with_fake_client(monkeypatch):
    import sys
    import types

    from nexus.adapters.artifacts import AzureStore

    objects: dict = {}
    blob_mod = types.ModuleType("azure.storage.blob")

    class _Settings:
        def __init__(self, content_type=None):
            self.content_type = content_type

    class _Container:
        def upload_blob(self, key, content, **kwargs):
            objects[key] = bytes(content)

        def download_blob(self, key):
            if key not in objects:
                raise FileNotFoundError(key)

            class _Reader:
                @staticmethod
                def readall():
                    return objects[key]

            return _Reader()

        def delete_blob(self, key):
            objects.pop(key, None)

        def list_blobs(self, name_starts_with=""):
            return [types.SimpleNamespace(name=n) for n in sorted(objects) if n.startswith(name_starts_with)]

    class _Service:
        @staticmethod
        def from_connection_string(conn_str):
            return _Service()

        def get_container_client(self, bucket):
            return _Container()

    blob_mod.BlobServiceClient = _Service
    blob_mod.ContentSettings = _Settings
    storage = types.ModuleType("azure.storage")
    storage.blob = blob_mod
    azure = types.ModuleType("azure")
    azure.storage = storage
    monkeypatch.setitem(sys.modules, "azure", azure)
    monkeypatch.setitem(sys.modules, "azure.storage", storage)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", blob_mod)

    store = AzureStore(connection_string="fake")
    assert store.get("b", "missing") is None
    store.put("b", "a/1.txt", b"hello", "text/plain")
    assert store.get("b", "a/1.txt") == b"hello"
    assert store.list("b", "a/") == ["a/1.txt"]
    store.delete("b", "a/1.txt")
    assert store.get("b", "a/1.txt") is None


def test_azure_missing_dep_raises_helpfully(monkeypatch):
    import builtins
    import sys

    import pytest

    for mod in ("azure", "azure.storage", "azure.storage.blob"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    real_import = builtins.__import__

    def _guarded(name, *args, **kwargs):
        if name == "azure.storage.blob" or name.startswith("azure.storage.blob."):
            raise ImportError("No module named 'azure'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded)
    from nexus.adapters.artifacts import AzureStore

    with pytest.raises(ImportError, match="nexus-arc\\[azure\\]"):
        AzureStore(connection_string="fake")


def test_azure_needs_connection_string():
    import pytest

    from nexus.adapters.artifacts import AzureStore

    try:
        import azure.storage.blob  # noqa: F401
    except ImportError:
        pytest.skip("azure lib present check not applicable")
    with pytest.raises(ValueError, match="connection string"):
        AzureStore(connection_string=None)

"""Read-only provider capture and offline localization of artifact revisions.

All metadata and writes belong to the staged backup/restore directory. Neither
operation changes source records, lists buckets, or writes to a remote provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from types import SimpleNamespace

from nexus.core.studio_artifacts import ARTIFACT_LIMIT, StudioArtifacts, profile_settings

FORMAT = "nexus-studio-backup-artifacts/v1"


def records(state):
    for collection in ("catalog", "catalog-history", "configuration-journals"):
        for path in sorted((state / collection).rglob("*.json")):
            record = json.loads(path.read_text())
            if isinstance(record, dict) and record.get("kind") == "artifacts":
                yield path, record


def _metadata(data):
    from nexus.core.studio_backup import _relative

    identity = data.get("store_id")
    if not isinstance(identity, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", identity
    ):
        raise ValueError("Invalid artifact storage profile reference")
    key = data.get("blob_key")
    _relative(key)
    if len(key.encode()) > 4096:
        raise ValueError("Artifact object key exceeds the backup limit")
    if not isinstance(data.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
        raise ValueError("Invalid artifact checksum")
    if type(data.get("size")) is not int or not 0 <= data["size"] <= ARTIFACT_LIMIT:
        raise ValueError("Invalid artifact size or artifact exceeds 16 MiB")
    return {key: data[key] for key in ("store_id", "blob_key", "sha256", "size")}


def _reader(state, factory=None):
    from nexus.core.studio_backup import _relative

    context = SimpleNamespace(
        state=state,
        _path=lambda collection, identity: (
            state / collection / (_relative(identity).as_posix() + ".json")
        ),
        _read=lambda path: json.loads(path.read_text()),
    )
    return StudioArtifacts(context, store_factory=factory)


def plan(state):
    """Validate every reference and profile before any provider is constructed."""
    from nexus.core.studio_backup import _sha

    reader, profiles, objects, references = _reader(state), {}, {}, []
    for path, record in records(state):
        data = record.get("data")
        if not isinstance(data, dict):
            raise ValueError("Invalid artifact metadata")
        if "content_base64" in data:
            base64.b64decode(data["content_base64"], validate=True)
            continue
        if data.get("store_id", "local") == "local":
            from nexus.core.studio_backup import _relative

            blob = state / "artifact-objects" / "studio" / _relative(data["blob_key"])
            if not blob.is_file() or _sha(blob) != data["sha256"]:
                raise ValueError("Local artifact bytes are missing or corrupt")
            continue
        item = _metadata(data)
        identity = item["store_id"]
        if identity not in profiles:
            try:
                profile = reader.profile(identity)
            except (FileNotFoundError, KeyError):
                raise ValueError("Artifact storage profile is missing from the snapshot") from None
            if (
                not isinstance(profile, dict)
                or profile.get("id") != identity
                or set(profile) - {"id", "name", "kind", "bucket", "options", "verified"}
            ):
                raise ValueError("Invalid artifact storage profile")
            profiles[identity] = {**profile, **profile_settings(profile)}
        profile = profiles[identity]
        item.update(kind=profile["kind"], bucket=profile["bucket"])
        fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
        item["local_key"] = "backup-objects/" + fingerprint
        key = (identity, item["blob_key"])
        if key in objects and objects[key] != item:
            raise ValueError("Conflicting immutable artifact object references")
        objects[key] = item
        references.append((path, record, key))
    return profiles, objects, references


def verify(state, archive=None, *, allow_remote=False):
    """Verify archived coverage and bytes without connecting to a provider."""
    from nexus.core.studio_backup import _sha

    if archive is None and not allow_remote:
        if any(
            isinstance(record.get("data"), dict)
            and record["data"].get("store_id", "local") != "local"
            for _, record in records(state)
        ):
            raise ValueError(
                "Remote artifact bytes require a separate provider backup or explicit inclusion"
            )
    _, expected, references = plan(state)
    if archive is None:
        return references
    if (
        not isinstance(archive, dict)
        or set(archive) != {"format", "objects"}
        or archive["format"] != FORMAT
    ):
        raise ValueError("Unsupported remote artifact backup inventory")
    items = archive["objects"]
    if not isinstance(items, list) or len(items) != len(expected):
        raise ValueError("Remote artifact backup coverage does not match saved revisions")
    actual = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "store_id",
            "kind",
            "bucket",
            "blob_key",
            "sha256",
            "size",
            "local_key",
        }:
            raise ValueError("Invalid remote artifact backup entry")
        _metadata(item)
        key = (item["store_id"], item["blob_key"])
        if key in actual or key not in expected or item != expected[key]:
            raise ValueError("Remote artifact backup entry conflicts with saved metadata")
        blob = state / "artifact-objects" / "studio" / item["local_key"]
        if (
            not blob.is_file()
            or blob.stat().st_size != item["size"]
            or _sha(blob) != item["sha256"]
        ):
            raise ValueError("Remote artifact backup bytes are missing or corrupt")
        actual[key] = item
    return references


def capture(state, *, store_factory=None):
    """Capture every referenced object once, using existing stores and credentials."""
    from nexus.core.studio_backup import MAX_BYTES, MAX_FILES, _files, _sha

    profiles, objects, _ = plan(state)
    files = list(_files(state.parent))
    additions = [
        item
        for item in objects.values()
        if not (state / "artifact-objects" / "studio" / item["local_key"]).exists()
    ]
    if (
        len(files) + len(additions) > MAX_FILES
        or sum(p.stat().st_size for p in files) + sum(item["size"] for item in additions)
        > MAX_BYTES
    ):
        raise ValueError("Snapshot exceeds the supported file/byte limits")
    reader, stores = _reader(state, store_factory), {}
    for item in objects.values():
        target = state / "artifact-objects" / "studio" / item["local_key"]
        if target.exists():
            if target.stat().st_size != item["size"] or _sha(target) != item["sha256"]:
                raise ValueError("Artifact backup path conflicts with existing bytes")
            continue
        identity = item["store_id"]
        try:
            if identity not in stores:
                stores[identity] = reader.store(profiles[identity])
            store = stores[identity]
            bounded = getattr(store, "get_bounded", None)
            content = (
                bounded(item["bucket"], item["blob_key"], item["size"])
                if bounded
                else store.get(item["bucket"], item["blob_key"])
            )
        except Exception:
            raise ValueError(
                "Remote artifact backup read failed; check its provider, credentials and availability"
            ) from None
        if (
            not isinstance(content, bytes)
            or len(content) != item["size"]
            or hashlib.sha256(content).hexdigest() != item["sha256"]
        ):
            raise ValueError("Remote artifact backup bytes are missing or corrupt")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as stream:
            import os

            os.chmod(target, 0o600)
            stream.write(content)
    archive = {"format": FORMAT, "objects": list(objects.values())}
    verify(state, archive)
    return archive


def localize(state, archive):
    """Rewrite staged references only; source objects and backup records stay intact."""
    if archive is None:
        return 0
    from nexus.core.studio_backup import _json

    references = verify(state, archive)
    objects = {(item["store_id"], item["blob_key"]): item for item in archive["objects"]}
    for path, record, key in references:
        record["data"].update(store_id="local", blob_key=objects[key]["local_key"])
        _json(path, record)
    return len(references)

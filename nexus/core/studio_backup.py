"""Stopped-workspace snapshots and isolated, inactive restore copies.

This is an operator API: stop every Studio/worker and other workspace writer
before calling it. It never starts a Studio service or resolves credentials.
Project source files and Nexus authentication/account stores need their existing
backup procedure. Remote artifact stores currently require a provider backup;
this API refuses to label a snapshot containing remote artifact references complete.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path

FORMAT = "nexus-studio-backup/v1"
EXCLUDED = {"mcp-homes", "speech-inputs", "speech-input-owners"}
MAX_FILES = 100000
MAX_BYTES = 8 * 1024**3


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(value):
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(p in {"", ".", ".."} for p in value.split("/"))
        or Path(value).is_absolute()
    ):
        raise ValueError("Invalid snapshot path")
    return Path(value)


def _files(directory, *, excluded=()):
    for path in sorted(directory.rglob("*")):
        if path.relative_to(directory).parts[0] in excluded:
            continue
        if path.is_symlink():
            raise ValueError("Snapshots cannot contain symbolic links")
        if path.is_file():
            yield path
        elif not path.is_dir():
            raise ValueError("Snapshots accept regular files only")


def _copy_file(source, target, *, sqlite_snapshot=False):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with source.open("rb") as stream:
        sqlite = stream.read(16) == b"SQLite format 3\x00"
    if sqlite and sqlite_snapshot:
        # The backup API includes committed WAL pages; copying only the main
        # SQLite file would lose them even with a stopped Studio process.
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(target) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise ValueError("SQLite snapshot failed its integrity check")
    else:
        shutil.copyfile(source, target)
    os.chmod(target, 0o600)


def _preflight_state(state):
    from nexus.core.studio_schema import SCHEMA_VERSION

    schema = state / "system" / "schema.json"
    if schema.exists():
        version = json.loads(schema.read_text()).get("version")
        if type(version) is not int or not 0 <= version <= SCHEMA_VERSION:
            raise ValueError("Unsupported Studio state version in snapshot")
    for path in state.rglob("*.json"):
        parts = path.relative_to(state).parts
        if parts[0] in EXCLUDED:
            continue
        if parts[0] in {"catalog", "catalog-history", "configuration-journals"}:
            record = json.loads(path.read_text())
            if parts[0] == "configuration-journals" and record.get("status") == "applying":
                raise ValueError("Recover the interrupted configuration release before backup")
            data = record.get("data", {})
            if record.get("kind") == "artifacts" and data.get("store_id", "local") != "local":
                raise ValueError("Remote artifact bytes require a separate provider backup")
            if record.get("kind") == "artifacts":
                if "content_base64" in data:
                    base64.b64decode(data["content_base64"], validate=True)
                else:
                    blob = state / "artifact-objects" / "studio" / _relative(data["blob_key"])
                    if not blob.is_file() or _sha(blob) != data["sha256"]:
                        raise ValueError("Local artifact bytes are missing or corrupt")


def create_snapshot(root, destination, *, state_dir=None, quiesced=False, sql_state=None):
    """Create a verified snapshot, publishing it only after complete preflight.

    ``sql_state`` is an existing StudioSQLState opened without a Studio worker.
    All writers must be stopped, including other processes. SQL records are
    exported from one namespace under its existing transaction lock. Queue rows
    are retained for inspection, never imported as executable jobs on restore.
    """
    if quiesced is not True:
        raise ValueError("Stop all workspace writers and explicitly confirm quiesced=True")
    root = Path(root).resolve(strict=True)
    state = Path(state_dir).resolve(strict=True) if state_dir else root / ".nexus" / "studio"
    if not state.is_dir():
        raise ValueError("Studio state directory is missing")
    marker = state / "storage-backend.json"
    if marker.exists():
        storage = json.loads(marker.read_text())
        expected = {
            "format": "nexus-studio-storage/v1",
            "backend": "sql" if sql_state else "files",
            "namespace": sql_state.namespace if sql_state else None,
        }
        if storage != expected:
            raise ValueError("Backup requires the configured storage backend and SQL namespace")
    destination = Path(destination).absolute()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Snapshot destination must not exist")
    if destination.is_relative_to(root) or destination.is_relative_to(state):
        raise ValueError("Store backups outside the project and private state")
    if (state.is_relative_to(root) and state != root / ".nexus" / "studio") or root.is_relative_to(
        state
    ):
        raise ValueError("Choose default project state or a separate private state directory")
    with tempfile.TemporaryDirectory(prefix=".studio-backup-", dir=parent) as temporary:
        staged = Path(temporary) / "snapshot"
        staged.mkdir(mode=0o700)
        for kind in ("agents", "workflows"):
            directory = root / kind
            if directory.is_symlink():
                raise ValueError("Definition directories cannot be symbolic links")
            if directory.exists():
                for path in _files(directory):
                    _copy_file(path, staged / "project" / kind / path.relative_to(directory))
        if state.is_symlink():
            raise ValueError("State directory cannot be a symbolic link")
        sql_database = (
            Path(sql_state.engine.url.database).resolve()
            if sql_state and sql_state.engine.dialect.name == "sqlite"
            else None
        )
        for path in _files(state, excluded=EXCLUDED):
            relative = path.relative_to(state)
            if relative.parts[0] in EXCLUDED or path.name.endswith(("-wal", "-shm", ".tmp")):
                continue
            if sql_database and path.resolve() == sql_database:
                # Export this namespace, never the physical shared database.
                continue
            _copy_file(
                path,
                staged / "state" / relative,
                sqlite_snapshot=relative.parts[0] != "artifact-objects"
                and path.suffix in {".sqlite", ".sqlite3", ".db"},
            )
        (staged / "state").mkdir(exist_ok=True, mode=0o700)
        jobs = []
        if sql_state:
            with sql_state.lock, sql_state.transaction() as connection:
                rows = connection.execute(
                    sql_state.sa.select(sql_state.table).where(
                        sql_state.table.c.namespace == sql_state.namespace
                    )
                ).mappings()
                for row in rows:
                    relative = _relative(row["collection"] + "/" + row["identity"] + ".json")
                    _json(staged / "state" / relative, row["body"])
                jobs = [
                    dict(row)
                    for row in connection.execute(
                        sql_state.sa.select(sql_state.jobs).where(
                            sql_state.jobs.c.namespace == sql_state.namespace
                        )
                    ).mappings()
                ]
            _json(staged / "sql-jobs.json", jobs)
        _preflight_state(staged / "state")
        entries = [
            {"path": p.relative_to(staged).as_posix(), "size": p.stat().st_size, "sha256": _sha(p)}
            for p in _files(staged)
        ]
        if len(entries) > MAX_FILES or sum(e["size"] for e in entries) > MAX_BYTES:
            raise ValueError("Snapshot exceeds the supported file/byte limits")
        manifest = {
            "format": FORMAT,
            "created_at": datetime.now(UTC).isoformat(),
            "backend": "sql-logical" if sql_state else "files",
            "namespace": sql_state.namespace if sql_state else None,
            "sql_jobs_retained": len(jobs),
            "excluded": sorted(EXCLUDED),
            "scope": ["definitions", "studio-state", "local-artifacts", "sqlite-indexes"],
            "files": entries,
        }
        _json(staged / "manifest.json", manifest)
        verify_snapshot(staged)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Snapshot destination must not exist")
        staged.rename(destination)
    return {"path": str(destination), "files": len(entries), "backend": manifest["backend"]}


def verify_snapshot(source):
    """Validate all paths and bytes before restore writes its destination."""
    source = Path(source).resolve(strict=True)
    paths = list(_files(source))
    manifest_path = source / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > 32 * 1024**2:
        raise ValueError("Snapshot manifest is missing or too large")
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ValueError("Unsupported snapshot format")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) > MAX_FILES:
        raise ValueError("Invalid snapshot inventory")
    expected, total = {"manifest.json"}, 0
    for item in entries:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise ValueError("Invalid snapshot entry")
        relative = _relative(item["path"])
        if relative.parts[0] not in {"project", "state", "sql-jobs.json"}:
            raise ValueError("Invalid snapshot scope")
        if item["path"] in expected:
            raise ValueError("Duplicate snapshot path")
        expected.add(item["path"])
        if type(item["size"]) is not int or item["size"] < 0:
            raise ValueError("Invalid snapshot size")
        total += item["size"]
        if (
            total > MAX_BYTES
            or not isinstance(item["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise ValueError("Invalid snapshot size or checksum")
        path = source / relative
        if (
            not path.is_file()
            or path.stat().st_size != item["size"]
            or _sha(path) != item["sha256"]
        ):
            raise ValueError("Snapshot integrity verification failed")
    if {p.relative_to(source).as_posix() for p in paths} != expected:
        raise ValueError("Snapshot contains unlisted files")
    _preflight_state(source / "state")
    return manifest


def _inactivate(state):
    changes = []
    for path in state.rglob("*.json"):
        relative = path.relative_to(state)
        record = json.loads(path.read_text())
        if not isinstance(record, dict):
            continue
        before = json.dumps(record, sort_keys=True)
        if relative.parts[0] == "catalog":
            data = record.get("data", {})
            for key in ("enabled", "a2a_enabled", "mcp_enabled"):
                if key in data:
                    data[key] = False
        if relative.parts[0] == "runs" and record.get("status") in {"queued", "running"}:
            record.update(
                status="interrupted", error="Restored copy: review external effects before resuming"
            )
        if relative.parts[0] == "external-runs" and record.get("status") in {
            "sending",
            "submitted",
            "working",
            "continuation-queued",
        }:
            record["status"] = "uncertain"
        if relative.parts[0] == "conversation-shares":
            record["revoked"] = True
        if relative.as_posix() == "system/public-sharing.json":
            record["enabled"] = False
        if relative.as_posix() == "artifact-settings/active.json":
            record["profile_id"] = "local"
        if before != json.dumps(record, sort_keys=True):
            _json(path, record)
            changes.append(relative.as_posix())
    return changes


def restore_snapshot(source, destination):
    """Restore to a new directory, with schedules/connections/public shares inactive.

    SQL snapshots restore as private file state; original queue rows remain in
    the backup only. This never connects to an auth store, cloud or SQL server.
    Review the report and supply the original team policy before hosted use.
    """
    source = Path(source).resolve(strict=True)
    manifest = verify_snapshot(source)
    destination = Path(destination).absolute()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Restore destination must not exist")
    if destination.is_relative_to(source):
        raise ValueError("Restore outside the snapshot")
    with tempfile.TemporaryDirectory(prefix=".studio-restore-", dir=parent) as temporary:
        staged = Path(temporary) / "restore"
        staged.mkdir(mode=0o700)
        for item in manifest["files"]:
            relative = _relative(item["path"])
            if relative.parts[0] in {"state", "project"}:
                _copy_file(source / relative, staged / relative)
        for directory in ("project", "state"):
            (staged / directory).mkdir(mode=0o700, exist_ok=True)
        changes = _inactivate(staged / "state")
        _json(
            staged / "state" / "storage-backend.json",
            {
                "format": "nexus-studio-storage/v1",
                "backend": "files",
                "namespace": None,
            },
        )
        report = {
            "format": "nexus-studio-restore/v1",
            "source_backend": manifest.get("backend"),
            "restored_backend": "files",
            "inactive_records": changes,
            "sql_jobs_not_enqueued": manifest.get("sql_jobs_retained", 0),
            "review_required": True,
        }
        _json(staged / "restore-report.json", report)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("Restore destination must not exist")
        staged.rename(destination)
    return {**report, "root": str(destination / "project"), "state_dir": str(destination / "state")}


def main():
    """Operator CLI, deliberately separate from the running HTTP service."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    create = commands.add_parser("create")
    create.add_argument("root")
    create.add_argument("destination")
    create.add_argument("--state-dir")
    create.add_argument("--stopped", action="store_true", help="All workspace writers are stopped")
    create.add_argument("--state-url-env", help="SQL connection environment reference")
    create.add_argument("--namespace")
    verify = commands.add_parser("verify")
    verify.add_argument("source")
    restore = commands.add_parser("restore")
    restore.add_argument("source")
    restore.add_argument("destination")
    args = parser.parse_args()
    store = None
    try:
        if args.operation == "create":
            if args.state_url_env:
                from nexus.core.studio_connections import env_name
                from nexus.core.studio_state import StudioSQLState

                url = os.getenv(env_name(args.state_url_env))
                if not url:
                    raise ValueError("SQL connection environment reference is not configured")
                store = StudioSQLState(url, args.namespace)
            elif args.namespace:
                raise ValueError("SQL namespace requires --state-url-env")
            result = create_snapshot(
                args.root,
                args.destination,
                state_dir=args.state_dir,
                quiesced=args.stopped,
                sql_state=store,
            )
        elif args.operation == "restore":
            result = restore_snapshot(args.source, args.destination)
        else:
            manifest = verify_snapshot(args.source)
            result = {"verified": True, "files": len(manifest["files"])}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    finally:
        if store:
            store.engine.dispose()


if __name__ == "__main__":
    main()

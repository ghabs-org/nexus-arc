"""Stopped, empty-namespace SQL restore with a recoverable filesystem boundary.

No Studio service or worker is started. A workspace guard remains until files,
one namespace transaction and its durable receipt have all been verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from nexus.core import studio_backup as backup

PENDING = "restore-pending.json"
PAYLOAD = "restore-records.json"
RECEIPTS = "restore-receipts"
CONTROL_FILES = {"restore-report.json", PAYLOAD, "state/" + PENDING}


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def _atomic_json(path, value, restore_id):
    descriptor, temporary = tempfile.mkstemp(
        prefix=".sql-restore-write-" + restore_id + "-", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    temporary = Path(temporary)
    try:
        backup._json(temporary, value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _temporary(name):
    return re.fullmatch(r"\.sql-restore-write-[0-9a-f]{32}-[A-Za-z0-9_-]+\.tmp", name)


def _inventory(directory):
    files = []
    total = 0
    for path in backup._files(directory):
        name = path.relative_to(directory).as_posix()
        if name in CONTROL_FILES or path.parent == directory and _temporary(path.name):
            continue
        if path.relative_to(directory).parts[0] not in {"project", "state"}:
            raise ValueError("Unexpected file in SQL restore workspace")
        size = path.stat().st_size
        total += size
        if len(files) >= backup.MAX_FILES or total > backup.MAX_BYTES:
            raise ValueError("SQL restore file inventory exceeds its limit")
        files.append({"path": name, "size": size, "sha256": backup._sha(path)})
    return files


def _rows(store, *, exclude=None):
    with store.transaction() as connection:
        rows = connection.execute(
            store.sa.select(store.table).where(store.table.c.namespace == store.namespace)
        ).mappings()
        values = [
            {"collection": row["collection"], "identity": row["identity"], "body": row["body"]}
            for row in rows
            if (row["collection"], row["identity"]) != exclude
        ]
    return sorted(values, key=lambda row: (row["collection"], row["identity"]))


def _no_jobs(store):
    with store.transaction() as connection:
        if connection.execute(
            store.sa.select(store.jobs.c.run_id)
            .where(store.jobs.c.namespace == store.namespace)
            .limit(1)
        ).first():
            raise ValueError("SQL restore requires a namespace with no job rows")


def _empty(store):
    _no_jobs(store)
    with store.transaction() as connection:
        if connection.execute(
            store.sa.select(store.table.c.identity)
            .where(store.table.c.namespace == store.namespace)
            .limit(1)
        ).first():
            raise ValueError("SQL restore requires an empty destination namespace")


def _stopped(store, quiesced):
    if quiesced is not True:
        raise ValueError("Stop all source and target writers and explicitly confirm quiesced=True")
    database = store.engine.url.database
    if store.engine.dialect.name == "sqlite" and (
        database in {None, "", ":memory:"}
        or str(database).startswith("file:")
        and (":memory:" in database or store.engine.url.query.get("mode") == "memory")
    ):
        raise ValueError("SQL restore requires persistent SQLite state, not an in-memory database")


def _receipt(binding):
    return {"format": "nexus-studio-sql-restore-receipt/v1", "binding": binding}


def _binding(value, destination, store):
    if not isinstance(value, dict) or set(value) != {
        "restore_id",
        "destination",
        "namespace",
        "snapshot_sha256",
        "records_sha256",
        "files_sha256",
        "records_count",
    }:
        raise ValueError("Invalid SQL restore binding")
    if (
        not isinstance(value["restore_id"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", value["restore_id"])
        or value["destination"] != str(destination)
        or value["namespace"] != store.namespace
        or type(value["records_count"]) is not int
        or not 0 <= value["records_count"] <= backup.MAX_FILES
        or any(
            not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key])
            for key in ("snapshot_sha256", "records_sha256", "files_sha256")
        )
    ):
        raise ValueError(
            "SQL restore belongs to a different destination or namespace, or is malformed"
        )
    return value


def _committed(store, binding):
    receipt = store.get(RECEIPTS, binding["restore_id"], missing=True)
    if receipt != _receipt(binding):
        raise ValueError("SQL restore receipt is missing or conflicts with the prepared workspace")
    _no_jobs(store)
    rows = _rows(store, exclude=(RECEIPTS, binding["restore_id"]))
    if len(rows) != binding["records_count"] or _digest(rows) != binding["records_sha256"]:
        raise ValueError("SQL restore records changed; review the target before recovery")


def _finish(destination, intent):
    binding = intent["binding"]
    report = {**intent["report"], "status": "ready-inactive"}
    _atomic_json(destination / "restore-report.json", report, binding["restore_id"])
    (destination / PAYLOAD).unlink(missing_ok=True)
    for path in destination.glob(".sql-restore-write-" + binding["restore_id"] + "-*.tmp"):
        path.unlink()
    # Keep the guard through every preceding write and cleanup operation.
    (destination / "state" / PENDING).unlink()
    return report


def recover_sql_restore(destination, sql_state, *, quiesced=False):
    """Finish this prepared import, or verify its committed receipt without replay."""
    _stopped(sql_state, quiesced)
    destination = Path(destination).resolve(strict=True)
    marker = destination / "state" / PENDING
    # Reject symlinks even on the already-finished path.
    list(backup._files(destination))
    if not marker.exists():
        report = json.loads((destination / "restore-report.json").read_text())
        binding = _binding(report.get("restore_binding"), destination, sql_state)
        if report.get("status") != "ready-inactive" or report.get("restored_backend") != "sql":
            raise ValueError("This directory is not a completed SQL restore")
        with sql_state.lock:
            if sql_state.get(RECEIPTS, binding["restore_id"], missing=True) != _receipt(binding):
                raise ValueError("Completed SQL restore receipt is unavailable")
        return {**report, "already_complete": True}
    intent = json.loads(marker.read_text())
    if (
        not isinstance(intent, dict)
        or set(intent) != {"format", "binding", "files", "report"}
        or intent["format"] != "nexus-studio-sql-restore-intent/v1"
        or not isinstance(intent["report"], dict)
        or not isinstance(intent["files"], list)
    ):
        raise ValueError("Invalid pending SQL restore intent")
    binding = _binding(intent["binding"], destination, sql_state)
    if (
        _digest(intent["files"]) != binding["files_sha256"]
        or _inventory(destination) != intent["files"]
    ):
        raise ValueError("SQL restore files changed or are incomplete")
    with sql_state.lock:
        receipt = sql_state.get(RECEIPTS, binding["restore_id"], missing=True)
        if receipt is None:
            _empty(sql_state)
            path = destination / PAYLOAD
            if not path.is_file() or path.stat().st_size > backup.MAX_BYTES:
                raise ValueError("SQL restore import payload is missing or too large")
            rows = json.loads(path.read_text())
            if (
                not isinstance(rows, list)
                or len(rows) != binding["records_count"]
                or _digest(rows) != binding["records_sha256"]
            ):
                raise ValueError("SQL restore import payload is invalid")
            for row in rows:
                sql_state.put(row["collection"], row["identity"], row["body"])
            sql_state.put(RECEIPTS, binding["restore_id"], _receipt(binding))
        else:
            _committed(sql_state, binding)
    # SQL has committed. A crash here leaves the guard and a reusable receipt.
    with sql_state.lock:
        _committed(sql_state, binding)
        if _inventory(destination) != intent["files"]:
            raise ValueError("SQL restore files changed before completion")
        return _finish(destination, intent)


def restore_sql_snapshot(source, destination, sql_state, *, quiesced=False):
    """Prepare a new inactive workspace and import one empty SQL namespace."""
    _stopped(sql_state, quiesced)
    source = Path(source).resolve(strict=True)
    manifest = backup.verify_snapshot(source)
    record_paths = backup.sql_record_paths(manifest)
    destination = Path(destination).absolute()
    destination = destination.parent.resolve(strict=True) / destination.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("SQL restore destination must not exist")
    if destination.is_relative_to(source):
        raise ValueError("Restore outside the snapshot")
    with sql_state.lock:
        _empty(sql_state)
    with tempfile.TemporaryDirectory(
        prefix=".studio-sql-restore-", dir=destination.parent
    ) as temporary:
        staged = Path(temporary) / "workspace"
        report = backup.restore_snapshot(source, staged)
        rows = []
        for name in record_paths:
            path = staged / name
            parts = backup._relative(name).parts
            rows.append(
                {
                    "collection": "/".join(parts[1:-1]),
                    "identity": path.stem,
                    "body": json.loads(path.read_text()),
                }
            )
            # SQL is authoritative. Leaving stale physical JSON records could
            # resurrect deleted records during a future file-state restore.
            path.unlink()
        rows.sort(key=lambda row: (row["collection"], row["identity"]))
        restore_id = uuid.uuid4().hex
        backup._json(staged / PAYLOAD, rows)
        backup._json(
            staged / "state" / "storage-backend.json",
            {
                "format": "nexus-studio-storage/v1",
                "backend": "sql",
                "namespace": sql_state.namespace,
            },
        )
        files = _inventory(staged)
        binding = {
            "restore_id": restore_id,
            "destination": str(destination),
            "namespace": sql_state.namespace,
            "snapshot_sha256": backup._sha(source / "manifest.json"),
            "records_sha256": _digest(rows),
            "files_sha256": _digest(files),
            "records_count": len(rows),
        }
        report.update(
            restored_backend="sql",
            target_namespace=sql_state.namespace,
            sql_records_imported=len(rows),
            restore_binding=binding,
            status="pending-sql",
            root=str(destination / "project"),
            state_dir=str(destination / "state"),
        )
        intent = {
            "format": "nexus-studio-sql-restore-intent/v1",
            "binding": binding,
            "files": files,
            "report": report,
        }
        backup._json(staged / "state" / PENDING, intent)
        backup._json(staged / "restore-report.json", report)
        with sql_state.lock:
            _empty(sql_state)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError("SQL restore destination must not exist")
            staged.rename(destination)
    try:
        return recover_sql_restore(destination, sql_state, quiesced=True)
    except Exception:
        raise ValueError(
            "SQL restore remains blocked; keep the workspace and run stopped-workspace recover-sql"
        ) from None

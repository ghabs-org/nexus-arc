"""Recoverable metadata-only upgrades; legacy user records stay byte-for-byte intact.

Version 1 adds metadata only. Any future migration that rewrites user records
must first require a verified, consistent backup and complete preflight; this
journal is not a replacement for that backup.
"""

from datetime import UTC, datetime

SCHEMA_VERSION = 1


class NewerStateError(ValueError):
    """The workspace was written by a newer Studio release; nothing was changed."""

    exit_code = 65  # EX_DATAERR; the native shell maps it to a specific screen.


def _version(record):
    version = record.get("version") if isinstance(record, dict) else None
    if type(version) is int and version > SCHEMA_VERSION:
        raise NewerStateError("Unsupported Studio state version; use a compatible Studio release")
    if type(version) is not int or not 0 <= version <= SCHEMA_VERSION:
        raise ValueError("Unsupported Studio state version; use a compatible Studio release")
    return version


def _migration_record(record):
    if (
        _version(record) != 1
        or type(record.get("previous")) is not int
        or record["previous"] != 0
        or not isinstance(record.get("updated_at"), str)
        or not record["updated_at"]
        or record.get("changes") != ["additive records"]
    ):
        raise ValueError("Invalid Studio schema upgrade metadata; restore verified metadata")
    return record


def upgrade(studio):
    with studio.lock:
        path = studio._path("system", "schema")
        previous = studio._read(path) if path.exists() else {"version": 0}
        version = _version(previous)
        history_path = studio._path("schema-history", str(SCHEMA_VERSION))
        journal_path = studio._path("schema-upgrades", str(SCHEMA_VERSION))
        history = _migration_record(studio._read(history_path)) if history_path.exists() else None
        journal_exists = journal_path.exists()
        journal = studio._read(journal_path) if journal_exists else None
        if journal_exists:
            if not isinstance(journal, dict) or journal.get("status") not in {
                "prepared",
                "committed",
            }:
                raise ValueError("Invalid Studio schema upgrade journal; restore verified metadata")
            record = _migration_record(journal.get("record"))
            if (history is not None and history != record) or (
                version == SCHEMA_VERSION and previous != record
            ):
                raise ValueError(
                    "Conflicting Studio schema upgrade metadata; restore verified metadata"
                )
        elif version == SCHEMA_VERSION:
            if history is not None and history != previous:
                raise ValueError(
                    "Conflicting Studio schema upgrade metadata; restore verified metadata"
                )
            # Older releases wrote no journal. Their completed metadata remains valid.
            return previous
        else:
            # Recover the old release's history-written / marker-not-written gap.
            record = history or {
                "version": SCHEMA_VERSION,
                "previous": version,
                "updated_at": datetime.now(UTC).isoformat(),
                "changes": ["additive records"],
            }
        # Validate every existing record before any mutation. Each write is atomic;
        # the immutable intent lets a later startup finish the same upgrade after
        # interruption at any boundary. SQL state wraps all writes in its transaction.
        if journal is None:
            journal = {"status": "prepared", "record": record}
            studio._write(journal_path, journal)
        if history is None:
            studio._write(history_path, record)
        if version != SCHEMA_VERSION:
            studio._write(path, record)
        if journal["status"] != "committed":
            studio._write(journal_path, {"status": "committed", "record": record})
        return record

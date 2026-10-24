"""Use framework recovery APIs in a disposable workspace, without accounts.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_recovery.py
Use --sql for empty-namespace SQL restore and receipt recovery using local SQLite.
"""

import argparse
import tempfile
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_backup import create_snapshot, restore_snapshot, verify_snapshot


def run_example():
    with tempfile.TemporaryDirectory(prefix="nexus-recovery-example-") as temporary:
        directory = Path(temporary)
        root = directory / "project"
        studio = StudioService(root)
        try:
            studio.save_definition(
                "agents", {"name": "Reviewer", "instructions": "Review carefully"}
            )
            studio.catalog.save(
                "prompts", {"name": "Review", "data": {"content": "Review {{Topic}}"}}
            )
            assert studio.schema["version"] == 1
        finally:
            studio.close()
        snapshot = directory / "backup"
        create_snapshot(root, snapshot, quiesced=True)
        assert verify_snapshot(snapshot)["files"]
        restored = restore_snapshot(snapshot, directory / "restored")
        copy = StudioService(restored["root"], state_dir=restored["state_dir"])
        try:
            assert copy.definition("agents", "reviewer")["spec"]["purpose"] == "Review carefully"
            assert copy.catalog.get("prompts", "review")["data"]["content"] == "Review {{Topic}}"
        finally:
            copy.close()
        print(
            "Verified upgrade metadata, private snapshot and isolated restore; no jobs dispatched."
        )


def run_sql_example():
    from nexus.core.studio_sql_restore import recover_sql_restore
    from nexus.core.studio_state import StudioSQLState

    with tempfile.TemporaryDirectory(prefix="nexus-sql-recovery-example-") as temporary:
        directory = Path(temporary)
        root, state = directory / "project", directory / "private"
        url = f"sqlite:///{directory}/records.sqlite"
        studio = StudioService(
            root, state_dir=state, state_options={"url": url, "namespace": "original", "workers": 1}
        )
        try:
            studio.save_definition(
                "agents", {"name": "Reviewer", "instructions": "Review carefully"}
            )
            studio.catalog.save(
                "prompts", {"name": "Review", "data": {"content": "Review {{Topic}}"}}
            )
        finally:
            studio.close()
        original, target = StudioSQLState(url, "original"), StudioSQLState(url, "restored")
        try:
            # Represent work that was queued when the original host stopped.
            original.put("runs", "pending", {"id": "pending", "status": "queued"})
            original.enqueue("pending", {"task": "Do not replay"}, "conversation")
            snapshot = directory / "backup"
            create_snapshot(root, snapshot, state_dir=state, sql_state=original, quiesced=True)
            restored = restore_snapshot(
                snapshot, directory / "restored", sql_state=target, quiesced=True
            )
            assert restored["restored_backend"] == "sql" and restored["status"] == "ready-inactive"
            assert target.get("runs", "pending")["status"] == "interrupted"
            assert original.get("runs", "pending")["status"] == "queued"
            assert target.job_request("pending") is None
            assert recover_sql_restore(directory / "restored", target, quiesced=True)[
                "already_complete"
            ]
            copy = StudioService(
                restored["root"],
                state_dir=restored["state_dir"],
                state_options={"url": url, "namespace": "restored", "workers": 1},
            )
            try:
                assert (
                    copy.definition("agents", "reviewer")["spec"]["purpose"] == "Review carefully"
                )
                assert (
                    copy.catalog.get("prompts", "review")["data"]["content"] == "Review {{Topic}}"
                )
                assert copy.run("pending")["status"] == "interrupted"
                assert copy.sql_state.job_request("pending") is None
            finally:
                copy.close()
        finally:
            original.engine.dispose()
            target.engine.dispose()
        print(
            "Verified SQL namespace restore, retained definitions, inactive work and idempotent receipt recovery; no jobs dispatched."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", action="store_true")
    args = parser.parse_args()
    run_sql_example() if args.sql else run_example()

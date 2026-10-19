"""Use framework recovery APIs in a disposable workspace, without accounts.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_recovery.py
"""

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


if __name__ == "__main__":
    run_example()

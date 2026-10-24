"""Demonstrate object-backed revisions restored offline, using local fixture bytes.

Run from the repository root with PYTHONPATH=.; no cloud account is contacted.
The same backup API uses configured S3/GCS/Azure profiles without this fixture.
"""

import base64
import tempfile
from pathlib import Path

from nexus.adapters.artifacts import FilesystemStore
from nexus.core.studio import StudioService
from nexus.core.studio_backup import create_snapshot, restore_snapshot


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-object-recovery-") as temporary:
        directory = Path(temporary)
        fixture = FilesystemStore(directory / "provider-fixture")
        studio = StudioService(
            directory / "project", artifact_options={"store_factory": lambda _: fixture}
        )
        try:
            profile = studio.artifacts.save_profile(
                {"name": "Fixture object storage", "kind": "s3", "bucket": "evidence"}
            )
            studio.artifacts.test(profile["id"])
            studio.artifacts.activate(profile["id"])
            first = studio.catalog.save(
                "artifacts",
                {
                    "name": "Policy",
                    "data": {
                        "filename": "policy.txt",
                        "content_base64": base64.b64encode(b"Original evidence").decode(),
                    },
                },
            )
            studio.catalog.save(
                "artifacts",
                {
                    "name": "Policy",
                    "id": first["id"],
                    "revision": 1,
                    "data": {
                        "filename": "policy.txt",
                        "content_base64": base64.b64encode(b"New evidence").decode(),
                    },
                },
            )
        finally:
            studio.close()
        snapshot = directory / "backup"
        result = create_snapshot(
            studio.root,
            snapshot,
            quiesced=True,
            include_remote_artifacts=True,
            artifact_store_factory=lambda _: fixture,
        )
        assert result["remote_artifact_objects"] == 2
        for key in fixture.list("evidence"):
            fixture.delete("evidence", key)
        restored = restore_snapshot(snapshot, directory / "restored")
        copy = StudioService(restored["root"], state_dir=restored["state_dir"])
        try:
            assert copy.artifacts.profile()["id"] == "local"
            assert (
                copy.artifacts.get(copy.artifacts.revision(first["id"], 1)) == b"Original evidence"
            )
            assert copy.artifacts.get(copy.artifacts.revision(first["id"])) == b"New evidence"
        finally:
            copy.close()
        print(
            "Two object-backed revisions restored offline; source records retained and remote activation requires review."
        )


if __name__ == "__main__":
    main()

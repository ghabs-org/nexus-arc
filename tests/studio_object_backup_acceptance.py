"""Actual boto3-backed backups against the local Postman object simulation."""

import json
import sys
import tempfile
from pathlib import Path

from nexus.adapters.artifacts import FilesystemStore, S3Store
from nexus.core.studio import StudioService
from nexus.core.studio_backup import create_snapshot, restore_snapshot
from tests.test_studio_backup_artifacts import save


def main(endpoint):
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("Use the local synthetic simulation endpoint only")
    with tempfile.TemporaryDirectory(prefix="nexus-object-backup-http-") as temporary:
        directory = Path(temporary)
        local = FilesystemStore(directory / "provider-fixture")
        app = StudioService(
            directory / "project", artifact_options={"store_factory": lambda _: local}
        )
        profile = app.artifacts.save_profile(
            {"name": "Synthetic S3", "kind": "s3", "bucket": "evidence"}
        )
        app.artifacts.test(profile["id"])
        app.artifacts.activate(profile["id"])
        first = save(app, b"Original policy refunds")
        save(app, b"Updated policy", id=first["id"], revision=1)
        state, root = app.state, app.root
        app.close()

        def provider(_):
            return S3Store(
                endpoint_url=endpoint,
                region="us-east-1",
                access_key="fixture-access",
                secret_key="fixture-secret",
            )

        snapshot = directory / "snapshot"
        result = create_snapshot(
            root,
            snapshot,
            quiesced=True,
            include_remote_artifacts=True,
            artifact_store_factory=provider,
        )
        assert result["remote_artifact_objects"] == 2
        restored = restore_snapshot(snapshot, directory / "restored")
        copy = StudioService(restored["root"], state_dir=restored["state_dir"])
        try:
            assert (
                copy.artifacts.get(copy.artifacts.revision(first["id"], 1))
                == b"Original policy refunds"
            )
            assert copy.artifacts.get(copy.artifacts.revision(first["id"])) == b"Updated policy"
        finally:
            copy.close()
        current = state / "catalog/artifacts" / (first["id"] + ".json")
        original = json.loads(current.read_text())
        for failure in ("missing", "denied", "outage", "corrupt", "oversize"):
            changed = json.loads(json.dumps(original))
            changed["data"]["blob_key"] = failure
            current.write_text(json.dumps(changed))
            destination = directory / failure
            try:
                create_snapshot(
                    root,
                    destination,
                    quiesced=True,
                    include_remote_artifacts=True,
                    artifact_store_factory=provider,
                )
            except ValueError as exc:
                assert "fixture-secret" not in str(exc)
            else:
                raise AssertionError("Invalid source object unexpectedly produced a snapshot")
            assert not destination.exists()
        current.write_text(json.dumps(original))
        print(
            json.dumps(
                {
                    "s3_sdk_success": True,
                    "revisions_restored_offline": 2,
                    "failure_shapes": 5,
                    "real_accounts_used": False,
                }
            )
        )


if __name__ == "__main__":
    main(sys.argv[1])

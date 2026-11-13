"""Actual GCS/Azure SDKs against local Postman HTTP contracts, never accounts.

Start postman/simulations/studio-cloud-backup.sim.yaml, then explicitly run this
gate with NEXUS_ACCEPTANCE_CLOUD_BACKUP_URL set to its loopback base URL.
Missing SDK/endpoint prerequisites fail this separate gate instead of skipping.
"""

import base64
import hashlib
import json
import os
from urllib.parse import urlparse
from urllib.request import ProxyHandler, build_opener

import pytest

from nexus.adapters.artifacts import AzureStore, GCSStore
from nexus.core.studio import StudioService
from nexus.core.studio_backup import create_snapshot, restore_snapshot, verify_snapshot
from tests.test_studio_backup_artifacts import setup_workspace


@pytest.mark.parametrize("kind", ["gcs", "azure"])
def test_real_sdk_backup_and_offline_restore(tmp_path, monkeypatch, kind, request):
    endpoint = os.environ["NEXUS_ACCEPTANCE_CLOUD_BACKUP_URL"]
    parsed = urlparse(endpoint)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    assert parsed.port and not parsed.username and not parsed.password
    assert parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
    endpoint = endpoint.rstrip("/") + "/" + kind
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    if kind == "gcs":
        monkeypatch.setenv("STORAGE_EMULATOR_HOST", endpoint)
        store = GCSStore(project="synthetic-backup")
    else:
        secret = base64.b64encode(b"synthetic-local-fixture-key-only").decode()
        store = AzureStore(
            connection_string=(
                "DefaultEndpointsProtocol=http;AccountName=fixtureaccount;AccountKey="
                + secret
                + ";BlobEndpoint="
                + endpoint
                + "/fixtureaccount;"
            )
        )
    root, state, _, _, _, _, artifact, project, _ = setup_workspace(tmp_path, kind)
    opener = build_opener(ProxyHandler({}))

    def calls():
        with opener.open(endpoint + "/_fixture/calls", timeout=5) as response:
            return json.load(response)

    start = len(calls())
    before = {str(p.relative_to(state)): p.read_bytes() for p in state.rglob("*.json")}
    snapshot = tmp_path / "snapshot"
    result = create_snapshot(
        root,
        snapshot,
        quiesced=True,
        include_remote_artifacts=True,
        artifact_store_factory=lambda _: store,
    )
    assert result["remote_artifact_objects"] == 2
    manifest = verify_snapshot(snapshot)
    assert len(manifest["remote_artifacts"]["objects"]) == 2
    for path in snapshot.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & 0o077 == 0
    after_backup = len(calls())
    restored = restore_snapshot(snapshot, tmp_path / "restored")
    offline = StudioService(restored["root"], state_dir=restored["state_dir"])
    try:
        assert offline.artifacts.profile()["id"] == "local"
        assert (
            offline.artifacts.get(offline.artifacts.revision(artifact["id"], 1))
            == b"Original policy refunds"
        )
        assert (
            offline.artifacts.get(offline.artifacts.revision(artifact["id"])) == b"Updated policy"
        )
        assert (
            offline.knowledge.search(project["id"], "refunds", None)["items"][0][
                "artifact_revision"
            ]
            == 1
        )
    finally:
        offline.close()
    assert len(calls()) == after_backup, "Offline restore connected to the provider"
    assert {str(p.relative_to(state)): p.read_bytes() for p in state.rglob("*.json")} == before

    # Metadata sizes reject oversize and avoid all media reads for empty objects.
    assert store.get_bounded("evidence", "empty", 0) == b""
    with pytest.raises(ValueError, match="read limit"):
        store.get_bounded("evidence", "oversize", 14)
    with pytest.raises(ValueError):
        store.get_bounded("evidence", "overrun", 14)

    current = state / "catalog/artifacts" / (artifact["id"] + ".json")
    original = current.read_bytes()
    failures = ["missing", "denied", "corrupt", "oversize"]
    if kind == "gcs":
        failures.extend(["changed", "outage"])
    try:
        for failure in failures:
            changed = json.loads(original)
            changed["data"]["blob_key"] = failure
            current.write_text(json.dumps(changed))
            destination = tmp_path / failure
            with pytest.raises(ValueError) as error:
                create_snapshot(
                    root,
                    destination,
                    quiesced=True,
                    include_remote_artifacts=True,
                    artifact_store_factory=lambda _: store,
                )
            assert "synthetic-local-fixture-key-only" not in str(error.value)
            assert "fixtureaccount:" not in str(error.value)
            assert not destination.exists()
    finally:
        current.write_bytes(original)
        if kind == "gcs":
            store._client.close()
        else:
            store._service.close()
    assert {str(p.relative_to(state)): p.read_bytes() for p in state.rglob("*.json")} == before
    observed = calls()[start:]
    assert observed and all(c["method"] in {"GET", "HEAD"} for c in observed)
    assert not any(c["path"].endswith(("/empty", "/oversize")) and c["range"] for c in observed)
    downloads = [c for c in observed if c["range"]]
    assert downloads and all(c["range"].startswith("bytes=0-") for c in downloads)
    if kind == "gcs":
        assert all(c["generation"] == "7" for c in downloads)
        assert not any(c["authenticated"] for c in observed)
    else:
        assert all(c["authenticated"] for c in observed)
    request.node.user_properties.append(
        (
            "result",
            json.dumps(
                {
                    "kind": kind,
                    "sdk_http_requests": len(observed),
                    "failure_shapes": failures,
                    "offline_revisions": 2,
                    "retained_citation_revision": 1,
                    "source_records_sha256": hashlib.sha256(
                        json.dumps({k: v.hex() for k, v in before.items()}, sort_keys=True).encode()
                    ).hexdigest(),
                    "provider_mutations": 0,
                    "real_accounts": False,
                },
                sort_keys=True,
            ),
        )
    )

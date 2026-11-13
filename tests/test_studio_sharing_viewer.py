"""HTTP expiry and disable/re-enable checks complement full-app viewer acceptance."""

import base64
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_studio_team import team as team


@pytest.mark.parametrize("public", [False, True])
def test_expiry_denies_both_snapshot_and_selected_download(team, public):
    studio, _, _, request, _ = team
    studio._write(
        studio._path("sessions", "sharing-expiry"),
        {"id": "sharing-expiry", "owner_id": "alice", "title": "Expiry", "messages": []},
    )
    status, artifact = request(
        "/api/catalog/artifacts",
        {
            "name": "Selected",
            "data": {
                "filename": "selected.txt",
                "content_base64": base64.b64encode(b"Pinned evidence").decode(),
            },
        },
        user="alice",
    )
    assert status == 201
    if public:
        assert request("/api/studio/sharing", {"enabled": True}, user="owner")[0] == 200
    status, snapshot = request(
        "/api/studio/shares",
        {
            "session_id": "sharing-expiry",
            "public": public,
            "recipients": ["bob"],
            "artifact_ids": [artifact["id"]],
        },
        user="alice",
    )
    assert status == 201
    endpoint = (
        "/api/public-shares/" + snapshot["token"]
        if public
        else "/api/studio/shares/" + snapshot["id"]
    )
    reader = None if public else "bob"
    assert request(endpoint, user=reader)[0] == 200
    assert request(endpoint + "/artifacts/" + artifact["id"], user=reader)[0] == 200
    with studio.lock:
        path = studio._path("conversation-shares", snapshot["id"])
        stored = studio._read(path)
        stored["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        studio._write(path, stored)
    assert request(endpoint, user=reader)[0] == 404
    assert request(endpoint + "/artifacts/" + artifact["id"], user=reader)[0] == 404


def test_disabling_public_sharing_permanently_revokes_existing_tokens(team):
    studio, _, _, request, _ = team
    studio._write(
        studio._path("sessions", "public-switch"),
        {"id": "public-switch", "owner_id": "alice", "title": "Public switch", "messages": []},
    )
    assert request("/api/studio/sharing", {"enabled": True}, user="alice")[0] == 403
    assert request("/api/studio/sharing", {"enabled": True}, user="owner")[0] == 200
    status, snapshot = request(
        "/api/studio/shares", {"session_id": "public-switch", "public": True}, user="alice"
    )
    assert status == 201
    endpoint = "/api/public-shares/" + snapshot["token"]
    assert request(endpoint)[0] == 200
    assert request("/api/studio/sharing", {"enabled": False}, user="owner")[0] == 200
    assert request(endpoint)[0] == 404
    assert request("/api/studio/sharing", {"enabled": True}, user="owner")[0] == 200
    assert request(endpoint)[0] == 404

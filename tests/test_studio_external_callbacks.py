"""Real sender HTTP deliveries exercise private callback receipt and read-only recovery."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from nexus.core.studio_external_callbacks import COLLECTION, origin
from tests.test_studio_a2a_http import gateway as gateway
from tests.test_studio_expansion_controls import done
from tests.test_studio_external_content import connection
from tests.test_studio_external_content import peer as peer
from tests.test_studio_team import team as team


def post(url, body, authorization, delivery="receipt-1", **headers):
    req = urllib.request.Request(
        url,
        json.dumps(body).encode(),
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json",
            "X-Nexus-Delivery-ID": delivery,
            **headers,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def event(task="remote-task", context="remote-context"):
    # A2A StreamResponse; body deliberately differs from authoritative GetTask.
    return {
        "task": {
            "id": task,
            "contextId": context,
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [
                {"artifactId": "untrusted", "parts": [{"text": "DO NOT IMPORT CALLBACK BODY"}]}
            ],
        }
    }


@pytest.fixture
def receiving(gateway, peer, monkeypatch):
    studio = gateway[0]
    monkeypatch.setenv("PEER_TOKEN", "synthetic-peer-token")
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    peer.update(push=True, mode="pause")
    profile = connection(studio, peer)
    callback = {}

    def sending(params):
        config = params["configuration"]
        callback.update(
            config.get("taskPushNotificationConfig") or config.get("pushNotificationConfig")
        )
        callback["early"] = post(
            callback["url"], event(), "Bearer " + callback["authentication"]["credentials"]
        )

    peer["on_send"] = sending
    base = gateway[2].split("/api/")[0]

    def start(version="1.0"):
        if version != profile["data"]["protocol_version"]:
            profile["data"]["protocol_version"] = version
            studio._write(studio.catalog._path("externalagents", profile["id"]), profile)
        code, run = post(
            base + "/api/runs",
            {
                "kind": "external",
                "external_agent_id": profile["id"],
                "task": "Review",
                "external_callbacks": True,
                "_callback_origin": "https://attacker.invalid",
            },
            "",
            **{"X-Nexus-Studio-Token": "csrf-test"},
        )
        assert code == 202, run
        assert done(studio, run)["status"] == "waiting"
        assert callback["early"][0] == 202
        return run

    yield studio, peer, callback, start, base, profile


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
@pytest.mark.parametrize("version", ["1.0", "0.3"])
def test_early_delivery_is_hint_and_recovery_reads_once(receiving, version):
    studio, peer, config, start, base, _ = receiving
    run = start(version)
    assert config["url"].startswith(base + "/api/a2a-callbacks/")
    row = studio.records(COLLECTION)[0]
    assert config["authentication"]["credentials"] not in json.dumps(row)
    assert "DO NOT IMPORT" not in json.dumps(row)
    assert studio.run(run["id"])["output"] == ""
    peer["mode"] = "inline"
    studio.external.callbacks.tick()
    assert studio.run(run["id"])["status"] == "completed"
    assert studio.run(run["id"])["output"] == "Remote report"
    assert len(peer["messages"]) == 1
    assert len([r for r in peer["requests"] if r[0] == "POST"]) == 2
    assert studio.external.callbacks.status(run["id"], None)["status"] == "verified"
    assert post(config["url"], event(), "Bearer " + config["authentication"]["credentials"])[1][
        "duplicate"
    ]
    studio.external.callbacks.tick()
    assert len([r for r in peer["requests"] if r[0] == "POST"]) == 2
    assert (
        post(
            config["url"],
            event(),
            "Bearer " + config["authentication"]["credentials"],
            "new-terminal",
        )[0]
        == 202
    )
    studio.external.callbacks.tick()
    assert studio.external.callbacks.status(run["id"], None)["status"] == "ignored"
    assert len(peer["messages"]) == 1


def test_rejects_bad_auth_identity_conflicting_receipt_and_csrf(receiving):
    studio, _, config, start, base, _ = receiving
    run = start()
    auth = "Bearer " + config["authentication"]["credentials"]
    assert post(config["url"], event(), "Bearer wrong")[0] == 403
    assert post(config["url"], event("other-task"), auth, "wrong-task")[0] == 400
    assert post(config["url"], event(context="wrong"), auth, "wrong-context")[0] == 400
    changed = event()
    changed["task"]["status"]["state"] = "TASK_STATE_WORKING"
    assert post(config["url"], changed, auth)[0] == 400
    assert post(config["url"], {"task": {}, "message": {}}, auth)[0] == 400
    assert (
        post(base + "/api/studio/external-runs/" + run["id"] + "/callbacks/revoke", {}, auth)[0]
        == 403
    )
    route = base + "/api/studio/external-runs/" + run["id"] + "/callbacks"
    with urllib.request.urlopen(route) as response:
        status = json.load(response)
    assert config["authentication"]["credentials"] not in json.dumps(status)
    assert post(route + "/revoke", {}, "", **{"X-Nexus-Studio-Token": "csrf-test"})[0] == 200
    assert post(config["url"], event(), auth)[0] == 403
    assert studio.records(COLLECTION)[0]["envelope"] is None


@pytest.mark.parametrize(
    "reason", ["expired", "changed-profile", "cancelled", "unacknowledged", "early-wrong-task"]
)
def test_no_read_for_inactive_or_unacknowledged_task(receiving, reason):
    studio, peer, config, start, _, profile = receiving
    run = start()
    path = studio._path("external-runs", run["id"])
    record = studio._read(path)
    row = studio.records(COLLECTION)[0]
    if reason == "expired":
        row["expires_at"] = time.time() - 1
    elif reason == "changed-profile":
        profile["data"]["streaming"] = True
        studio._write(studio.catalog._path("externalagents", profile["id"]), profile)
    elif reason == "cancelled":
        studio._update_run(run["id"], cancel_requested=True)
    elif reason == "unacknowledged":
        record.pop("task_id")
        studio._write(path, record)
    else:
        row["pending"]["task_id"] = "early-other-task"
    studio._write(studio.external.callbacks.path(row["id"]), row)
    studio.external.callbacks.tick()
    assert len(peer["requests"]) == 2
    assert not studio.records(COLLECTION)[0]["pending"]
    if reason in {"expired", "changed-profile"}:
        assert (
            post(
                config["url"], event(), "Bearer " + config["authentication"]["credentials"], "next"
            )[0]
            == 403
        )
    assert studio.run(run["id"])["status"] == "waiting"


def test_revocation_during_artifact_download_prevents_publication(receiving):
    studio, peer, _, start, _, _ = receiving
    run = start()
    peer.update(mode="url", block_file=True)
    reader = threading.Thread(target=studio.external.callbacks.tick)
    reader.start()
    assert peer["file_entered"].wait(5)
    studio.external.callbacks.revoke(run["id"], None)
    peer["file_release"].set()
    reader.join(5)
    assert not reader.is_alive()
    assert studio.run(run["id"])["status"] == "waiting"
    assert not studio.catalog.list("artifacts")


def test_claim_prevents_duplicate_concurrent_reads(receiving):
    studio, peer, _, start, _, _ = receiving
    run = start()
    peer.update(mode="url", block_file=True)
    reader = threading.Thread(target=studio.external.callbacks.tick)
    reader.start()
    assert peer["file_entered"].wait(5)
    studio.external.callbacks.tick()
    assert len(peer["messages"]) == 1
    assert len([r for r in peer["requests"] if r[0] == "POST"]) == 2
    peer["file_release"].set()
    reader.join(5)
    assert studio.run(run["id"])["status"] == "completed"


def test_opt_in_requires_origin_encryption_and_provider_capability(receiving, monkeypatch):
    studio, peer, _, _, _, profile = receiving
    payload = {
        "kind": "external",
        "external_agent_id": profile["id"],
        "task": "Review",
        "external_callbacks": True,
    }
    with pytest.raises(ValueError, match="origin"):
        studio.start_run({**payload, "_callback_origin": "https://spoof.invalid"})
    monkeypatch.delenv("NEXUS_CREDENTIALS_MASTER_KEY")
    with pytest.raises(ValueError, match="MASTER_KEY"):
        studio.start_run(payload, callback_origin="http://127.0.0.1:1234")
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    peer["push"] = False
    run = studio.start_run(payload, callback_origin="http://127.0.0.1:1234")
    assert done(studio, run)["status"] == "failed"
    assert not peer["messages"]
    assert not studio.records(COLLECTION)


@pytest.mark.parametrize(
    "url",
    [
        "http://public.example",
        "https://user:secret@example.com",
        "https://example.com/path",
        "https://example.com?q=a",
        "https://example.com#x",
    ],
)
def test_callback_origin_validation(url):
    with pytest.raises(ValueError):
        origin(url)


def test_retries_are_bounded_and_do_not_repeat_send(receiving):
    studio, peer, _, start, _, _ = receiving
    run = start()
    peer["mode"] = "malformed"
    now = time.time()
    for offset in [0, 5, 15, 100]:
        studio.external.callbacks.tick(now + offset)
    assert studio.external.callbacks.status(run["id"], None)["status"] == "read-failed"
    assert len(peer["messages"]) == 1
    assert len([r for r in peer["requests"] if r[0] == "POST"]) == 4
    assert studio.run(run["id"])["status"] == "waiting"


@pytest.mark.parametrize("gateway", ["files", "sqlite"], indirect=True)
def test_restart_recovers_receipt_and_backup_excludes_credentials(receiving, tmp_path):
    from nexus.core.studio import StudioService
    from nexus.core.studio_backup import create_snapshot, restore_snapshot

    studio, peer, _, start, _, _ = receiving
    run = start()
    peer["mode"] = "inline"
    options = (
        {
            "url": str(studio.sql_state.engine.url),
            "namespace": studio.sql_state.namespace,
            "workers": 1,
        }
        if studio.sql_state
        else None
    )
    root = studio.root
    studio.close()
    recovered = StudioService(root, state_options=options)
    try:
        recovered.external.callbacks.tick()
        assert recovered.run(run["id"])["status"] == "completed"
        assert len(peer["messages"]) == 1
        snapshot = tmp_path.parent / (tmp_path.name + "-callback-snapshot")
        create_snapshot(recovered.root, snapshot, quiesced=True, sql_state=recovered.sql_state)
        assert not (snapshot / "state" / COLLECTION).exists()
        restored = restore_snapshot(
            snapshot, tmp_path.parent / (tmp_path.name + "-callback-restored")
        )
        copy = StudioService(restored["root"], state_dir=restored["state_dir"])
        try:
            assert not copy.records(COLLECTION)
            assert copy.external.callbacks.status(run["id"], None)["status"] == "off"
        finally:
            copy.close()
    finally:
        recovered.close()


def test_team_callback_grant_is_owner_scoped_and_role_revocation_blocks_recovery(
    team, peer, monkeypatch
):
    studio, policy, _, request, _ = team
    monkeypatch.setenv("NEXUS_CREDENTIALS_MASTER_KEY", "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    policy.environment_resolver = lambda identity: ({"PEER_TOKEN": "synthetic-peer-token"}, None)
    profile = connection(studio, peer, actor="alice")
    peer.update(push=True, mode="pause")
    captured = {}
    peer["on_send"] = lambda params: captured.update(
        params["configuration"]["taskPushNotificationConfig"]
    )
    run = studio.start_run(
        {
            "kind": "external",
            "external_agent_id": profile["id"],
            "requester_nexus_id": "alice",
            "task": "Review",
            "external_callbacks": True,
        }
    )
    assert done(studio, run)["status"] == "waiting"
    route = "/api/studio/external-runs/" + run["id"] + "/callbacks"
    for user in ["bob", "owner", "viewer"]:
        assert request(route, user=user)[0] == 403
        assert request(route + "/revoke", {}, user=user)[0] == 403
    assert request(route, user="alice")[0] == 200
    row = studio.records(COLLECTION)[0]
    auth = "Bearer " + captured["authentication"]["credentials"]
    studio.external.callbacks.receive(row["id"], auth, event())
    policy.bindings["alice"] = "viewer"
    studio.external.callbacks.tick()
    assert studio.records(COLLECTION)[0]["status"] == "unavailable"
    with pytest.raises(PermissionError):
        studio.external.callbacks.receive(row["id"], auth, event(), "next")
    assert len(peer["messages"]) == 1


@pytest.mark.parametrize(
    "shape", ["statusUpdate", "artifactUpdate", "legacy-status", "legacy-artifact"]
)
def test_status_and_artifact_notifications_are_hints(receiving, shape):
    studio, peer, config, start, _, _ = receiving
    run = start()
    body = {"taskId": "remote-task", "contextId": "remote-context"}
    if "status" in shape.lower():
        body["status"] = {"state": "TASK_STATE_COMPLETED"}
    else:
        body["artifact"] = {"artifactId": "hint", "parts": [{"text": "DO NOT IMPORT"}]}
    payload = (
        {shape: body}
        if not shape.startswith("legacy-")
        else {**body, "kind": shape.removeprefix("legacy-") + "-update"}
    )
    assert (
        post(config["url"], payload, "Bearer " + config["authentication"]["credentials"], shape)[0]
        == 202
    )
    peer["mode"] = "inline"
    studio.external.callbacks.tick()
    assert studio.run(run["id"])["output"] == "Remote report"


def test_receipt_storage_is_bounded_and_http_body_limit_enforced(receiving):
    studio, _, config, start, _, _ = receiving
    start()
    auth = "Bearer " + config["authentication"]["credentials"]
    row = studio.records(COLLECTION)[0]
    for index in range(140):
        studio.external.callbacks.receive(row["id"], auth, event(), str(index))
    assert len(studio.records(COLLECTION)[0]["seen"]) == 128
    request = urllib.request.Request(
        config["url"],
        b"{}",
        headers={
            "Authorization": auth,
            "Content-Type": "application/json",
            "Content-Length": str(24 * 1024 * 1024 + 1),
        },
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request, timeout=5)
    assert error.value.code == 413


@pytest.mark.parametrize("gateway", ["sqlite"], indirect=True)
def test_independent_sql_workers_share_receipt_claim(receiving):
    from nexus.core.studio import StudioService

    studio, peer, _, start, _, _ = receiving
    run = start()
    other = StudioService(
        studio.root,
        state_options={
            "url": str(studio.sql_state.engine.url),
            "namespace": studio.sql_state.namespace,
            "workers": 1,
        },
    )
    peer.update(mode="url", block_file=True)
    reader = threading.Thread(target=studio.external.callbacks.tick)
    try:
        reader.start()
        assert peer["file_entered"].wait(5)
        other.external.callbacks.tick()
        assert len([r for r in peer["requests"] if r[0] == "POST"]) == 2
        peer["file_release"].set()
        reader.join(5)
        assert studio.run(run["id"])["status"] == "completed"
    finally:
        peer["file_release"].set()
        reader.join(5)
        other.close()

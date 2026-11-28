import hashlib
import io
import json
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_container_recovery import LABEL, ContainerRecovery, launch_environment
from nexus.core.studio_sandbox import StudioSandbox

NAME = "nexus-analysis-" + "a" * 32
IDENTITY = "b" * 64
IMAGE = "sha256:" + "c" * 64
DOCKER = ["docker", "--context", "default"]


def test_slim_linux_random_uuid_nodes_keep_identity_across_owners(recovery, monkeypatch, tmp_path):
    from nexus.core import studio_container_recovery as module

    boot = tmp_path / "boot-id"
    boot.write_text("28d0f917-a05b-44c5-baf8-0b51b647f21e\n")
    nodes = iter([0x010000000001, 0x010000000002])
    monkeypatch.setattr(module.uuid, "getnode", lambda: next(nodes))
    monkeypatch.setattr(module.socket, "gethostname", lambda: "fixture-controller")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(module, "Path", lambda _: boot, raising=False)
    studio = SimpleNamespace(
        state=recovery.directory.parent, _write=recovery.write, _read=recovery.read
    )
    first = ContainerRecovery(studio)
    engine = Engine()
    lease = register(first, engine)
    lease.created(IDENTITY)
    lease.finish(False)
    second = ContainerRecovery(studio)
    assert first.host == second.host
    assert second.recover(engine) == {"items": [{"name": NAME, "status": "removed"}], "pending": 0}


def test_real_mac_host_hash_stays_legacy_compatible(monkeypatch):
    from nexus.core import studio_container_recovery as module

    node = 0x020000000001
    monkeypatch.setattr(module.uuid, "getnode", lambda: node)
    monkeypatch.setattr(module.socket, "gethostname", lambda: "fixture-controller")
    expected = hashlib.sha256(f"fixture-controller:{node}".encode()).hexdigest()
    assert module.host_fingerprint() == expected


@pytest.mark.parametrize("change", ["boot", "hostname"])
def test_fallback_identity_does_not_adopt_changed_boot_or_controller(
    recovery, monkeypatch, tmp_path, change
):
    from nexus.core import studio_container_recovery as module

    boot = tmp_path / "boot-id"
    boot.write_text("28d0f917-a05b-44c5-baf8-0b51b647f21e\n")
    monkeypatch.setattr(module.uuid, "getnode", lambda: 0x010000000001)
    monkeypatch.setattr(module.socket, "gethostname", lambda: "fixture-controller")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(module, "Path", lambda _: boot, raising=False)
    studio = SimpleNamespace(
        state=recovery.directory.parent, _write=recovery.write, _read=recovery.read
    )
    first = ContainerRecovery(studio)
    engine = Engine()
    lease = register(first, engine)
    lease.created(IDENTITY)
    lease.finish(False)
    if change == "boot":
        boot.write_text("39a1f028-b16c-45d6-8c09-1c62c758032f\n")
    else:
        monkeypatch.setattr(module.socket, "gethostname", lambda: "different-controller")
    calls = len(engine.calls)
    assert ContainerRecovery(studio).recover(engine) == {
        "items": [{"name": NAME, "status": "other_host"}],
        "pending": 1,
    }
    assert len(engine.calls) == calls


@pytest.mark.parametrize("content", [None, "invalid", "x" * 1000])
def test_unavailable_kernel_identity_keeps_fail_closed_legacy_hash(monkeypatch, tmp_path, content):
    from nexus.core import studio_container_recovery as module

    boot = tmp_path / "boot-id"
    if content is not None:
        boot.write_text(content)
    node = 0x010000000001
    monkeypatch.setattr(module.uuid, "getnode", lambda: node)
    monkeypatch.setattr(module.socket, "gethostname", lambda: "fixture-controller")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(module, "Path", lambda _: boot, raising=False)
    assert (
        module.host_fingerprint()
        == hashlib.sha256(f"fixture-controller:{node}".encode()).hexdigest()
    )


class Engine:
    def __init__(self):
        self.calls = []
        self.available = True
        self.found = IDENTITY
        self.label = NAME
        self.route = "unix:///fixture.sock"
        self.lost_removal = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        assert 0 < kwargs["timeout"] <= 5
        if "context" in args and "inspect" in args:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [{"Name": "default", "Endpoints": {"docker": {"Host": self.route}}}]
                ).encode(),
            )
        if not self.available:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if "ps" in args:
            return SimpleNamespace(returncode=0, stdout=self.found.encode())
        if "inspect" in args:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "Id": self.found,
                            "Name": "/" + NAME,
                            "Config": {"Labels": {LABEL: self.label}},
                        }
                    ]
                ).encode(),
            )
        assert args[-3:] == ["rm", "--force", IDENTITY]
        self.found = ""
        if self.lost_removal:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout=b"")


@pytest.fixture
def recovery(tmp_path):
    studio = StudioService(tmp_path / "workspace")
    yield ContainerRecovery(studio)
    studio.close()


def register(recovery, engine):
    return recovery.register(NAME, DOCKER, launch_environment("default"), engine)


def test_pending_cleanup_survives_restart_without_payloads_or_environment_secrets(
    recovery, monkeypatch
):
    monkeypatch.setenv("UNRELATED_SECRET", "never-write-this")
    engine = Engine()
    lease = register(recovery, engine)
    lease.created(IDENTITY)
    assert recovery.recover(engine)["items"] == [{"name": NAME, "status": "owner_active"}]
    lease.finish(False)
    saved = lease.path.read_text()
    assert "never-write-this" not in saved and "unix:///fixture.sock" not in saved
    restarted = ContainerRecovery(
        SimpleNamespace(state=recovery.directory.parent, _write=recovery.write, _read=recovery.read)
    )
    assert restarted.recover(engine) == {
        "items": [{"name": NAME, "status": "removed"}],
        "pending": 0,
    }
    assert engine.calls[-1] == ["docker", "--host", engine.route, "rm", "--force", IDENTITY]
    assert not any("start" in args or "run" in args or "create" in args for args in engine.calls)


def test_unavailable_daemon_keeps_record_then_recovers_without_replaying(recovery):
    engine = Engine()
    lease = register(recovery, engine)
    lease.created(IDENTITY)
    lease.finish(False)
    engine.available = False
    assert recovery.recover(engine)["pending"] == 1
    engine.available = True
    assert recovery.recover(engine)["pending"] == 0


def test_lost_create_absence_is_not_success_and_late_container_is_removed(recovery):
    engine = Engine()
    lease = register(recovery, engine)
    lease.finish(False)
    engine.found = ""
    assert recovery.recover(engine) == {
        "items": [{"name": NAME, "status": "awaiting_creation"}],
        "pending": 1,
    }
    engine.found = IDENTITY
    assert recovery.recover(engine)["pending"] == 0


@pytest.mark.parametrize("change", ["route", "identity", "label"])
def test_recovery_preserves_changed_routes_and_unowned_containers(recovery, change):
    engine = Engine()
    lease = register(recovery, engine)
    lease.created(IDENTITY)
    lease.finish(False)
    if change == "route":
        engine.route = "ssh://another-host"
    elif change == "identity":
        engine.found = "d" * 64
    else:
        engine.label = "someone-else"
    result = recovery.recover(engine)
    assert result["pending"] == 1
    assert result["items"][0]["status"] == (
        "route_changed" if change == "route" else "ownership_mismatch"
    )
    assert not any("rm" in args for args in engine.calls)


def test_lost_remove_reply_is_resolved_by_absence_on_next_recovery(recovery):
    engine = Engine()
    lease = register(recovery, engine)
    lease.finish(False)
    engine.lost_removal = True
    assert recovery.recover(engine)["pending"] == 1
    assert not json.loads(lease.path.read_text())["creation_uncertain"]
    assert recovery.recover(engine)["pending"] == 0
    assert sum("rm" in args for args in engine.calls) == 1


def test_concurrent_recovery_cannot_remove_the_same_container_twice(recovery):
    engine = Engine()
    lease = register(recovery, engine)
    lease.finish(False)
    entered, release = threading.Event(), threading.Event()

    def blocked(args, **kwargs):
        if "rm" in args:
            entered.set()
            assert release.wait(2)
        return engine(args, **kwargs)

    owner = threading.Thread(target=recovery.recover, args=(blocked,))
    owner.start()
    try:
        assert entered.wait(2)
        assert recovery.recover(engine)["items"][0]["status"] == "owner_active"
    finally:
        release.set()
        owner.join(3)
    assert not owner.is_alive()
    assert sum("rm" in args for args in engine.calls) == 1


def test_corrupt_record_is_retained_without_docker_mutations(recovery):
    engine = Engine()
    lease = register(recovery, engine)
    lease.finish(False)
    lease.path.write_text("[]")
    before = len(engine.calls)
    assert recovery.recover(engine)["pending"] == 1
    assert len(engine.calls) == before


def test_other_worker_host_cannot_retire_shared_volume_cleanup_authority(recovery):
    engine = Engine()
    lease = register(recovery, engine)
    lease.created(IDENTITY)
    lease.finish(False)
    before = len(engine.calls)
    recovery.host = "different-worker-host"
    report = recovery.recover(engine)
    assert report == {"items": [{"name": NAME, "status": "other_host"}], "pending": 1}
    assert len(engine.calls) == before


def test_host_cleanup_journal_is_excluded_from_backups(tmp_path):
    from nexus.core import studio_backup

    root = tmp_path / "workspace"
    studio = StudioService(root)
    recovery = studio.data_tools.sandbox.recovery
    lease = register(recovery, Engine())
    lease.finish(False)
    studio.close()
    backup = tmp_path / "backup"
    studio_backup.create_snapshot(root, backup, quiesced=True)
    assert not list(backup.rglob("analysis-workers"))
    restored = studio_backup.restore_snapshot(backup, tmp_path / "restored")
    copy = StudioService(restored["root"], state_dir=restored["state_dir"])
    try:
        assert copy.data_tools.sandbox.recovery.pending() == 0
    finally:
        copy.close()


def test_owner_readiness_reports_pending_workers_without_running_docker(tmp_path):
    from nexus.core.studio_readiness import deployment_readiness

    studio = StudioService(tmp_path)
    try:
        lease = register(studio.data_tools.sandbox.recovery, Engine())
        lease.finish(False)
        report = deployment_readiness(studio, None)
        item = next(row for row in report["items"] if row["name"] == "Analysis container ownership")
        assert item["status"] == "not_verified" and "1 active or unresolved" in item["detail"]
    finally:
        studio.close()


class Client:
    def __init__(self, *, create=False):
        self.stdin, self.stdout, self.stderr = (
            io.BytesIO(),
            io.BytesIO(b'{"stdout":"ok","files":[]}'),
            io.BytesIO(),
        )
        self.returncode = 0
        self.create = create
        self.killed = False

    def communicate(self, timeout):
        return IDENTITY.encode(), b""

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        return self.returncode


@pytest.mark.parametrize("failure", [None, "create_timeout", "create_failed", "shutdown"])
def test_create_acknowledgment_and_shutdown_gate_start_exactly_once(monkeypatch, failure):
    calls = []

    def run(args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "Name": "default",
                        "Endpoints": {"docker": {"Host": "unix:///fixture.sock"}},
                    }
                ]
            ).encode(),
        )

    sandbox = StudioSandbox({"python": {"image": IMAGE}}, run=run)
    monkeypatch.setattr("nexus.core.studio_sandbox.shutil.which", lambda _: "docker")

    def popen(args, **kwargs):
        calls.append(args)
        client = Client(create="create" in args)
        if "create" in args:
            if failure == "create_timeout":

                def communicate(timeout):
                    raise subprocess.TimeoutExpired(args, timeout)

                client.communicate = communicate
            elif failure == "create_failed":
                client.returncode = 1
            elif failure == "shutdown":

                def communicate(timeout):
                    sandbox.close(wait=False)
                    return IDENTITY.encode(), b""

                client.communicate = communicate
        return client

    monkeypatch.setattr("nexus.core.studio_sandbox.subprocess.Popen", popen)
    if failure:
        with pytest.raises((ValueError, subprocess.TimeoutExpired)):
            sandbox.execute("print('must run at most once')")
    else:
        assert sandbox.execute("print('ok')") == {"stdout": "ok", "files": []}
    starts = [args for args in calls if "start" in args]
    assert len(starts) == (0 if failure else 1)
    if starts:
        assert starts[0] == [
            "docker",
            "--host",
            "unix:///fixture.sock",
            "start",
            "--attach",
            "--interactive",
            IDENTITY,
        ]
    assert "create" in calls[0] and "--pull=never" in calls[0]

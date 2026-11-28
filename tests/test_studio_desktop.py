"""Tests for Nexus Studio standalone desktop lifecycle, security, and locking."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import pytest
import requests

from nexus.core.studio import StudioService
from nexus.core.studio_desktop import (
    DesktopBackendRunner,
    DesktopLaunchCredential,
    DesktopSecurityPolicy,
    UnsafeWorkspaceError,
    WorkspaceLock,
    check_desktop_prerequisites,
    check_workspace_root,
    find_available_loopback_port,
    get_desktop_dirs,
    get_desktop_platform,
    import_workspace,
    is_process_alive,
)
from nexus.core.studio_desktop_cli import desktop_command
from nexus.studio import make_studio_handler


def test_platform_detection_and_directories():
    plat = get_desktop_platform()
    assert plat in {"darwin", "linux", "windows"}

    dirs = get_desktop_dirs("NexusTestStudio")
    assert "data" in dirs
    assert "config" in dirs
    assert "cache" in dirs
    assert "logs" in dirs
    assert "backups" in dirs
    assert "NexusTestStudio" in str(dirs["data"]) or "nexus-studio" in str(dirs["data"])


def test_loopback_port_allocation():
    port1 = find_available_loopback_port()
    assert 1024 <= port1 <= 65535

    # Check port is bound only to loopback
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port1))
        # Finding port again should choose a different one
        port2 = find_available_loopback_port(port1)
        assert port2 != port1


def test_process_alive_check():
    assert is_process_alive(os.getpid()) is True
    # Nonexistent PID (e.g. 999999)
    assert is_process_alive(999999) is False
    assert is_process_alive(-1) is False


def test_workspace_lock_lifecycle(tmp_path):
    ws = tmp_path / "test_workspace"
    ws.mkdir()
    lock = WorkspaceLock(ws)

    assert lock.is_locked() is False
    assert lock.acquire(port=5003, token="secret-token-123") is True
    assert lock.is_locked() is True

    info = lock.read()
    assert info is not None
    assert info["pid"] == os.getpid()
    assert info["port"] == 5003
    assert "token_hash" in info

    # Second acquisition fails while process is alive
    second_lock = WorkspaceLock(ws)
    assert second_lock.acquire(port=5004, token="second-token") is False

    # Release frees the lock
    assert lock.release() is True
    assert lock.is_locked() is False
    assert second_lock.acquire(port=5004, token="second-token") is True
    assert second_lock.release() is True


def test_workspace_lock_stale_detection(tmp_path):
    ws = tmp_path / "stale_workspace"
    ws.mkdir()
    lock_file = ws / ".nexus-workspace.lock"

    # Write a lock file claiming a dead PID
    fake_info = {
        "pid": 999999,
        "port": 5003,
        "token_hash": "abc",
        "acquired_at": "2026-10-20T00:00:00Z",
    }
    lock_file.write_text(json.dumps(fake_info), encoding="utf-8")

    lock = WorkspaceLock(ws)
    # is_locked should detect that PID 999999 is dead
    assert lock.is_locked() is False

    # Acquiring should succeed by breaking the stale lock
    assert lock.acquire(port=5003, token="fresh-token") is True
    assert lock.read()["pid"] == os.getpid()
    lock.release()


def test_desktop_security_policy():
    # Host header validation
    assert DesktopSecurityPolicy.validate_host_header("127.0.0.1:5003", 5003) is True
    assert DesktopSecurityPolicy.validate_host_header("localhost:5003", 5003) is True
    assert DesktopSecurityPolicy.validate_host_header("127.0.0.1:5003", 8080) is False
    assert DesktopSecurityPolicy.validate_host_header("evil.com:5003") is False
    assert DesktopSecurityPolicy.validate_host_header("192.168.1.100:5003") is False

    # Origin validation
    assert DesktopSecurityPolicy.validate_origin("http://127.0.0.1:5003") is True
    assert DesktopSecurityPolicy.validate_origin("http://localhost:5003") is True
    assert DesktopSecurityPolicy.validate_origin("tauri://localhost") is True
    assert DesktopSecurityPolicy.validate_origin("http://tauri.localhost") is True
    assert DesktopSecurityPolicy.validate_origin("https://evil.attacker.com") is False

    # External URL validation for system browser opener
    assert DesktopSecurityPolicy.validate_external_url("https://github.com/ghabs-org/nexus-arc") is True
    assert DesktopSecurityPolicy.validate_external_url("mailto:support@nexus.io") is True
    assert DesktopSecurityPolicy.validate_external_url("file:///etc/passwd") is False
    assert DesktopSecurityPolicy.validate_external_url("javascript:alert(1)") is False
    assert DesktopSecurityPolicy.validate_external_url("data:text/html,<script>") is False
    assert DesktopSecurityPolicy.validate_external_url("http://127.0.0.1:5003/api/bootstrap") is False
    assert DesktopSecurityPolicy.validate_external_url("http://192.168.1.1/admin") is False


def test_workspace_import(tmp_path):
    src = tmp_path / "source_ws"
    src.mkdir()
    (src / "test_file.txt").write_text("Hello Studio", encoding="utf-8")
    sub = src / "definitions"
    sub.mkdir()
    (sub / "agent.yaml").write_text("name: TestAgent", encoding="utf-8")

    dst = tmp_path / "dest_ws"
    res = import_workspace(src, dst, backup_first=False)
    assert res["success"] is True
    assert (dst / "test_file.txt").read_text(encoding="utf-8") == "Hello Studio"
    assert (dst / "definitions" / "agent.yaml").is_file()

    # Re-importing with backup
    (dst / "test_file.txt").write_text("Modified Studio", encoding="utf-8")
    res2 = import_workspace(src, dst, backup_first=True)
    assert res2["success"] is True
    assert res2["backup"] is not None
    assert Path(res2["backup"]).is_file()


def test_workspace_import_locked_destination(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst_locked"
    dst.mkdir()

    lock = WorkspaceLock(dst)
    lock.acquire(port=5003, token="tok")
    try:
        with pytest.raises(ValueError, match="Destination workspace is locked"):
            import_workspace(src, dst)
    finally:
        lock.release()


def test_desktop_prerequisites_check():
    audit = check_desktop_prerequisites()
    assert audit["ready"] is True
    assert audit["platform"] in {"darwin", "linux", "windows"}
    checks = {c["name"]: c for c in audit["checks"]}
    assert "python" in checks
    assert checks["python"]["status"] == "available"
    assert "docker" in checks
    assert "node" in checks
    assert "git" in checks


def test_desktop_cli_subcommands(tmp_path, capsys):
    ws = tmp_path / "cli_ws"
    ws.mkdir()

    # 1. Preflight
    class PreflightArgs:
        desktop_subcommand = "preflight"
        json_output = True

    code = desktop_command(PreflightArgs())
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["ready"] is True

    # 2. Status (unlocked)
    class StatusArgs:
        desktop_subcommand = "status"
        dir = str(ws)
        json_output = True

    code = desktop_command(StatusArgs())
    assert code == 0
    captured = capsys.readouterr()
    stat_data = json.loads(captured.out)
    assert stat_data["locked"] is False

    # 3. Status (locked)
    lock = WorkspaceLock(ws)
    lock.acquire(5003, "tok")
    try:
        code = desktop_command(StatusArgs())
        assert code == 0
        captured = capsys.readouterr()
        stat_data = json.loads(captured.out)
        assert stat_data["locked"] is True
        assert stat_data["info"]["port"] == 5003
    finally:
        lock.release()


# Shared with apps/studio-desktop/src-tauri/src/backend.rs tests.
NATIVE_SECRET = bytes(range(32))
NATIVE_PROOF = "4b7a52c3560283554eb58b5d08d9b702439989429f5764056d46f5b9ea4a51ce"
NATIVE_SESSION = "1b21ee8c700106024d6068c1c6d0152b72063952162b9f2aa880a8114189c0f5"


def test_launch_credential_matches_native_shell_and_reads_strict_stdin():
    credential = DesktopLaunchCredential.read(io.BytesIO(NATIVE_SECRET.hex().encode() + b"\n"))
    assert credential.proof("ab" * 32, "/private/workspace", 43123) == NATIVE_PROOF
    assert credential.session() == NATIVE_SESSION
    assert credential.line() == NATIVE_SECRET.hex().encode() + b"\n"
    for bad in (b"", NATIVE_SECRET.hex().upper().encode() + b"\n", NATIVE_SECRET.hex().encode()):
        with pytest.raises(ValueError) as error:
            DesktopLaunchCredential.read(io.BytesIO(bad))
        assert NATIVE_SECRET.hex() not in str(error.value).lower()
    with pytest.raises(ValueError):
        credential.proof("AB" * 32, "/private/workspace", 43123)
    assert credential.session_ok(f"other=1; nexus_desktop_session={NATIVE_SESSION}")
    assert not credential.session_ok(f"nexus_desktop_session={NATIVE_SESSION[:-1]}0")
    assert not credential.session_ok(None)


@pytest.fixture
def studio_server(tmp_path):
    servers = []

    def start(launch):
        studio = StudioService(tmp_path / f"workspace-{len(servers)}")
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_studio_handler(studio, "c" * 43, launch)
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append((server, studio))
        return studio, f"http://127.0.0.1:{server.server_port}", server.server_port

    yield start
    for server, studio in servers:
        server.shutdown()
        server.server_close()
        studio.close()


def test_native_launch_proof_and_session_gate_local_api(studio_server):
    credential = DesktopLaunchCredential.generate()
    studio, base, port = studio_server(credential)
    with requests.Session() as client:
        client.trust_env = False
        challenge = os.urandom(32).hex()
        answer = client.get(
            base + "/api/desktop/handshake", headers={"X-Nexus-Desktop-Challenge": challenge}
        )
        assert answer.json() == {"proof": credential.proof(challenge, str(studio.root), port)}
        assert client.get(base + "/api/desktop/handshake").status_code == 400

        anonymous = client.get(base + "/api/bootstrap")
        assert anonymous.status_code == 401 and "token" not in anonymous.json()
        assert client.get(base + "/api/auth/status").json()["authenticated"] is False
        assert client.get(base + "/").status_code == 200  # static shell carries no secret
        csrf = {"X-Nexus-Studio-Token": "c" * 43}
        assert client.post(base + "/api/missing", json={"a": 1}, headers=csrf).status_code == 401
        assert client.delete(base + "/api/agents/missing", headers=csrf).status_code == 401
        forged = {"Cookie": f"{DesktopLaunchCredential.COOKIE}={'0' * 64}"}
        assert client.get(base + "/api/bootstrap", headers=forged).status_code == 401

        session = {"Cookie": f"{DesktopLaunchCredential.COOKIE}={credential.session()}"}
        assert client.get(base + "/api/bootstrap", headers=session).json()["token"] == "c" * 43
        assert client.get(base + "/api/auth/status", headers=session).json()["authenticated"]
        missing = client.post(base + "/api/missing", json={"a": 1}, headers={**csrf, **session})
        assert missing.status_code == 404


def test_desktop_mode_without_native_launch_is_unchanged(studio_server):
    _, base, _ = studio_server(None)
    with requests.Session() as client:
        client.trust_env = False
        assert client.get(base + "/api/bootstrap").json()["token"] == "c" * 43
        challenge = {"X-Nexus-Desktop-Challenge": "ab" * 32}
        assert "proof" not in client.get(base + "/api/desktop/handshake", headers=challenge).text


def test_studio_cli_rejects_invalid_launch_credential_without_echo(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "nexus.cli", "studio", "--dir", str(tmp_path), "--port", "0",
         "--desktop-launch-stdin"],
        input=b"not-the-secret\n",
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 2
    assert b"Invalid desktop launch credential" in result.stderr
    assert b"not-the-secret" not in result.stderr + result.stdout


HOLD_LOCK = """
import sys, time
from nexus.core.studio_desktop import WorkspaceLock
assert WorkspaceLock(sys.argv[1]).acquire(5003)
print("held", flush=True)
time.sleep(60)
"""


def test_workspace_lock_is_kernel_owned_and_released_when_holder_dies(tmp_path):
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLD_LOCK, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        assert WorkspaceLock(tmp_path).is_locked()
        assert WorkspaceLock(tmp_path).read()["pid"] == holder.pid
        assert not WorkspaceLock(tmp_path).acquire(5004)
    finally:
        holder.kill()
        holder.wait(10)
    # A crashed holder leaves its metadata, but never its lock.
    assert WorkspaceLock(tmp_path).read()["pid"] == holder.pid
    assert not WorkspaceLock(tmp_path).is_locked()
    # A live but unrelated PID in the file is not treated as the owner.
    (tmp_path / ".nexus-workspace.lock").write_text(json.dumps({"pid": os.getpid()}))
    lock = WorkspaceLock(tmp_path)
    assert not lock.is_locked() and lock.acquire(5005)
    lock.release()


def test_second_studio_launch_exits_in_use_before_touching_workspace(tmp_path):
    lock = WorkspaceLock(tmp_path)
    assert lock.acquire(5003)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nexus.cli", "studio", "--dir", str(tmp_path), "--port", "0"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        lock.release()
    assert result.returncode == 75
    assert f"already open in another Nexus Studio (process {os.getpid()}" in result.stderr
    assert not (tmp_path / ".nexus").exists()


GROUP = """
import os, subprocess, sys, time
from nexus.core.studio_desktop import end_owned_process_group
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
print(child.pid, flush=True)
end_owned_process_group()
print("survived", flush=True)
"""


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows uses job objects")
@pytest.mark.parametrize("own_group", [True, False])
def test_process_group_cleanup_only_when_leading_own_group(own_group):
    process = subprocess.run(
        [sys.executable, "-c", GROUP],
        capture_output=True,
        text=True,
        timeout=30,
        start_new_session=own_group,
        check=False,
    )
    child = int(process.stdout.split()[0])
    try:
        if own_group:
            assert process.returncode == -9 and "survived" not in process.stdout
            deadline = time.monotonic() + 5
            while is_process_alive(child) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not is_process_alive(child)
        else:
            assert process.returncode == 0 and "survived" in process.stdout
    finally:
        if is_process_alive(child):
            os.kill(child, 9)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX; Windows uses job objects")
def test_native_startup_error_reports_status_instead_of_killing_its_group(tmp_path):
    # A newer release's workspace: an older backend must refuse it, visibly.
    schema = tmp_path / ".nexus" / "studio" / "system" / "schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text(json.dumps({"version": 2}))
    result = subprocess.run(
        [sys.executable, "-m", "nexus.cli", "studio", "--dir", str(tmp_path), "--port", "0",
         "--desktop-launch-stdin"],
        input=DesktopLaunchCredential.generate().line(),
        capture_output=True,
        timeout=60,
        start_new_session=True,  # leads its own group, exactly like the shell's sidecar
        check=False,
    )
    assert result.returncode == 65, result.returncode  # the shell's newer-workspace screen
    assert b"Unsupported Studio state version" in result.stderr
    assert json.loads(schema.read_text()) == {"version": 2}
    assert not WorkspaceLock(tmp_path).is_locked()


def test_workspace_root_must_be_a_dedicated_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Projects" / "studio").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    assert check_workspace_root(home / "Projects" / "studio") == (home / "Projects" / "studio").resolve()
    assert check_workspace_root(home / "Library" / "Application Support" / "x" / "workspace")
    for refused in (home, tmp_path, Path("/"), home / "Apps" / "Nexus Studio.app" / "Contents"):
        with pytest.raises(UnsafeWorkspaceError, match="dedicated folder"):
            check_workspace_root(refused)
    if get_desktop_platform() == "darwin":
        for refused in ("/System/Library", "/Applications/Studio", "/usr/local/share"):
            with pytest.raises(UnsafeWorkspaceError):
                check_workspace_root(refused)


def test_native_launch_refuses_home_folder_before_creating_anything(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "nexus.cli", "studio", "--dir", str(home), "--port", "0",
         "--desktop-launch-stdin"],
        input=DesktopLaunchCredential.generate().line(),
        capture_output=True,
        timeout=60,
        start_new_session=True,
        env={**os.environ, "HOME": str(home)},
        check=False,
    )
    assert result.returncode == 64, result.stderr
    assert b"dedicated folder" in result.stderr
    assert list(home.iterdir()) == []


def test_import_refuses_open_or_nested_sources(tmp_path):
    source = tmp_path / "source"
    (source / "agents").mkdir(parents=True)
    with pytest.raises(ValueError, match="separate folders"):
        import_workspace(source, source / "copy", backup_first=False)
    lock = WorkspaceLock(source)
    assert lock.acquire(5003)
    try:
        with pytest.raises(ValueError, match="open in another Nexus Studio"):
            import_workspace(source, tmp_path / "copy", backup_first=False)
    finally:
        lock.release()
    assert not (tmp_path / "copy").exists()


def test_sidecar_routes_native_import_to_desktop_cli(tmp_path):
    source = tmp_path / "source"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "reviewer.yaml").write_text("name: Reviewer\n")
    result = subprocess.run(
        [sys.executable, "-c", "from nexus.core.studio_desktop_sidecar import main; main()",
         "--nexus-desktop", "import", "--from", str(source), "--dir", str(tmp_path / "copy"), "--json"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["success"] is True
    assert (tmp_path / "copy" / "agents" / "reviewer.yaml").read_text() == "name: Reviewer\n"

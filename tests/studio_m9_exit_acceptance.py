"""Desktop helper checks; does not launch the native shell or verify an installer.

Run from repository root:
PYTHONPATH=. .venv/bin/python3 tests/studio_m9_exit_acceptance.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from nexus.core.studio_desktop import (
    DesktopBackendRunner,
    DesktopSecurityPolicy,
    WorkspaceLock,
    check_desktop_prerequisites,
    find_available_loopback_port,
    get_desktop_dirs,
    get_desktop_platform,
    import_workspace,
    is_process_alive,
)


def verify_platform_directories_and_isolation() -> None:
    print("[1/6] Verifying platform directory resolution and isolation...")
    dirs = get_desktop_dirs()
    assert all(k in dirs for k in ("data", "config", "cache", "logs", "backups"))
    plat = get_desktop_platform()
    assert plat in {"darwin", "linux", "windows"}


def verify_instance_locking_and_concurrency(workspace: Path) -> None:
    print("[2/6] Verifying workspace instance locking and concurrency prevention...")
    lock1 = WorkspaceLock(workspace)
    assert lock1.is_locked() is False

    # Acquire lock
    assert lock1.acquire(port=5003, token="auth-token-1") is True
    assert lock1.is_locked() is True
    info = lock1.read()
    assert info["pid"] == os.getpid()
    assert info["port"] == 5003

    # Duplicate acquisition fails
    lock2 = WorkspaceLock(workspace)
    assert lock2.acquire(port=5004, token="auth-token-2") is False

    # Release frees lock
    assert lock1.release() is True
    assert lock1.is_locked() is False

    # Stale lock cleanup
    lock_file = workspace / ".nexus-workspace.lock"
    lock_file.write_text(json.dumps({"pid": 999999, "port": 5003, "token_hash": "dead"}), encoding="utf-8")
    assert lock1.is_locked() is False
    assert lock1.acquire(port=5003, token="auth-token-3") is True
    assert lock1.release() is True


def verify_desktop_security_boundaries() -> None:
    print("[3/6] Verifying loopback host, origin, and external URL security boundaries...")
    # Loopback Host
    assert DesktopSecurityPolicy.validate_host_header("127.0.0.1:5003", 5003) is True
    assert DesktopSecurityPolicy.validate_host_header("localhost:5003", 5003) is True
    assert DesktopSecurityPolicy.validate_host_header("evil.com:5003", 5003) is False
    assert DesktopSecurityPolicy.validate_host_header("192.168.1.50:5003", 5003) is False

    # Origin
    assert DesktopSecurityPolicy.validate_origin("tauri://localhost") is True
    assert DesktopSecurityPolicy.validate_origin("http://tauri.localhost") is True
    assert DesktopSecurityPolicy.validate_origin("http://127.0.0.1:5003") is True
    assert DesktopSecurityPolicy.validate_origin("https://attacker.org") is False

    # External URL opener
    assert DesktopSecurityPolicy.validate_external_url("https://docs.nexus.io") is True
    assert DesktopSecurityPolicy.validate_external_url("file:///etc/passwd") is False
    assert DesktopSecurityPolicy.validate_external_url("javascript:void(0)") is False
    assert DesktopSecurityPolicy.validate_external_url("http://127.0.0.1:5003/api/secret") is False
    assert DesktopSecurityPolicy.validate_external_url("http://10.0.0.1/admin") is False


def verify_workspace_import_and_backup(tmp_dir: Path) -> None:
    print("[4/6] Verifying workspace import, backup creation, and writer exclusivity...")
    src = tmp_dir / "src_ws"
    src.mkdir()
    (src / "agent.yaml").write_text("name: Assistant", encoding="utf-8")

    dst = tmp_dir / "dst_ws"
    dst.mkdir()
    (dst / "initial.txt").write_text("v1", encoding="utf-8")

    res = import_workspace(src, dst, backup_first=True)
    assert res["success"] is True
    assert (dst / "agent.yaml").is_file()
    assert res["backup"] is not None and Path(res["backup"]).is_file()

    # Destination locked check
    lock = WorkspaceLock(dst)
    lock.acquire(5003, "tok")
    try:
        failed = False
        try:
            import_workspace(src, dst)
        except ValueError:
            failed = True
        assert failed is True, "Import must fail when destination is locked"
    finally:
        lock.release()


def verify_desktop_backend_lifecycle(workspace: Path) -> None:
    print("[5/6] Verifying desktop backend runner and port allocation...")
    port = find_available_loopback_port()
    assert 1024 <= port <= 65535

    runner = DesktopBackendRunner(workspace, port=port)
    assert runner.port == port
    assert len(runner.token) >= 32

    # Verify single-instance detection
    runner.lock.acquire(port, runner.token)
    try:
        dup = runner.start()
        assert dup["success"] is False
        assert dup["already_running"] is True
    finally:
        runner.lock.release()


def verify_system_prerequisites() -> None:
    print("[6/6] Verifying prerequisite audit...")
    audit = check_desktop_prerequisites()
    assert audit["ready"] is True
    names = {c["name"] for c in audit["checks"]}
    assert {"python", "docker", "node", "git"}.issubset(names)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        verify_platform_directories_and_isolation()
        verify_instance_locking_and_concurrency(tmp_dir / "ws_lock")
        verify_desktop_security_boundaries()
        verify_workspace_import_and_backup(tmp_dir)
        verify_desktop_backend_lifecycle(tmp_dir / "ws_backend")
        verify_system_prerequisites()

    print("Desktop helper checks passed; native shell startup and installed builds remain unverified.")


if __name__ == "__main__":
    main()

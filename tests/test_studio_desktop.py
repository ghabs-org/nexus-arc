"""Tests for Nexus Studio standalone desktop lifecycle, security, and locking."""

import json
import os
from pathlib import Path
import sys

import pytest

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
from nexus.core.studio_desktop_cli import desktop_command


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

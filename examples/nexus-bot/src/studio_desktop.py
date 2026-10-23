"""Exercise Nexus Studio standalone desktop lifecycle against disposable workspace.

Run from repository root:
PYTHONPATH=. .venv/bin/python3 examples/nexus-bot/src/studio_desktop.py
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from nexus.core.studio_desktop import (
    DesktopBackendRunner,
    DesktopSecurityPolicy,
    WorkspaceLock,
    check_desktop_prerequisites,
    get_desktop_dirs,
    get_desktop_platform,
    import_workspace,
)


def run_platform_dirs_demo() -> None:
    print("=== Step 1: Resolve platform desktop user directories ===")
    plat = get_desktop_platform()
    dirs = get_desktop_dirs()
    print(f"Platform: {plat}")
    print(f"  Data dir:   {dirs['data']}")
    print(f"  Config dir: {dirs['config']}")
    print(f"  Logs dir:   {dirs['logs']}")
    print(f"  Backups:    {dirs['backups']}")


def run_prerequisites_demo() -> None:
    print("\n=== Step 2: Check desktop system prerequisites ===")
    audit = check_desktop_prerequisites()
    print(f"System: {audit['platform']} ({audit['architecture']}) - Ready: {audit['ready']}")
    for c in audit["checks"]:
        ver = f" ({c['version']})" if c.get("version") else ""
        print(f"  [{c['status'].upper()}] {c['name']}{ver}")


def run_workspace_lock_demo(workspace: Path) -> None:
    print("\n=== Step 3: Workspace instance locking and single-instance guard ===")
    lock1 = WorkspaceLock(workspace)
    assert lock1.acquire(port=5003, token="ephemeral-token-abc") is True
    print(f"Acquired instance lock for workspace: PID {lock1.read()['pid']}, port {lock1.read()['port']}")

    # Try duplicate lock
    lock2 = WorkspaceLock(workspace)
    assert lock2.acquire(port=5004, token="second-token") is False
    print("Duplicate lock rejected as expected: another instance is running.")

    # Release
    assert lock1.release() is True
    print("Instance lock released successfully.")


def run_workspace_import_demo(tmp_dir: Path) -> None:
    print("\n=== Step 4: Workspace import and automated backup ===")
    src = tmp_dir / "old_workspace"
    src.mkdir()
    (src / "agent.yaml").write_text("name: CustomerSupport", encoding="utf-8")

    dst = tmp_dir / "new_workspace"
    dst.mkdir()
    (dst / "existing.txt").write_text("Pre-existing data", encoding="utf-8")

    res = import_workspace(src, dst, backup_first=True)
    print(f"Imported {res['copied_items']} items into {res['target']}")
    print(f"Created pre-import backup archive: {res['backup']}")
    assert Path(dst / "agent.yaml").is_file()
    assert res["backup"] is not None and Path(res["backup"]).is_file()


def run_security_policy_demo() -> None:
    print("\n=== Step 5: Desktop loopback and external URL security policy ===")
    # Loopback checks
    assert DesktopSecurityPolicy.validate_host_header("127.0.0.1:5003", 5003) is True
    assert DesktopSecurityPolicy.validate_host_header("evil.com:5003", 5003) is False
    print("Host header validation: strictly enforces loopback binding.")

    # Origin checks
    assert DesktopSecurityPolicy.validate_origin("tauri://localhost") is True
    assert DesktopSecurityPolicy.validate_origin("https://evil.org") is False
    print("Origin validation: enforces Tauri desktop shell origins.")

    # External URL opener checks
    assert DesktopSecurityPolicy.validate_external_url("https://github.com/ghabs-org/nexus-arc") is True
    assert DesktopSecurityPolicy.validate_external_url("file:///etc/shadow") is False
    print("External URL opener: rejects local files, javascript, and internal network addresses.")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        run_platform_dirs_demo()
        run_prerequisites_demo()
        run_workspace_lock_demo(tmp_dir / "workspace")
        run_workspace_import_demo(tmp_dir)
        run_security_policy_demo()

    print("\nDesktop lifecycle demonstration passed successfully.")


if __name__ == "__main__":
    main()

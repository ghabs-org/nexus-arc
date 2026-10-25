"""Nexus Studio standalone desktop lifecycle, locking, and platform services.

Implements workspace instance locking, platform user directories, loopback security,
prerequisite auditing, workspace import/backup, and child process lifecycle management
for the Tauri 2 desktop shell.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

# Platform directories
def get_desktop_platform() -> str:
    """Return normalized platform identifier: 'darwin', 'linux', or 'windows'."""
    sys_plat = sys.platform.lower()
    if sys_plat.startswith("darwin"):
        return "darwin"
    if sys_plat.startswith("win"):
        return "windows"
    return "linux"


def get_desktop_dirs(app_name: str = "NexusStudio") -> dict[str, Path]:
    """Resolve standard user data, config, cache, and log paths per platform."""
    plat = get_desktop_platform()
    home = Path.home()

    if plat == "darwin":
        base_data = home / "Library" / "Application Support" / app_name
        base_config = home / "Library" / "Preferences" / app_name
        base_cache = home / "Library" / "Caches" / app_name
        base_logs = home / "Library" / "Logs" / app_name
    elif plat == "windows":
        appdata = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
        localappdata = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
        base_data = localappdata / app_name / "data"
        base_config = appdata / app_name / "config"
        base_cache = localappdata / app_name / "cache"
        base_logs = localappdata / app_name / "logs"
    else:
        # Linux / XDG
        data_home = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (home / ".config"))
        cache_home = Path(os.environ.get("XDG_CACHE_HOME") or (home / ".cache"))
        state_home = Path(os.environ.get("XDG_STATE_HOME") or (home / ".local" / "state"))
        base_data = data_home / "nexus-studio"
        base_config = config_home / "nexus-studio"
        base_cache = cache_home / "nexus-studio"
        base_logs = state_home / "nexus-studio" / "logs"

    return {
        "data": base_data,
        "config": base_config,
        "cache": base_cache,
        "logs": base_logs,
        "backups": base_data / "backups",
    }


def ensure_desktop_dirs(app_name: str = "NexusStudio") -> dict[str, Path]:
    """Ensure standard platform directories exist on disk."""
    dirs = get_desktop_dirs(app_name)
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    return dirs


# Loopback port allocation and security
def find_available_loopback_port(preferred: int = 0) -> int:
    """Find an available port bound strictly to 127.0.0.1."""
    if preferred > 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def is_process_alive(pid: int) -> bool:
    """Check if a process ID is currently running on the system."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            return exit_code.value == 259  # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


_LOCK_BYTE = 1 << 30  # Windows locks a byte past the metadata so readers still work.


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _close_locked(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, _LOCK_BYTE, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    os.close(fd)


class UnsafeWorkspaceError(ValueError):
    """A folder Studio must not take over as a workspace."""

    exit_code = 64  # EX_USAGE; the native shell maps it to a specific screen.


_SYSTEM_FOLDERS = {
    "darwin": ["/System", "/Library", "/usr", "/bin", "/sbin", "/private/etc", "/Applications", "/dev"],
    "linux": ["/usr", "/etc", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev", "/lib", "/opt"],
    "windows": [os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files")],
}


def check_workspace_root(path: str | Path) -> Path:
    """Refuse the home folder or any of its ancestors, system folders and app bundles.

    Studio creates agents/, workflows/, files/ and private state in its workspace,
    so it needs a dedicated folder.
    """
    root = Path(path).resolve()
    system = [Path(p).resolve() for p in _SYSTEM_FOLDERS.get(get_desktop_platform(), [])]
    if (
        Path.home().resolve().is_relative_to(root)
        or any(root.is_relative_to(folder) for folder in system)
        or any(part.endswith(".app") for part in root.parts)
    ):
        raise UnsafeWorkspaceError(f"Choose a dedicated folder for a Studio workspace, not {root}")
    return root


class WorkspaceInUseError(RuntimeError):
    """Another Studio process already serves this workspace."""

    exit_code = 75  # EX_TEMPFAIL; the native shell maps it to a specific screen.


class WorkspaceLock:
    """Exclusive single-writer lock held by the process serving a workspace.

    Uses an OS file lock (flock / msvcrt) that the kernel releases when the
    holder exits or crashes, so stale owners never need PID guessing and no
    unrelated process is ever treated as the owner. The JSON body is
    informational metadata for status displays; trust it only while locked.
    """

    def __init__(self, workspace_path: Union[str, Path], lock_name: str = ".nexus-workspace.lock"):
        self.workspace = Path(workspace_path).resolve()
        self.lock_file = self.workspace / lock_name
        self._fd: int | None = None

    def acquire(
        self,
        port: int,
        token: str = "",
        metadata: dict[str, Any] | None = None,
        wait: float = 0.0,
    ) -> bool:
        """Take the lock, waiting up to ``wait`` seconds for a closing holder."""
        if self._fd is not None:
            return False
        self.workspace.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + wait
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                os.close(fd)
                return False
            time.sleep(0.1)
        info = {
            "pid": os.getpid(),
            "port": port,
            "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest() if token else None,
            "acquired_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "platform": get_desktop_platform(),
            "metadata": metadata or {},
        }
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps(info, indent=2).encode("utf-8"))
        self._fd = fd
        return True

    def release(self) -> bool:
        """Release the lock if this instance holds it. The file is kept on purpose:
        unlinking a lock file lets a waiter lock an orphaned inode."""
        if self._fd is not None:
            _close_locked(self._fd)
            self._fd = None
        return True

    def is_locked(self) -> bool:
        """True while any process, including this one, holds the lock."""
        if self._fd is not None:
            return True
        try:
            fd = os.open(self.lock_file, os.O_RDWR)
        except OSError:
            return False
        if _try_lock(fd):
            _close_locked(fd)
            return False
        os.close(fd)
        return True

    def read(self) -> Optional[dict[str, Any]]:
        """Read current lock metadata if valid."""
        if not self.lock_file.is_file():
            return None
        try:
            return json.loads(self.lock_file.read_text(encoding="utf-8"))
        except Exception:
            return None


def end_owned_process_group() -> None:
    """Kill leftovers in the process group the native shell created for us, ourselves included.

    Only acts when this process leads its own group (the shell spawns the sidecar
    that way), so a terminal session or test runner's group is never touched.
    """
    if os.name == "posix" and os.getpgrp() == os.getpid():
        os.killpg(os.getpid(), signal.SIGKILL)


class DesktopSecurityPolicy:
    """Enforces strict loopback host, origin, and safe external URL boundaries."""

    ALLOWED_ORIGINS = {
        "http://127.0.0.1",
        "http://localhost",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    }

    ALLOWED_EXTERNAL_SCHEMES = {"http", "https", "mailto"}

    @classmethod
    def validate_host_header(cls, host_header: str, expected_port: Optional[int] = None) -> bool:
        """Verify Host header points to 127.0.0.1 or localhost."""
        if not host_header:
            return False
        host = host_header.split(":")[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            return False
        if expected_port is not None and ":" in host_header:
            try:
                port = int(host_header.split(":")[1])
                if port != expected_port:
                    return False
            except ValueError:
                return False
        return True

    @classmethod
    def validate_origin(cls, origin_header: str) -> bool:
        """Validate request Origin header matches allowed desktop schemes."""
        if not origin_header:
            return True  # Same-origin non-CORS requests
        parsed = urlparse(origin_header)
        base = f"{parsed.scheme}://{parsed.hostname}" if parsed.hostname else origin_header
        return base in cls.ALLOWED_ORIGINS or origin_header in cls.ALLOWED_ORIGINS

    @classmethod
    def validate_external_url(cls, target_url: str) -> bool:
        """Validate external URL before opening in system default browser."""
        parsed = urlparse(target_url.strip())
        if parsed.scheme.lower() not in cls.ALLOWED_EXTERNAL_SCHEMES:
            return False
        if not parsed.netloc and parsed.scheme.lower() != "mailto":
            return False
        # Disallow loopback / internal network via external browser opener
        host = (parsed.hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "0.0.0.0"} or host.startswith("192.168.") or host.startswith("10."):
            return False
        return True


class DesktopLaunchCredential:
    """Per-launch secret shared only by the native shell and the backend it owns.

    The shell writes it to the child's standard input, never argv, environment,
    URLs or logs. The backend proves knowledge with an HMAC over a fresh shell
    challenge bound to its workspace and port, so the secret is never sent and a
    proof cannot be relayed to another port. The webview then receives an HttpOnly
    session cookie derived from the same secret.
    """

    COOKIE = "nexus_desktop_session"

    def __init__(self, secret: bytes):
        if len(secret) != 32:
            raise ValueError("Desktop launch credential must be 32 bytes")
        self._secret = secret

    @classmethod
    def generate(cls) -> DesktopLaunchCredential:
        return cls(secrets.token_bytes(32))

    @classmethod
    def read(cls, stream) -> DesktopLaunchCredential:
        """Read one lowercase-hex line from a binary stream without echoing it."""
        line = stream.readline(65)
        if not re.fullmatch(rb"[0-9a-f]{64}\n", line):
            raise ValueError("Invalid desktop launch credential on standard input")
        return cls(bytes.fromhex(line[:64].decode()))

    def line(self) -> bytes:
        return self._secret.hex().encode() + b"\n"

    def _mac(self, *parts: str) -> str:
        return hmac.new(self._secret, "\0".join(parts).encode(), hashlib.sha256).hexdigest()

    def proof(self, challenge: str, workspace: str, port: int) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", challenge):
            raise ValueError("Desktop challenge must be 64 lowercase hex characters")
        return self._mac("nexus-desktop-launch/v1", challenge, workspace, str(port))

    def session(self) -> str:
        return self._mac("nexus-desktop-session/v1")

    def session_ok(self, cookie_header: str | None) -> bool:
        cookies = SimpleCookie()
        try:
            cookies.load(cookie_header or "")
        except CookieError:
            return False
        morsel = cookies.get(self.COOKIE)
        return morsel is not None and hmac.compare_digest(
            morsel.value.encode(), self.session().encode()
        )


def import_workspace(
    source_dir: Union[str, Path],
    target_dir: Union[str, Path],
    backup_first: bool = True,
) -> dict[str, Any]:
    """Import an existing Studio workspace with backup and exclusive-writer check."""
    src = Path(source_dir).resolve()
    dst = Path(target_dir).resolve()

    if not src.is_dir():
        raise FileNotFoundError(f"Source workspace directory not found: {src}")

    if src == dst or src.is_relative_to(dst) or dst.is_relative_to(src):
        raise ValueError("Source and destination workspaces must be separate folders")
    lock = WorkspaceLock(dst)
    if lock.is_locked():
        raise ValueError(f"Destination workspace is locked by another running process (PID {lock.read().get('pid')})")
    if WorkspaceLock(src).is_locked():
        # A copy taken while another Studio writes the source could be inconsistent.
        raise ValueError("Source workspace is open in another Nexus Studio; close it before importing")

    backup_path = None
    if backup_first and dst.is_dir() and any(dst.iterdir()):
        dirs = ensure_desktop_dirs()
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_name = f"backup_{dst.name}_{ts}"
        backup_file = dirs["backups"] / f"{backup_name}.zip"
        shutil.make_archive(str(dirs["backups"] / backup_name), "zip", dst)
        backup_path = str(backup_file)

    dst.mkdir(parents=True, exist_ok=True)

    # Copy files excluding caches and existing locks
    copied_count = 0
    for item in src.iterdir():
        if item.name.startswith(".nexus-workspace.lock") or item.name in {"__pycache__", ".git"}:
            continue
        target_item = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target_item, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target_item)
        copied_count += 1

    return {
        "success": True,
        "source": str(src),
        "target": str(dst),
        "backup": backup_path,
        "copied_items": copied_count,
    }


def check_desktop_prerequisites() -> dict[str, Any]:
    """Audit system prerequisites and external runtimes without invoking unreviewed actions."""
    checks = []

    # 1. Python environment
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    checks.append({
        "name": "python",
        "description": "Python 3.10+ execution environment",
        "status": "available",
        "version": py_ver,
        "required": True,
    })

    # 2. Docker daemon
    docker_bin = shutil.which("docker")
    docker_status = "missing"
    docker_ver = None
    if docker_bin:
        try:
            res = subprocess.run([docker_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                docker_status = "available"
                docker_ver = res.stdout.strip()
            else:
                docker_status = "error"
        except Exception:
            docker_status = "unreachable"

    checks.append({
        "name": "docker",
        "description": "Docker runtime for isolated code and vision workers",
        "status": docker_status,
        "version": docker_ver,
        "required": False,
        "notice": "Required only for Python analysis workers, ImageMagick, and YOLO object detection.",
    })

    # 3. Node.js (optional, for local MCP servers)
    node_bin = shutil.which("node")
    node_status = "missing"
    node_ver = None
    if node_bin:
        try:
            res = subprocess.run([node_bin, "--version"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                node_status = "available"
                node_ver = res.stdout.strip()
        except Exception:
            pass

    checks.append({
        "name": "node",
        "description": "Node.js runtime for local MCP server adapters",
        "status": node_status,
        "version": node_ver,
        "required": False,
    })

    # 4. Git
    git_bin = shutil.which("git")
    git_status = "available" if git_bin else "missing"
    checks.append({
        "name": "git",
        "description": "Git version control CLI",
        "status": git_status,
        "required": False,
    })

    return {
        "platform": get_desktop_platform(),
        "architecture": platform.machine(),
        "os_version": platform.version(),
        "ready": True,
        "checks": checks,
    }


class DesktopBackendRunner:
    """Manages an owned Studio backend child process for the desktop window."""

    def __init__(
        self,
        workspace_dir: Union[str, Path],
        port: int = 0,
        host: str = "127.0.0.1",
    ):
        self.workspace = Path(workspace_dir).resolve()
        self.host = host
        self.port = port or find_available_loopback_port(5003)
        self.token = secrets.token_urlsafe(32)
        self.lock = WorkspaceLock(self.workspace)
        self.process: Optional[subprocess.Popen] = None

    def start(self, timeout_seconds: float = 15.0) -> dict[str, Any]:
        """Spawn the backend child, which owns the workspace lock, and poll readiness."""
        if self.lock.is_locked():
            info = self.lock.read() or {}
            return {
                "success": False,
                "already_running": True,
                "pid": info.get("pid"),
                "port": info.get("port"),
                "error": "Workspace is already locked by a running instance",
            }

        cmd = [
            sys.executable,
            "-m",
            "nexus.cli",
            "studio",
            "--dir",
            str(self.workspace),
            "--port",
            str(self.port),
            "--host",
            self.host,
        ]

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["NEXUS_DESKTOP_MODE"] = "1"
        env["NEXUS_DESKTOP_TOKEN"] = self.token

        self.process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Poll readiness endpoint
        start_time = time.time()
        ready = False
        url = f"http://{self.host}:{self.port}/api/bootstrap"

        import urllib.request
        while time.time() - start_time < timeout_seconds:
            if self.process.poll() is not None:
                _, stderr = self.process.communicate()
                return {
                    "success": False,
                    "already_running": False,
                    "error": f"Backend process terminated immediately: {stderr.strip()}",
                }
            try:
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.2)

        if not ready:
            self.stop()
            return {
                "success": False,
                "already_running": False,
                "error": f"Backend process did not become ready within {timeout_seconds}s",
            }

        return {
            "success": True,
            "pid": self.process.pid,
            "port": self.port,
            "url": f"http://{self.host}:{self.port}",
            "token": self.token,
            "workspace": str(self.workspace),
        }

    def stop(self, timeout_seconds: float = 5.0) -> bool:
        """Terminate child process cleanly and release workspace lock."""
        try:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=timeout_seconds)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        finally:
            self.lock.release()
            self.process = None
        return True

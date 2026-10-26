"""Explicit local PyInstaller packaging; never installs optional build software."""

import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from nexus.core.studio_desktop_profiles import check_profile, profile_requirements

FROZEN_ENTRYPOINT = '''"""Trusted sidecar entry; PDF workers do not bootstrap Studio or provider SDKs."""
import multiprocessing
import sys

multiprocessing.freeze_support()
if sys.argv[1:3] in (["--nexus-worker", "nexus.core.studio_artifact_extract"],
                       ["--nexus-worker", "nexus.core.studio_mcp_supervisor"]):
    import runpy
    from pathlib import Path

    module = sys.argv[2]
    worker = Path(__file__).resolve().parent / "nexus" / "core" / (module.rsplit(".", 1)[1] + ".py")
    sys.argv = [module, *sys.argv[3:]]
    runpy.run_path(str(worker), run_name="__main__")
else:
    from nexus.core.studio_desktop_sidecar import main

    main()
'''


def source_snapshot(repo_root):
    paths = list((repo_root / "nexus").rglob("*.py")) + [repo_root / "pyproject.toml"]
    paths += [p for p in (repo_root / "nexus" / "studio_static").rglob("*") if p.is_file()]
    return {
        p.relative_to(repo_root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
        if p.is_file()
    }


def native_target():
    system = {
        "Darwin": "apple-darwin",
        "Linux": "unknown-linux-gnu",
        "Windows": "pc-windows-msvc",
    }.get(platform.system())
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        platform.machine().lower()
    )
    if not system or not machine:
        raise ValueError("Build the desktop sidecar on a supported native OS/architecture")
    return machine + "-" + system


def tree_sha256(root):
    """Content digest of a bundle folder as the runtime sees it.

    File symlinks are hashed by content and directory symlinks (framework
    aliases) are skipped, matching what Tauri's resource copy installs, so an
    installed app still verifies against the build manifest.
    """
    root = Path(root)
    files = []
    for directory, _, names in os.walk(root):
        files += [Path(directory) / name for name in names]
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def build_bundle(repo_root, output, *, target=None, log_path=None, profile="core"):
    """Build a one-folder backend including this build environment's dependencies.

    One-folder, not one-file: the executable is then the single process leading the
    shell-created process group (descendant cleanup works), starts without
    re-extracting itself, and leaves nothing in the temp folder when killed.
    Staging never replaces an existing bundle until PyInstaller succeeds.
    Optional SDK coverage is recorded rather than implied by a successful build.
    Models, credentials, workspace state and host CLI tools are not collected.
    """
    requirements = profile_requirements(profile)
    profile_check = check_profile(profile)
    if not profile_check["passed"]:
        missing = [key for key, row in profile_check["packages"].items() if not row["imported"]]
        missing += [
            key for key, row in profile_check.get("offline_checks", {}).items() if not row["passed"]
        ]
        raise ValueError("Desktop profile dependencies unavailable: " + ", ".join(missing))
    repo_root, output = Path(repo_root).resolve(), Path(output).resolve()
    target = target or native_target()
    if (
        not isinstance(target, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", target)
        or target != native_target()
    ):
        raise ValueError("PyInstaller bundles must be built on their native target")
    if importlib.util.find_spec("PyInstaller") is None:
        raise ValueError(
            "Install the studio-desktop-build extra in an isolated build environment first"
        )
    static = repo_root / "nexus" / "studio_static"
    for name in ["index.html", "studio.js", "studio.css", "favicon.ico", "vendor/marked.js"]:
        if not (static / name).is_file():
            raise ValueError("Studio frontend assets are incomplete")
    output.mkdir(parents=True, exist_ok=True)
    # Not "nexus-sidecar-<target>": that is the tracked development wrapper file.
    name = "nexus-backend-" + target
    sources = source_snapshot(repo_root)
    assets = {
        p.relative_to(static).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(static.rglob("*"))
        if p.is_file()
    }
    with tempfile.TemporaryDirectory(prefix="nexus-sidecar-build-") as directory:
        work = Path(directory)
        entry = work / "entry.py"
        entry.write_text(FROZEN_ENTRYPOINT)
        command = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onedir",
            "--noconfirm",
            "--name",
            "nexus-sidecar",
            "--distpath",
            str(work / "dist"),
            "--workpath",
            str(work / "build"),
            "--specpath",
            str(work),
            "--paths",
            str(repo_root),
            "--collect-submodules",
            "nexus",
            "--add-data",
            str(static) + os.pathsep + "nexus/studio_static",
            "--add-data",
            str(repo_root / "nexus" / "core" / "studio_artifact_extract.py")
            + os.pathsep
            + "nexus/core",
            "--add-data",
            str(repo_root / "nexus" / "core" / "studio_mcp_supervisor.py")
            + os.pathsep
            + "nexus/core",
            "--copy-metadata",
            "nexus-arc",
            str(entry),
        ]
        for distribution, module in requirements.items():
            command[3:3] = ["--hidden-import", module, "--recursive-copy-metadata", distribution]
        if profile in {"connected", "full"}:
            # Bedrock uses runtime-selected model descriptions; SQLAlchemy loads
            # dialects dynamically. Their package hooks/data must survive freezing.
            command[3:3] = [
                "--collect-data",
                "botocore",
                "--collect-submodules",
                "sqlalchemy.dialects",
            ]
        if profile == "full":
            command[3:3] = [
                "--collect-data",
                "whisper",
                "--collect-submodules",
                "tiktoken_ext",
                "--collect-all",
                "kafka",
                "--collect-all",
                "eventlet",
                "--collect-all",
                "dns",
                "--collect-submodules",
                "engineio.async_drivers",
                "--collect-submodules",
                "socketio.async_drivers",
            ]
        log_path = Path(log_path) if log_path else output / "build.log"
        with log_path.open("w") as log:
            subprocess.run(
                command,
                cwd=work,
                env={**os.environ, "PYINSTALLER_CONFIG_DIR": str(work / "cache")},
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        if source_snapshot(repo_root) != sources or any(
            not (static / name).is_file()
            or hashlib.sha256((static / name).read_bytes()).hexdigest() != digest
            for name, digest in assets.items()
        ):
            raise ValueError(
                "Build inputs changed during packaging; rebuild from a stable checkout"
            )
        executable = "nexus-sidecar" + (".exe" if os.name == "nt" else "")
        built = work / "dist" / "nexus-sidecar" / executable
        if not built.is_file() or not os.access(built, os.X_OK):
            raise ValueError("PyInstaller did not produce an executable sidecar")
        # Tauri resource copies dereference file links (same content ships) but drop
        # directory links, so those paths would be missing from the installed app.
        links = [
            os.path.relpath(os.path.join(directory, name), built.parent)
            for directory, dirs, _ in os.walk(built.parent)
            for name in dirs
            if os.path.islink(os.path.join(directory, name))
        ]
        if links:
            raise ValueError(
                "Bundle contains directory symlinks (" + ", ".join(sorted(links)[:5]) + "); a "
                "macOS framework Python needs a non-framework interpreter (uv venv --managed-python)"
            )
        empty = work / "self-check"
        empty.mkdir()
        environment = {"PATH": "", "HOME": str(empty)}
        if os.name == "nt":
            environment.update(SystemRoot=os.environ["SystemRoot"], USERPROFILE=str(empty))
        result = subprocess.run(
            [str(built), "--nexus-bundle-info"],
            cwd=empty,
            env=environment,
            capture_output=True,
            timeout=30,
            check=True,
        )
        runtime = json.loads(result.stdout)
        if (
            runtime.get("format") != "nexus-sidecar-info/v1"
            or not runtime.get("frozen")
            or not runtime.get("frontend_present")
        ):
            raise ValueError("Bundled runtime or frontend self-check failed")
        result = subprocess.run(
            [str(built), "--nexus-bundle-check", profile],
            cwd=empty,
            env=environment,
            capture_output=True,
            timeout=60,
            check=False,
        )
        try:
            frozen_profile = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise ValueError("Bundled dependency profile returned an invalid report") from None
        if (
            result.returncode != 0
            or not isinstance(frozen_profile, dict)
            or frozen_profile.get("format") != "nexus-desktop-profile/v1"
            or frozen_profile.get("profile") != profile
            or frozen_profile.get("passed") is not True
            or set(frozen_profile.get("packages", {})) != set(requirements)
            or any(row.get("imported") is not True for row in frozen_profile["packages"].values())
        ):
            failures = []
            if isinstance(frozen_profile, dict):
                for label in ("packages", "offline_checks"):
                    rows = frozen_profile.get(label, {})
                    if isinstance(rows, dict):
                        for name, row in rows.items():
                            if isinstance(row, dict) and row.get("error_type"):
                                failures.append(name + ": " + row["error_type"])
            raise ValueError(
                "Bundled dependency profile self-check failed"
                + (": " + ", ".join(failures) if failures else "")
            )
        staged, destination = output / (name + ".staging"), output / name
        previous = output / (name + ".previous")
        for leftover in (staged, previous):
            shutil.rmtree(leftover, ignore_errors=True)
        shutil.copytree(built.parent, staged, symlinks=True)
        if destination.exists():
            destination.rename(previous)
        staged.rename(destination)
        shutil.rmtree(previous, ignore_errors=True)
    files = [p for p in destination.rglob("*") if p.is_file() and not p.is_symlink()]
    manifest = {
        "format": "nexus-desktop-bundle/v2",
        "target": target,
        "layout": "onedir",
        "directory": name,
        "executable": name + "/" + executable,
        "sha256": hashlib.sha256((destination / executable).read_bytes()).hexdigest(),
        "tree_sha256": tree_sha256(destination),
        "file_count": len(files),
        "size_bytes": sum(p.stat().st_size for p in files),
        "python": platform.python_version(),
        "pyinstaller": importlib.metadata.version("pyinstaller"),
        "build_environment_distributions": sorted(
            {(d.metadata["Name"], d.version) for d in importlib.metadata.distributions()}
        ),
        "runtime": runtime,
        "dependency_profile": frozen_profile,
        "source_sha256": sources,
        "sdk_coverage": "Installed build-environment packages only; not all-feature or live certification",
        "external_prerequisites": [
            "Configured provider accounts and host CLI subscriptions",
            "Docker/Node where selected tools require them",
            "Explicitly installed local models",
        ],
        "frontend_sha256": assets,
    }
    manifest_path = output / (name + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return {
        "executable": str(destination / executable),
        "directory": str(destination),
        "manifest": str(manifest_path),
        "target": target,
    }

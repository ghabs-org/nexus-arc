"""Bundling keeps source execution compatible and limits worker dispatch."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from nexus.core import studio_desktop_bundle as bundle
from nexus.core import studio_desktop_profiles as profiles
from nexus.core import studio_desktop_sidecar as sidecar


@pytest.mark.parametrize("profile", ["connected", "full"])
def test_profile_failures_are_reported_without_exception_values(monkeypatch, profile):
    monkeypatch.setattr(profiles.importlib.metadata, "version", lambda _: "1.0")

    def fail_import(module):
        raise RuntimeError("sensitive environment value")

    monkeypatch.setattr(profiles.importlib, "import_module", fail_import)
    result = profiles.check_profile(profile)
    assert not result["passed"] and len(result["packages"]) == len(
        profiles.profile_requirements(profile)
    )
    assert "sensitive" not in json.dumps(result)
    assert all(row["error_type"] == "RuntimeError" for row in result["packages"].values())


def test_missing_packaged_data_fails_full_profile_even_with_all_imports(monkeypatch):
    monkeypatch.setattr(profiles.importlib.metadata, "version", lambda _: "1.0")
    monkeypatch.setattr(profiles.importlib, "import_module", lambda _: object())

    def missing_data():
        raise FileNotFoundError("private-build-path")

    monkeypatch.setattr(profiles, "full_offline_checks", lambda: {"speech_data": missing_data})
    result = profiles.check_profile("full")
    assert not result["passed"]
    assert all(row["imported"] for row in result["packages"].values())
    assert result["offline_checks"]["speech_data"] == {
        "passed": False,
        "error_type": "FileNotFoundError",
    }
    assert "private-build-path" not in json.dumps(result)


def test_missing_packaged_data_blocks_compilation_before_output(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bundle,
        "check_profile",
        lambda _: {
            "passed": False,
            "packages": {},
            "offline_checks": {"speech_data": {"passed": False}},
        },
    )
    compiler = Mock(side_effect=AssertionError("Must not compile missing data"))
    monkeypatch.setattr(bundle.subprocess, "run", compiler)
    with pytest.raises(ValueError, match="dependencies unavailable: speech_data"):
        bundle.build_bundle(tmp_path, tmp_path / "out", profile="full")
    assert not compiler.called and not (tmp_path / "out").exists()


def test_missing_connected_dependencies_fail_before_output_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        bundle,
        "check_profile",
        lambda _: {"passed": False, "packages": {"mcp": {"imported": False}}},
    )
    compiler = Mock(side_effect=AssertionError("Must not compile incomplete profile"))
    monkeypatch.setattr(bundle.subprocess, "run", compiler)
    with pytest.raises(ValueError, match="dependencies unavailable: mcp"):
        bundle.build_bundle(tmp_path, tmp_path / "output", profile="connected")
    assert not (tmp_path / "output").exists() and not compiler.called


def test_unknown_profile_cannot_dispatch_an_arbitrary_import(monkeypatch):
    imported = Mock(side_effect=AssertionError("Must not import arbitrary modules"))
    monkeypatch.setattr(profiles.importlib, "import_module", imported)
    monkeypatch.setattr(sys, "argv", ["sidecar", "--nexus-bundle-check", "os"])
    with pytest.raises(SystemExit, match="Unknown desktop dependency profile"):
        sidecar.main()
    assert not imported.called


def test_frozen_import_failure_preserves_existing_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.importlib.util, "find_spec", lambda _: object())
    source = tmp_path / "source"
    static = source / "nexus" / "studio_static"
    for name in ["index.html", "studio.js", "studio.css", "favicon.ico", "vendor/marked.js"]:
        path = static / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    output = tmp_path / "output"
    output.mkdir()
    name = "nexus-sidecar" + (".exe" if sys.platform == "win32" else "")
    existing = output / ("nexus-backend-" + bundle.native_target()) / name
    existing.parent.mkdir()
    existing.write_bytes(b"previous release")

    def run(command, **kwargs):
        if "PyInstaller" in command:
            # One-folder layout: dist/nexus-sidecar/<executable> plus _internal/.
            folder = Path(command[command.index("--distpath") + 1]) / "nexus-sidecar"
            (folder / "_internal").mkdir(parents=True)
            executable = folder / name
            executable.write_bytes(b"new release")
            executable.chmod(0o755)
            return subprocess.CompletedProcess(command, 0)
        if "--nexus-bundle-info" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {"format": "nexus-sidecar-info/v1", "frozen": True, "frontend_present": True}
                ).encode(),
            )
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=json.dumps(
                {
                    "format": "nexus-desktop-profile/v1",
                    "profile": "core",
                    "passed": False,
                    "packages": {"pypdf": {"imported": False, "error_type": "ModuleNotFoundError"}},
                }
            ).encode(),
            stderr=b"private build path must not be exposed",
        )

    monkeypatch.setattr(bundle.subprocess, "run", run)
    with pytest.raises(ValueError, match="self-check failed: pypdf: ModuleNotFoundError") as error:
        bundle.build_bundle(source, output)
    assert "private build path" not in str(error.value)
    assert existing.read_bytes() == b"previous release"


@pytest.mark.parametrize("module", list(sidecar.WORKERS))
def test_frozen_workers_reexecute_the_bundled_runtime(monkeypatch, module):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert sidecar.worker_command(module, "input", executable="/bundle/app") == [
        "/bundle/app",
        "--nexus-worker",
        module,
        "input",
    ]


def test_source_commands_keep_module_and_isolated_finance_behavior(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    pdf = sidecar.worker_command("nexus.core.studio_artifact_extract", "2", executable="python")
    assert pdf[:2] == ["python", "-I"]
    assert Path(pdf[2]).name == "studio_artifact_extract.py" and pdf[3:] == ["2"]
    command = sidecar.worker_command(
        "nexus.core.studio_finance_worker", "ABC", "1mo", "history", executable="python"
    )
    assert (
        command[:2] == ["python", "-I"]
        and Path(command[2]).name == "studio_finance_worker.py"
        and command[3:] == ["ABC", "1mo", "history"]
    )


def test_arbitrary_worker_names_never_import_or_execute(monkeypatch):
    imported = Mock(side_effect=AssertionError("Unknown worker must not import"))
    monkeypatch.setattr(sidecar.importlib, "import_module", imported)
    monkeypatch.setattr(sys, "argv", ["sidecar", "--nexus-worker", "os"])
    with pytest.raises(SystemExit, match="Unsupported internal"):
        sidecar.main()
    with pytest.raises(ValueError):
        sidecar.worker_command("os")
    assert not imported.called


def test_failed_build_does_not_overwrite_existing_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(
        bundle.subprocess, "run", Mock(side_effect=subprocess.CalledProcessError(1, ["builder"]))
    )
    static = tmp_path / "source" / "nexus" / "studio_static"
    for name in ["index.html", "studio.js", "studio.css", "favicon.ico", "vendor/marked.js"]:
        path = static / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    output = tmp_path / "output"
    output.mkdir()
    target = bundle.native_target()
    existing = output / ("nexus-sidecar-" + target + (".exe" if sys.platform == "win32" else ""))
    existing.write_bytes(b"prior-artifact")
    with pytest.raises(subprocess.CalledProcessError):
        bundle.build_bundle(tmp_path / "source", output)
    assert existing.read_bytes() == b"prior-artifact"


def test_cross_target_build_is_rejected_before_output_mutation(tmp_path):
    with pytest.raises(ValueError, match="native target"):
        bundle.build_bundle(tmp_path, tmp_path / "out", target="foreign-unknown-target")
    assert not (tmp_path / "out").exists()


def test_concurrent_input_changes_cannot_replace_a_prior_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.importlib.util, "find_spec", lambda _: object())
    source = tmp_path / "source"
    static = source / "nexus" / "studio_static"
    for name in ["index.html", "studio.js", "studio.css", "favicon.ico", "vendor/marked.js"]:
        path = static / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")

    def change_inputs(*args, **kwargs):
        (static / "new-asset.js").write_text("changed during compilation")

    monkeypatch.setattr(bundle.subprocess, "run", change_inputs)
    output = tmp_path / "output"
    output.mkdir()
    existing = output / (
        "nexus-sidecar-" + bundle.native_target() + (".exe" if sys.platform == "win32" else "")
    )
    existing.write_bytes(b"prior-artifact")
    with pytest.raises(ValueError, match="inputs changed"):
        bundle.build_bundle(source, output)
    assert existing.read_bytes() == b"prior-artifact"


def test_bundle_folder_never_replaces_the_tracked_development_wrapper(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.importlib.util, "find_spec", lambda _: object())
    monkeypatch.setattr(bundle.importlib.metadata, "version", lambda _: "0+test")
    source = tmp_path / "source"
    static = source / "nexus" / "studio_static"
    for name in ["index.html", "studio.js", "studio.css", "favicon.ico", "vendor/marked.js"]:
        path = static / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    output = tmp_path / "binaries"
    output.mkdir()
    wrapper = output / ("nexus-sidecar-" + bundle.native_target())
    wrapper.write_text("#!/bin/sh\nexec development-python\n")
    executable = "nexus-sidecar" + (".exe" if sys.platform == "win32" else "")

    def run(command, **kwargs):
        if "PyInstaller" in command:
            folder = Path(command[command.index("--distpath") + 1]) / "nexus-sidecar"
            (folder / "_internal").mkdir(parents=True)
            (folder / executable).write_bytes(b"frozen")
            (folder / executable).chmod(0o755)
            return subprocess.CompletedProcess(command, 0)
        report = (
            {"format": "nexus-sidecar-info/v1", "frozen": True, "frontend_present": True}
            if "--nexus-bundle-info" in command
            else {
                "format": "nexus-desktop-profile/v1",
                "profile": "core",
                "passed": True,
                "packages": {},
            }
        )
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(report).encode())

    monkeypatch.setattr(bundle.subprocess, "run", run)
    result = bundle.build_bundle(source, output)
    assert wrapper.read_text().startswith("#!/bin/sh")
    assert Path(result["executable"]).read_bytes() == b"frozen"
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["layout"] == "onedir"
    assert manifest["tree_sha256"] == bundle.tree_sha256(result["directory"])


def test_symlinked_bundle_is_rejected_before_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(bundle.importlib.util, "find_spec", lambda _: object())
    source = tmp_path / "source"
    static = source / "nexus" / "studio_static"
    for name in ["index.html", "studio.js", "studio.css", "favicon.ico", "vendor/marked.js"]:
        path = static / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    output = tmp_path / "output"
    existing = output / ("nexus-backend-" + bundle.native_target()) / "nexus-sidecar"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"previous release")

    def run(command, **kwargs):
        assert "PyInstaller" in command, "self-check must not run on a linked bundle"
        folder = Path(command[command.index("--distpath") + 1]) / "nexus-sidecar"
        (folder / "_internal" / "Python.framework" / "Versions" / "3.13").mkdir(parents=True)
        executable = folder / ("nexus-sidecar" + (".exe" if sys.platform == "win32" else ""))
        executable.write_bytes(b"frozen")
        executable.chmod(0o755)
        (folder / "_internal" / "Python").symlink_to("Python.framework/Versions/3.13")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(bundle.subprocess, "run", run)
    with pytest.raises(ValueError, match="directory symlinks.*non-framework interpreter"):
        bundle.build_bundle(source, output)
    assert existing.read_bytes() == b"previous release"

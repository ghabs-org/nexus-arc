"""Release preparation preserves user state and rejects unsuitable identities."""

import json
import plistlib
import runpy
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from nexus.core import studio_desktop_release as release
from nexus.core.studio import StudioService
from nexus.core.studio_backup import verify_snapshot
from nexus.core.studio_desktop import WorkspaceLock


def application(tmp_path):
    app = tmp_path / "Nexus Studio.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Info.plist").write_text("fixture")
    (app / "Contents/MacOS/studio").write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")
    return app


def test_backup_prepares_verified_snapshot_and_preserves_previous_app(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    service = StudioService(workspace)
    service.save_definition(
        "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Keep"}
    )
    service.close()
    app = application(tmp_path)
    monkeypatch.setattr(release.sys, "platform", "darwin")

    def copy(command, **kwargs):
        assert command[0] == "/usr/bin/ditto"
        contender = WorkspaceLock(workspace)
        assert not contender.acquire(0)
        shutil.copytree(command[1], command[2])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release.subprocess, "run", copy)
    result = release.prepare_update(workspace, tmp_path / "backups/before-update", app)
    assert result["success"]
    manifest = verify_snapshot(Path(result["backup"]) / "workspace")
    assert any(row["path"].endswith("reviewer.yaml") for row in manifest["files"])
    assert (Path(result["previous_app"]) / "Contents/MacOS/studio").read_bytes() == (
        app / "Contents/MacOS/studio"
    ).read_bytes()
    assert not WorkspaceLock(workspace).is_locked()


def test_live_workspace_and_existing_destination_never_replaced(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    app = application(tmp_path)
    lock = WorkspaceLock(workspace)
    assert lock.acquire(0)
    monkeypatch.setattr(release.sys, "platform", "darwin")
    try:
        with pytest.raises(ValueError, match="Close the workspace"):
            release.prepare_update(workspace, tmp_path / "backup", app)
        assert not (tmp_path / "backup").exists()
    finally:
        lock.release()
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "canary").write_text("Keep")
    with pytest.raises(ValueError):
        release.prepare_update(workspace, existing, app)
    assert (existing / "canary").read_text() == "Keep"


def test_identity_probe_accepts_only_developer_id_application(monkeypatch):
    output = "\n".join(
        [
            "1) " + "A" * 40 + ' "Apple Distribution: Team (TEAM)"',
            "2) " + "B" * 40 + ' "Developer ID Application: Team (TEAM)"',
            "3) " + "C" * 40 + ' "Apple Development: Person (TEAM)"',
        ]
    )
    monkeypatch.setattr(
        release.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=output)
    )
    assert release.developer_identities() == [("B" * 40, "Developer ID Application: Team (TEAM)")]


def test_inside_out_signing_includes_native_libraries_under_resources(tmp_path, monkeypatch):
    app = application(tmp_path)
    library = app / "Contents/Resources/sidecar/_internal/worker.so"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"\xcf\xfa\xed\xfe" + b"library")
    (library.parent / "data.json").write_text("{}")
    monkeypatch.setattr(
        release, "developer_identities", lambda: [("A" * 40, "Developer ID Application: Team")]
    )
    commands = []
    monkeypatch.setattr(release, "_run", lambda command: commands.append(command))
    result = release.sign_application(app, "A" * 40)
    signed = [command[-1] for command in commands if "--sign" in command]
    assert signed[0] == str(library) and signed[-1] == str(app)
    assert result["native_files_signed"] == 2
    assert all("--timestamp" in command for command in commands if "--sign" in command)
    assert not any("--deep" in command for command in commands if "--sign" in command)
    assert commands[-1][1:4] == ["--verify", "--deep", "--strict"]
    with pytest.raises(ValueError, match="Developer ID Application"):
        release.sign_application(app, "Apple Distribution: Team")


def test_executable_memory_entitlement_is_limited_to_python_host(tmp_path, monkeypatch):
    app = application(tmp_path)
    sidecar = app / "Contents/Resources/sidecar/nexus-sidecar"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b"\xcf\xfa\xed\xfe" + b"fixture")
    monkeypatch.setattr(
        release, "developer_identities", lambda: [("A" * 40, "Developer ID Application: Team")]
    )
    entitled = []

    def run(command):
        if "--entitlements" in command:
            settings = plistlib.loads(
                Path(command[command.index("--entitlements") + 1]).read_bytes()
            )
            entitled.append((command[-1], settings))

    monkeypatch.setattr(release, "_run", run)
    release.sign_application(app, "A" * 40)
    assert entitled == [
        (str(sidecar), {"com.apple.security.cs.allow-unsigned-executable-memory": True})
    ]


@pytest.mark.parametrize(
    "returncode,output", [(-9, b""), (0, b'{"profile":"full","passed":false}'), (0, b"invalid")]
)
def test_hardened_profile_failure_blocks_release_before_notarization(
    tmp_path, monkeypatch, returncode, output
):
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, returncode, stdout=output),
    )
    with pytest.raises(ValueError, match="no notarization was started"):
        release.check_signed_profile(tmp_path)


def test_notarization_requires_accepted_result_and_redacts_rejected_response(tmp_path, monkeypatch):
    artifact = tmp_path / "app.zip"
    artifact.write_bytes(b"fixture")
    commands = []

    def run(command):
        commands.append(command)
        return json.dumps({"id": "submission", "status": "Accepted"}).encode()

    monkeypatch.setattr(release, "_run", run)
    assert release.notarize(artifact, "local-keychain-profile")["status"] == "Accepted"
    assert "--keychain-profile" in commands[0] and "--password" not in commands[0]
    monkeypatch.setattr(release, "_run", lambda _: b'{"status":"Invalid","message":"PRIVATE"}')
    with pytest.raises(ValueError, match="did not accept") as error:
        release.notarize(artifact, "local-keychain-profile")
    assert "PRIVATE" not in str(error.value)


def test_full_profile_generated_config_advertises_native_sdk_minimum(tmp_path, monkeypatch):
    from nexus.core import studio_desktop_bundle

    script = runpy.run_path("apps/studio-desktop/build_desktop.py")
    output = tmp_path / "sidecar"

    def build(*args, **kwargs):
        output.mkdir()
        return {
            "directory": str(output / "backend"),
            "executable": "fixture",
            "manifest": "fixture",
        }

    monkeypatch.setattr(studio_desktop_bundle, "build_bundle", build)
    monkeypatch.setattr(release.sys, "platform", "darwin")
    monkeypatch.setattr(
        release.sys,
        "argv",
        ["build_desktop.py", "--sidecar-only", "--profile", "full", "--output", str(output)],
    )
    assert script["main"]() == 0
    config = json.loads((output / "tauri.bundle.conf.json").read_text())
    assert config["bundle"]["macOS"] == {"signingIdentity": "-", "minimumSystemVersion": "14.0"}


@pytest.mark.parametrize("extra_root", [None, "._Nexus Studio.app", "../escape"])
def test_update_archive_has_one_root_and_excludes_macos_metadata(tmp_path, monkeypatch, extra_root):
    app = application(tmp_path)
    destination = tmp_path / "update.tar.gz"

    def tar(command):
        assert "--no-mac-metadata" in command
        with tarfile.open(destination, "w:gz") as archive:
            archive.add(app, arcname=app.name)
            if extra_root:
                info = tarfile.TarInfo(extra_root)
                archive.addfile(info)

    monkeypatch.setattr(release, "_run", tar)
    if extra_root:
        with pytest.raises(ValueError, match="one application root"):
            release.build_update_archive(app, destination)
    else:
        release.build_update_archive(app, destination)
    with pytest.raises(ValueError, match="new updater archive"):
        release.build_update_archive(app, destination)

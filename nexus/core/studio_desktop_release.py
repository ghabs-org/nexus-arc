"""Stopped-workspace update preparation and explicit macOS signing operations."""

import hashlib
import json
import os
import plistlib
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from nexus.core.studio_backup import create_snapshot, verify_snapshot
from nexus.core.studio_desktop import WorkspaceLock, check_workspace_root


def prepare_update(workspace, destination, application):
    """Keep a verified private workspace backup and the previous application."""
    workspace = check_workspace_root(workspace)
    destination = Path(destination).absolute()
    application = Path(application).resolve(strict=True)
    if (
        sys.platform != "darwin"
        or application.suffix != ".app"
        or not (application / "Contents/Info.plist").is_file()
        or destination.exists()
        or destination.is_symlink()
        or destination.resolve().is_relative_to(workspace)
        or destination.resolve().is_relative_to(application)
    ):
        raise ValueError("Choose a new backup folder outside the workspace and macOS application")
    lock = WorkspaceLock(workspace)
    if not lock.acquire(0, metadata={"operation": "prepare-update"}):
        raise ValueError("Close the workspace before preparing an update")
    try:
        destination.mkdir(parents=True, mode=0o700)
        create_snapshot(workspace, destination / "workspace", quiesced=True)
        verify_snapshot(destination / "workspace")
        previous = destination / application.name
        result = subprocess.run(
            ["/usr/bin/ditto", str(application), str(previous)], capture_output=True
        )
        if result.returncode:
            raise ValueError("Could not preserve the previous application")
        return {"success": True, "backup": str(destination), "previous_app": str(previous)}
    finally:
        lock.release()


def developer_identities():
    """Return public certificate metadata only, never key material."""
    result = subprocess.run(
        ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True,
        text=True,
        check=True,
    )
    return re.findall(r'\b([0-9A-F]{40}) "(Developer ID Application: [^"\n]+)"', result.stdout)


def _run(command):
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise ValueError(f"Release operation failed: {Path(command[0]).name}")
    return result.stdout


def sign_application(application, identity):
    """Sign every nested Mach-O inside-out, then the outer application."""
    application = Path(application).resolve(strict=True)
    identities = developer_identities()
    selected = next((digest for digest, name in identities if identity in {digest, name}), None)
    if selected is None:
        raise ValueError("Choose an installed Developer ID Application identity")
    if application.suffix != ".app" or not (application / "Contents/Info.plist").is_file():
        raise ValueError("Choose a built macOS application")
    # All native libraries use the same team. Do not disable library validation.
    magic = {
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    }
    native = []
    for path in application.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in magic:
                    native.append(path)
    # LLVM/Numba uses executable memory without MAP_JIT. Only the Python host
    # needs this exception; dylibs inherit it and the native shell keeps none.
    with tempfile.NamedTemporaryFile(suffix=".plist") as entitlements:
        entitlements.write(
            plistlib.dumps({"com.apple.security.cs.allow-unsigned-executable-memory": True})
        )
        entitlements.flush()
        for path in sorted(native, key=lambda item: (-len(item.parts), str(item))):
            extra = (
                ["--entitlements", entitlements.name]
                if path == application / "Contents/Resources/sidecar/nexus-sidecar"
                else []
            )
            _run(
                [
                    "/usr/bin/codesign",
                    "--force",
                    "--options",
                    "runtime",
                    "--timestamp",
                    "--sign",
                    selected,
                    *extra,
                    str(path),
                ]
            )
    _run(
        [
            "/usr/bin/codesign",
            "--force",
            "--options",
            "runtime",
            "--timestamp",
            "--sign",
            selected,
            str(application),
        ]
    )
    _run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(application)])
    return {"native_files_signed": len(native), "identity_sha1": selected}


def check_signed_profile(application):
    """Reject a hardened full-SDK runtime failure before sending it to Apple."""
    executable = Path(application) / "Contents/Resources/sidecar/nexus-sidecar"
    with tempfile.TemporaryDirectory(prefix="nexus-signed-profile-") as home:
        result = subprocess.run(
            [str(executable), "--nexus-bundle-check", "full"],
            capture_output=True,
            timeout=120,
            env={"HOME": home, "TMPDIR": home, "PATH": os.defpath},
        )
    try:
        report = json.loads(result.stdout)
    except (ValueError, UnicodeError):
        report = {}
    if (
        result.returncode
        or not isinstance(report, dict)
        or report.get("passed") is not True
        or report.get("profile") != "full"
        or not isinstance(report.get("packages"), dict)
        or not report["packages"]
    ):
        raise ValueError("Signed full SDK runtime failed; no notarization was started")
    return {"passed": True, "profile": "full", "sdk_imports": len(report["packages"])}


def notarize(path, profile):
    """Submit only an explicitly selected artifact using a Keychain profile."""
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("Choose a notarization Keychain profile")
    raw = _run(
        [
            "/usr/bin/xcrun",
            "notarytool",
            "submit",
            str(Path(path).resolve(strict=True)),
            "--keychain-profile",
            profile,
            "--wait",
            "--output-format",
            "json",
        ]
    )
    result = json.loads(raw)
    if result.get("status") != "Accepted":
        raise ValueError("Apple did not accept the notarization submission")
    return {"id": result["id"], "status": result["status"]}


def build_update_archive(application, destination):
    """Use Tauri's single app-root layout without macOS AppleDouble entries."""
    application = Path(application).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Choose a new updater archive path")
    _run(
        [
            "/usr/bin/tar",
            "--no-mac-metadata",
            "-czf",
            str(destination),
            "-C",
            str(application.parent),
            application.name,
        ]
    )
    with tarfile.open(destination, "r|gz") as archive:
        count = 0
        for member in archive:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] != application.name
            ):
                raise ValueError("Updater archive must contain exactly one application root")
            count += 1
        if not count:
            raise ValueError("Updater archive is empty")


def release_macos(application, output, identity, profile, updater_key, tauri_cli, notes=None):
    """Produce a notarized DMG and version-bound updater archive; never publish."""
    application = Path(application).resolve(strict=True)
    output = Path(output).absolute()
    key = Path(updater_key).resolve(strict=True)
    cli = Path(tauri_cli).resolve(strict=True)
    if sys.platform != "darwin" or output.exists() or output.is_symlink():
        raise ValueError("Choose a new release output folder on macOS")
    if not developer_identities():
        raise ValueError("Install a Developer ID Application certificate before release")
    metadata = plistlib.loads((application / "Contents/Info.plist").read_bytes())
    version = metadata["CFBundleShortVersionString"]
    if metadata.get("CFBundleIdentifier") != "com.nexusarc.studio" or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version
    ):
        raise ValueError("Choose a versioned Nexus Studio application")
    executable = application / "Contents/MacOS" / metadata["CFBundleExecutable"]
    if _run(["/usr/bin/lipo", "-archs", str(executable)]).strip() != b"arm64":
        raise ValueError("This release consumer supports Apple Silicon builds only")
    output.mkdir(parents=True, mode=0o700)
    app = output / application.name
    _run(["/usr/bin/ditto", str(application), str(app)])
    signed = sign_application(app, identity)
    profile_check = check_signed_profile(app)
    archive = output / "notarization.zip"
    _run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)])
    app_notary = notarize(archive, profile)
    _run(["/usr/bin/xcrun", "stapler", "staple", str(app)])
    _run(["/usr/bin/xcrun", "stapler", "validate", str(app)])
    _run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)])
    archive.unlink()
    image_source = output / "disk-image"
    image_source.mkdir()
    _run(["/usr/bin/ditto", str(app), str(image_source / app.name)])
    (image_source / "Applications").symlink_to("/Applications")
    dmg = output / f"Nexus-Studio-{version}-apple-silicon.dmg"
    _run(
        [
            "/usr/bin/hdiutil",
            "create",
            "-volname",
            "Nexus Studio",
            "-srcfolder",
            str(image_source),
            "-format",
            "UDZO",
            str(dmg),
        ]
    )
    _run(
        ["/usr/bin/codesign", "--force", "--timestamp", "--sign", signed["identity_sha1"], str(dmg)]
    )
    dmg_notary = notarize(dmg, profile)
    _run(["/usr/bin/xcrun", "stapler", "staple", str(dmg)])
    _run(["/usr/bin/xcrun", "stapler", "validate", str(dmg)])
    _run(["/usr/bin/hdiutil", "verify", str(dmg)])
    update = output / f"Nexus-Studio-{version}-aarch64.app.tar.gz"
    build_update_archive(app, update)
    _run(
        [
            "node",
            str(cli),
            "signer",
            "sign",
            "--private-key-path",
            str(key),
            "--password",
            "",
            "--app-version",
            version,
            str(update),
        ]
    )
    signature = Path(str(update) + ".sig")
    feed = {
        "version": version,
        "notes": notes or f"Nexus Studio {version}.",
        "platforms": {
            "darwin-aarch64": {
                "signature": signature.read_text().strip(),
                "url": f"https://github.com/ghabs-org/nexus-arc/releases/download/studio-v{version}/{update.name}",
            }
        },
    }
    (output / "nexus-studio-latest.json").write_text(json.dumps(feed, indent=2) + "\n")
    artifacts = [dmg, update, signature, output / "nexus-studio-latest.json"]
    digests = {}
    for artifact in artifacts:
        digest = hashlib.sha256()
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digests[artifact.name] = digest.hexdigest()
    (output / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in digests.items())
    )
    result = {
        "format": "nexus-studio-macos-release/v1",
        "version": version,
        "signing": signed,
        "signed_profile": profile_check,
        "app_notarization": app_notary,
        "dmg_notarization": dmg_notary,
        "sha256": digests,
        "published": False,
    }
    (output / "release.json").write_text(json.dumps(result, indent=2) + "\n")
    return result

# Portable desktop backend packaging

The native build path now produces a real one-folder Python backend, rather
than silently staging a shell script pointing at the build machine's interpreter.
The macOS Apple Silicon **core prototype** runs after relocation without a source
checkout or host Python on PATH, and as part of an installed, unsigned
`Nexus Studio.app` (see *Install into the native app*). This does not close
milestone 9: SDK coverage, signing and other OS builds remain separate gates.

One folder (`--onedir`), not one file: measured on the same inputs, one-file runs
a bootloader plus a Python child (so the backend does not lead the shell's process
group and descendant cleanup no-ops), re-extracts itself on every launch (~3 s to
ready) and leaves its extraction folder behind when killed. One folder is a single
process, ready in ~0.4–1.5 s, leaves nothing behind, and is PyInstaller's
recommended layout inside macOS app bundles.

## Build explicitly

Use an isolated build environment and install Nexus plus the capabilities intended
for that build. For the verified core/PDF prototype:

```sh
uv venv /tmp/nexus-desktop-build --managed-python --python 3.13
uv pip install --python /tmp/nexus-desktop-build/bin/python -e '.[studio-desktop-build,documents]'
/tmp/nexus-desktop-build/bin/python apps/studio-desktop/build_desktop.py --sidecar-only --output /tmp/nexus-sidecar
```

The build helper never installs dependencies itself. `studio-desktop-build` pins
PyInstaller 6.22.3. To include API or connector SDKs, install their existing Nexus
extras in the build environment **before** bundling. Missing SDKs are not fetched
at runtime. Models, host CLI subscriptions, account credentials, Docker and Node
retain their explicit external prerequisites. Never silently replace a selected
subscription with an API provider.

Without `--output`, the helper stages `nexus-backend-<target>/` (the executable
`nexus-sidecar` plus `_internal/`), its manifest and `tauri.bundle.conf.json`
under `apps/studio-desktop/src-tauri/binaries/` (all git-ignored; the tracked
`nexus-sidecar-<target>` development wrapper file is never replaced). `npm run build`
runs packaging, then `tauri build --config` with that generated file; a packaging
failure does not fall back to a development wrapper. `--development-wrapper` is an explicit opt-in to the previous source-only
workflow and is not a standalone distribution.

PyInstaller builds on its native OS/architecture. Cross-target requests fail before
staging. Build macOS/Linux/Windows artifacts on their corresponding build hosts;
Linux ABI compatibility and each target's installation still need verification.

## What is included and verified

The executable contains its Python interpreter, Nexus module graph, public Studio
assets (including vendor resources and favicon), Nexus package metadata, and the
collected dependencies available in that build environment. Source/data paths
resolve inside the bundle folder. The source checkout and build environment
are not runtime inputs.

`--nexus-bundle-info` reports frozen-runtime identity, frontend presence and optional
SDK module presence without reading credentials, loading models or calling APIs.
The builder runs this self-check from an empty folder with an empty PATH before
staging. Its manifest (`nexus-desktop-bundle/v2`) records target, layout, executable
SHA-256, a content tree hash of the folder (`tree_sha256`; file symlinks by content,
framework directory aliases skipped, so an installed copy verifies), Python/PyInstaller
versions, build-environment distribution versions, actual runtime package probes,
and source/asset hashes. Concurrent source/input changes reject the build before
replacing an older staged artifact. A failed compiler or self-check also retains
that previous artifact.

Internal PDF, Whisper and finance subprocess commands re-execute the bundled
runtime through a fixed module allowlist. Source execution keeps its existing
module commands and isolated finance worker. There is no arbitrary Python-code
or module execution interface. Docker analysis continues to use Python inside
its existing restricted container.

The verified prototype includes PDF support. OpenAI, Anthropic, Slack, MCP,
SQLAlchemy, Whisper, Bedrock and MongoDB SDK presence is explicitly false in its
runtime report. It is **not an all-feature release**. Ship the intended SDK extras
and run their actual protocol/behavior acceptance before promoting that claim.

## Actual relocated acceptance

Build first, then run the existing framework example from the development test
environment (the child process itself uses no host Python):

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_desktop_bundle.py --executable /tmp/nexus-sidecar/nexus-backend-aarch64-apple-darwin/nexus-sidecar
NEXUS_ACCEPTANCE_SIDECAR=/tmp/nexus-sidecar/nexus-backend-aarch64-apple-darwin/nexus-sidecar PYTHONPATH=. python -m pytest tests/studio_desktop_bundle_acceptance.py -q -o addopts=''
```

The consumer copies the bundle folder and renames its executable into a directory containing spaces,
clears PATH/Python environment variables, uses an empty HOME, verifies the native
HMAC/session path, saves an agent YAML file, serves frontend/vendor/favicon assets,
uploads and previews a PDF through the frozen subprocess, and shuts down on
parent-pipe EOF. No live provider, integration account or user workspace is used.

## Install into the native app

```sh
/tmp/nexus-desktop-build/bin/python apps/studio-desktop/build_desktop.py --sidecar-only --output /tmp/nexus-sidecar
cd apps/studio-desktop
CI=true npx --yes @tauri-apps/cli@2.12.4 build --config /tmp/nexus-sidecar/tauri.bundle.conf.json --bundles app,dmg
```

Build with a **non-framework** Python (`uv venv --managed-python`). Homebrew and
python.org interpreters are macOS frameworks; their `Python.framework` directory
aliases are dropped by Tauri's resource copy, so the builder refuses any bundle
with directory symlinks. File symlinks (PyInstaller's top-level links to SDK
dylibs) install as their content and verify. `CI=true` skips the DMG script's
Finder AppleScript styling, which would otherwise ask for automation access.

The generated configuration ships the target's folder as the `sidecar` resource
(`Nexus Studio.app/Contents/Resources/sidecar/`), empties `externalBin` so the
development wrapper is never packaged, and on macOS requests an ad-hoc signature
(`signingIdentity: "-"`) until a Developer ID exists. Release shells spawn only
the bundled backend and show the local "spawn" error if it is missing; only debug
builds fall back to the wrapper. Verify an installed copy with
`tree_sha256(<app>/Contents/Resources/sidecar) == manifest["tree_sha256"]`.

Verified on macOS arm64, installed from the DMG under a path with spaces outside
the repo and launched with an empty environment (`PATH=/usr/bin:/bin`):

| Build | App / DMG | Backend files | Ready |
|---|---|---|---|
| core | 48.1 / 22.1 MiB | 161 | 0.8–1.4 s |
| connected (17 SDKs) | 147.2 / 77.9 MiB | 2,556 | ~3.4 s |

For both: DMG checksum valid, installed sidecar matches the manifest, strict deep
signature verification passes, backend is one process leading its own group,
nothing open under the repo, lock held by the backend, anonymous API 401. The
connected profile acceptance passes against the installed backend. The user
confirmed the full UI and closed the window; window close, SIGTERM and SIGKILL to
the shell leave no processes and a free lock. A Studio run whose agent CLI (a
synthetic `claude` that starts its own child) is active is cleaned up both on the
shell's stop and when the shell vanishes; the run is recorded `interrupted` first.

## Signing notes (no Developer ID yet)

- `codesign --deep` seals Mach-O files under `Contents/Resources` as data and does
  not sign them, so the sidecar keeps PyInstaller's ad-hoc signature without
  hardened runtime. Notarization requires every Mach-O to carry a Developer ID
  signature with hardened runtime and a timestamp: sign the bundle folder's
  binaries inside-out before `tauri build`, then let Tauri sign the app.
- Under hardened runtime, ad-hoc signatures fail library validation ("different
  Team IDs") because ad-hoc has no team. With only that check relaxed, the backend
  and its frozen PDF worker ran fully, so a single Developer ID team is expected
  to need no entitlements. That remains unproven until a certificate exists.
- Gatekeeper rejects a quarantined unsigned app as "damaged" on Apple Silicon; the
  ad-hoc build can be allowed explicitly in System Settings. Neither is a
  substitute for signed and notarized releases.

Next: `full` profile installed build, Linux AppImage/deb and Windows installers on
their own hosts, Developer ID signing/notarization, signature-verified updates.

Primary packaging reference: [PyInstaller usage](https://pyinstaller.org/en/stable/usage.html)
and [runtime data handling](https://pyinstaller.org/en/stable/spec-files.html).

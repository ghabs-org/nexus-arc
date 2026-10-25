# Nexus Studio — next-agent delivery plan

Updated 25 October 2026. Continue in `nexus-arc`, reading `AGENTS.md` and the
current implementation plan first. Inspect the working tree before edits and
preserve work from other agents. This handoff identifies remaining delivery
capabilities; do not rebuild features already present.

## What is already present

- Web/browser favicon serving and package inclusion now reuse the existing Nexus
  mark. Passive local speech readiness reports missing model/runtime dependencies
  before recording; no model was downloaded and no API fallback was introduced.
  Actual voice/model/device/VM acceptance remains open.

- Extended Slack gateway code now includes outbound artifacts, rich replies,
  feedback/cancellation, workflow threads and Socket Mode. Its checkpoint reports
  local acceptance. Independently check the changed invocation paths before
  promoting broader live claims; this handoff is not fresh protocol certification.
- Catalog records now report 27 supported entries reviewed and four exclusions.
  Reuse their evidence rather than repeating catalog setup work by default.
- OpenClaw's saved Postman simulation now executes eight logged HTTP cases plus
  unreachable transport, with zero automatic retries. The former dataset blocker
  is resolved. Real gateway channels and VM checks remain separate.
- Native desktop startup now retains child ownership, drains output, rejects
  shutdown races and keeps a local failure screen. It authenticates its backend:
  a stdin-delivered per-launch secret, an HMAC challenge-response bound to
  workspace/port, and an HttpOnly session cookie gating local `/api/` routes. A
  macOS development-build window drill loaded the full UI through that session.
  See `STUDIO-DESKTOP-SESSION-CHECKPOINT.json`. This is not standalone OS acceptance.

## 1. Finish real voice and operational acceptance before desktop release

Reuse Studio speech profiles, existing jobs, artifacts, recording UI and runtime
configuration. Identify installed local Whisper/Piper models and explicitly
configured API accounts. Show missing prerequisites without downloading models,
installing hooks or switching to paid providers automatically.

Acceptance:

- Actual configured local transcription and speech generation complete through
  normal jobs; API profiles are checked separately when an account is available.
- Browser microphone permission, recording, language/voice, speed, stop and opt-in
  playback work on the selected physical devices.
- Temporary audio is removed after completion/failure/cancellation. Restart and
  shared-user access retain the existing safeguards.
- Personal HTTPS VM install/restart/upgrade/restore and selected integration checks
  have real evidence. Prepare deployable artifacts locally first; do not infer
  SSH access, credentials or live account authorization from fixture success.

Keep unavailable account/device/VM checks visible as pending. Do not mark them
passed using deterministic provider substitutes.

## 2. Authenticated native startup and workspace ownership

Parent lifetime follow-up: `STUDIO-DESKTOP-PARENT-CHECKPOINT.json` records normal
backend shutdown on parent EOF and abnormal synthetic shell termination, plus
unbuffered stdin handling. Preserve this fix while wiring workspace ownership.

Extend the existing native ownership/readiness code in
`apps/studio-desktop/src-tauri/src/backend.rs` and `src/main.rs`. Reuse Nexus
authentication/CSRF and existing desktop lifecycle helpers; do not create a
parallel account system.

Done on 25 October (macOS development build): the shell verifies the instance it
launched before opening its webview, secrets stay out of URLs/assets/UI/logs, and
impostor loopback services cannot satisfy readiness. Windows WebView2 and Linux
WebKitGTK cookie behaviour still needs the same drill.

Also done on 25 October, jointly by two agents (macOS development build; see
`STUDIO-DESKTOP-OWNERSHIP-CHECKPOINT.json` and the parent checkpoint above):
native and terminal launches share a kernel-owned single-writer lock (second
launch exits 75; no PID guessing); window close closes Studio cleanly, then
group cleanup; a SIGKILLed or SIGTERMed shell no longer orphans its sidecar; a
user-driven window close and the in-use cases were drilled on a real window.

Remaining acceptance:

- Close/crash cleanup with active external descendants (terminal agents, MCP
  stdio servers; Docker containers sit outside the process group).
- The same lock, close and crash drills on Windows (msvcrt lock, job objects)
  and Linux, and in installed builds.

Retain current fail-closed navigation, parent-pipe monitoring and process-group cleanup.

## 3. Portable sidecar packaging

Core/PDF prototype now builds and runs after relocation without host Python.
See `STUDIO-DESKTOP-BUNDLE.md` and `STUDIO-DESKTOP-BUNDLE-CHECKPOINT.json`.
Default release packaging creates the executable first; development wrappers are
explicit opt-ins. The full SDK profile, native application installation and
bootloader/descendant cleanup matrix remain open.

Extend the portable builder with a reviewed SDK-complete profile and integrate its
output into installed native packages. Checked-in development wrappers are not
release artifacts. Preserve the current Studio frontend and framework.

**Installed app (25 October, done for macOS arm64 core, unsigned — Claude Code
session).** The builder now uses PyInstaller's one-folder layout. Measured on the
same inputs, one-file ran a bootloader plus a Python child, so the backend did not
lead the shell's process group and its descendant cleanup no-oped. It also took
~3 s to start every launch and left its extraction folder behind when killed;
one-folder fixes all three. Output is `nexus-backend-<target>/` (never the tracked
`nexus-sidecar-<target>` wrapper file) with a content `tree_sha256`. `tauri build
--config <generated tauri.bundle.conf.json>` ships it as the `sidecar` resource
and drops the development wrapper; release shells spawn only the bundle. The
installed `.app`, launched from an empty environment outside the repo, passed:
UI confirmed by the user, window close, SIGTERM and SIGKILL with no leftovers and
a free lock. See `STUDIO-DESKTOP-INSTALLED-CHECKPOINT.json`. The profile gate below
is preserved. Open: SDK-complete installed build, Python.framework layout for
signing (Tauri flattens its links), DMG/Linux/Windows packages.

**macOS DMG releases (25 October, done — Claude Code session; see
`STUDIO-DESKTOP-MACOS-RELEASE-CHECKPOINT.json`).** Build with a non-framework
Python (`uv venv --managed-python`). The builder now refuses bundles with directory
symlinks, which Tauri drops. Releases are ad-hoc signed (`signingIdentity "-"`),
and DMGs build with `CI=true`. Core (48 MiB app / 22 MiB DMG) and connected
(147 / 78 MiB) apps were installed from their DMGs and passed: manifest match,
strict signature verification, connected acceptance, user-confirmed UI and window
close, SIGKILL/SIGTERM. A live agent-CLI descendant (synthetic `claude` with its
own child) is cleaned up on stop and when the shell vanishes, with the run
recorded `interrupted` first. For Developer ID, sign the backend folder's Mach-O
files inside-out before `tauri build`; `--deep` leaves them unsigned. Open: full
profile installed build, MCP stdio and Docker descendants, Developer ID and
notarization, Linux/Windows.

**SDK profile gate (25 October, verified on macOS arm64).** The framework builder
now accepts `profile="connected"`; the native build consumer exposes `--profile connected`.
This profile requires 17 SDK imports before compilation and from the frozen runtime
before staging. `studio-desktop-connected` installs the explicit build dependencies.
Preserve these checks while changing bundle layout; source/import success is not
account or service acceptance. The relocated connected build passed all 17 imports,
authenticated startup, agent save and PDF-worker dispatch without host Python.
See `STUDIO-DESKTOP-PROFILES-CHECKPOINT.json` and `STUDIO-DESKTOP-PROFILES.md`.
The remaining optional SDKs and their invocation paths still need coverage.
One repeat lost PDF extraction metadata; later full and direct worker runs passed.
The checkpoint retains this intermittent failure. Diagnose it before closing
portable PDF stability; no extraction guard or timeout was relaxed.
PDF failures now carry safe categories for timeout, dependency, unreadable file,
launch, signal, worker exit and malformed output. Seventy affected checks and
three rebuilt, relocated connected-backend runs pass (valid/damaged/encrypted
PDFs through authenticated HTTP). The historical cause remains open; see
`STUDIO-PDF-PREVIEW-CHECKPOINT.json`.

Acceptance:

- The installed app works without the source checkout, `.venv`, build-machine
  absolute paths or separately installed Python.
- Server frontend assets, vendored libraries, package metadata and required
  Python dependencies are bundled and resolved from the installed app.
- Optional Docker, host CLI subscriptions, models and credentials retain explicit
  readiness/billing disclosures. Never silently replace subscriptions with API use.
- The shell's small startup page remains local until verified readiness; the full
  UI is served by the owned backend.
- Reproducible build instructions and artifact manifests identify each OS/architecture.

**Full SDK profile coordination (25 October, Codex in progress).** Codex is adding
`--profile full` and `studio-desktop-full` for the remaining finance/cloud/protocol/
channel/terminal and local Whisper SDKs. Offline speech filter/tokenizer assets,
Bedrock/S3 descriptions and gRPC messages must also pass before staging. Builds
go to `/private/tmp/nexus-full-sidecar` using a managed non-framework interpreter;
framework Python was correctly rejected by the new link guard. Models, account and service acceptance
remain separate. Connected/native commands now also use the generated Tauri
release resource configuration. Preserve the existing installed-core work.

**Native workspaces (26 October, done for macOS — Claude Code session; see
`STUDIO-DESKTOP-WORKSPACES-CHECKPOINT.json`).** The shell's Workspace menu opens
a folder in place, imports a copy into the app's `workspaces/` folder (through
`--nexus-desktop import`, routed to the existing `nexus desktop` CLI) and reveals
the folder. The choice is remembered only after a verified launch. Native launches
refuse home, system and app-bundle folders (exit 64). Import refuses nested or
open sources. The user drilled the menu on installed 1.0.2. Open: in-app
pre-import backup restore, Windows/Linux.

**Descendants and macOS lifecycle (25 October, done — Claude Code session; see
`STUDIO-DESKTOP-LIFECYCLE-CHECKPOINT.json`).** Stdio MCP servers run in their own
session (MCP SDK). Studio's normal close terminates them, but after a backend
crash only servers that exit on stdin EOF stop. Docker sandbox containers outlive
a dead backend by at most their timeout. Fixed a bug where any native startup
error was hidden by a self-SIGKILL. A workspace from a newer release now exits 65
and the native app shows a dedicated screen (user-confirmed). The shell grace is
12 s, above the backend's 10 s bound. Upgrade 1.0.0→1.0.1, rollback and uninstall
(data kept) passed with real installed apps. Open in section 4: the native
workspace chooser/import flow and real schema migrations.

## 4. Workspace selection and installed OS lifecycle

Use existing workspace import/backup services and native platform directory APIs.
Expose a clear open/import flow without weakening allowed-folder or ownership rules.

Acceptance:

- Users can select/open a workspace, safely import an existing one and inspect
  where agents, workflows, documents and artifacts are stored.
- Existing workspaces survive app upgrades and rollback; pre-import backups can
  be restored using the normal recovery flow.
- Clean macOS, Linux and Windows installations pass launch, quit/relaunch,
  permissions, upgrade and uninstall-data-retention checks.
- Signing/notarization, release packaging and update behavior have actual build
  evidence. A debug binary or unit-tested directory helper is not an installer.

Keep milestone 9 last, after earlier implementation and operational gates.

## 5. Optional follow-on feature already in the roadmap

After required delivery gates, consider the direct TypeSafe decision provider.
The roadmap records this as new optional interoperability, outside the frozen
baseline. Inspect current decision adapters first. Reuse typed schemas,
credentials, validation and error/recovery handling; verify the provider's current
contract before adding a native adapter. Support explicit API selection/billing
and no automatic fallback. Do not change frozen parity scope or acceptance counts.

## Delivery rules

Each slice needs reusable framework code, an example consumer, relevant tests,
compatibility notes and a checkpoint naming the exact proof. Preserve mobile UI,
existing CLI subscriptions and private-by-default data handling. Run required
checks with zero unexplained skips; report optional live prerequisites separately.

The inventory's 135 accepted flags are recorded claims. They do not substitute
for the separate desktop release gate or actual live/device/VM evidence. Keep
whole-plan completion false until every required gate has matching proof.

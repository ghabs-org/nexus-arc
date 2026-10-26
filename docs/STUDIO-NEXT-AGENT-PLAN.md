# Nexus Studio — next-agent delivery plan

**Distribution rule — user request, 26 October 2026:** rebuild or create a DMG
only after an explicit user request. Routine implementation must leave existing
DMGs and their recorded checksums unchanged.

Updated 26 October 2026. Continue in `nexus-arc`, reading `AGENTS.md` and the
current implementation plan first. Inspect the working tree before edits and
preserve work from other agents. This handoff identifies remaining delivery
capabilities; do not rebuild features already present.

## What is already present

- Web/browser favicon serving and package inclusion now reuse the existing Nexus
  mark. Passive local speech readiness reports missing model/runtime dependencies
  before recording without downloading models or introducing API fallback.
  A later user-approved local Piper/Whisper roundtrip is now verified; physical
  devices, API accounts, native Windows/Linux and personal VM checks remain open.

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

Local voice follow-up (26 October): real Piper and Whisper jobs now generate and
transcribe a synthetic English sentence on this Mac. Private audio artifacts and
temporary cleanup pass; no microphone or API client was used. Model/runtime
preparation was explicitly approved and both model hashes verified. A discovered
unconditional POSIX import was fixed; native Windows/Linux inference is still
unverified. All 59 affected regressions plus the real-model acceptance pass without
skips. See `STUDIO-LOCAL-SPEECH-CHECKPOINT.json` and the repeatable local speech
example. No running workspace was activated and no installer/DMG rebuilt.

Linux service recovery follow-up (26 October): SIGTERM previously terminated
Studio without invoking service cleanup. Main-thread controller and worker
launches now request cooperative stop, preserve repeated-stop handling during
cleanup and restore the previous signal policy. Stops during initialization
close before operational dispatch. File/SQLite restart and inactive restore
retain approvals, artifact revisions, pinned citations and run identity. Scoped
Mac and isolated Linux acceptance are recorded in
`STUDIO-SERVER-RECOVERY-CHECKPOINT.json`. No running workspace or installer was
changed; actual personal HTTPS VM, accounts and systemd deployment remain open.

Acceptance:

- Local Mac transcription/generation through normal jobs is verified above.
  Repeat on deployment targets; check API profiles separately with a selected
  account when available.
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

MCP follow-up (26 October): one-shot direct requests previously survived close;
pooled close took about nine seconds. Direct calls now reuse existing execution
ownership and pooled cleanup starts immediately. Source/frozen POSIX checks stop
resistant server/child groups without replay, for normal stop and launcher death.
Beta 2 includes the fix; see `STUDIO-MCP-SHUTDOWN-CHECKPOINT.json`.

Docker follow-up (26 October, source runtime): a retained stdin lifetime channel
now stops the container namespace, including a detached SIGTERM-resistant child,
on close, owner death or Docker-client/group termination. Cleanup starts before
executor joins and rejects late dispatch/publication. Explicit context selection
also overrides ambient Docker host/TLS flags. All 126 required regressions and
14 actual local Docker/MCP checks pass without skips. See
`STUDIO-DOCKER-SHUTDOWN-CHECKPOINT.json`. No DMG was rebuilt; this fix is not in
existing installers. Recorded release checksums were retained; the two DMG files
are currently absent from `releases/`, so their hashes were not reverified.

Container recovery follow-up (26 October, source runtime): stopped creation now
precedes one acknowledged-ID start; uncertain creates are never replayed. Private
host journals survive owner death/restart. Explicit bounded recovery skips active
owners, other hosts and changed routes; it removes only verified name/label/ID ownership. Unknown
creates remain pending when absent. Backup/restore excludes cleanup authority;
Deployment checks expose record counts without Docker calls. See
`STUDIO-CONTAINER-RECOVERY-CHECKPOINT.json` and `STUDIO-DOCKER-SHUTDOWN.md`.
All 165 affected regressions and 13 actual local Docker/data checks pass with
zero skips. That earlier Python-only run explicitly deselected its chart/image
workflow; the full-worker follow-up below now closes that local prerequisite.
No installer was rebuilt. Real remote/daemon-restart and OS acceptance stay open.

Cleanup UI follow-up (26 October): owners can now choose **Retry container
cleanup** in **Workspace tools → Deployment checks**. The action reuses saved
recovery records, current Nexus permissions and CSRF. It rejects arbitrary worker
settings, prevents duplicate dispatch within the server process and keeps every
unresolved outcome visible. The phone-width layout fits without horizontal
scrolling. No DMG was rebuilt. See
`STUDIO-CONTAINER-CLEANUP-UI-CHECKPOINT.json` for scoped acceptance.

Full data-worker follow-up (26 October): the existing image recipe now has fresh
acceptance after the lifecycle/recovery changes. All nine actual Docker checks
pass, without skips or deselections, including the six-operation workflow and
private artifact provenance. JQ, chart PNG export and ImageMagick are verified on
the separately built local arm64 image. No framework fix, runtime activation or
DMG rebuild was needed. Frozen image/package/source evidence is in
`STUDIO-FULL-ANALYSIS-WORKER-CHECKPOINT.json`. This closes the earlier optional
full-worker prerequisite; remote, vision/model, device/VM and installed OS gates
retain their own scope.

Docker route follow-up (26 October): local fault injection reproduced a context
changing after the final routing check but before start. Image inspection, create,
start and cleanup now share a fixed connection: a resolved host for the default
context or a private Docker-exported named-context snapshot. Recovery pins its
connection too. Original journal hashes/formats remain compatible. Actual local
named/default-selection races, lifecycle and full data tools pass; real CLI
TLS/SSH configuration-copy checks retain transport settings and daemon headers
without copying registry logins. See `STUDIO-DOCKER-ROUTE-CHECKPOINT.json`.
No runtime was activated and no installer/DMG rebuilt.

Filtering-proxy recovery follow-up (26 October): the hosted proxy previously
denied the analysis cleanup lookup and forgot ownership after failed removal.
The shared framework policy now allows one exact tracked analysis lookup,
scopes upstream queries to its full ID, canonicalizes name/short-ID operations,
and retains bounded deletion tombstones. Retired entries evict before live
owners. Real proxy/daemon execution, stopped recovery, failed-removal retry,
name reuse and restart uncertainty pass. The standalone launcher remains
stdlib-only; Compose mounts its shared policy. See
`STUDIO-PROXY-RECOVERY-CHECKPOINT.json`. No user workspace, VM or installer changed.

Remaining acceptance:

- Durable authenticated ownership across filtering-proxy restart remains open.
  Unknown/evicted ownership now fails closed and requires operator review; it
  never produces false absence or silently adopts a container by name.
- Remote/SSH Docker connections, actual daemon restart/disconnection and
  frozen/installed acceptance. Local context reconfiguration, unreachable
  transport and delayed-create recovery now have source proof. Changes outside
  Docker context selection (for example SSH aliases or DNS) retain their own scope.
- MCP host descendants daemonized outside the SDK-owned group. Ordinary CLI/MCP
  descendants have scoped macOS proof.
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
PDF startup follow-up (26 October, source plus disposable core backend): direct
investigation reproduced a valid PDF failing because an unrelated provider import
failed before the extractor ran. PDF dispatch now uses its trusted isolated script;
frozen packaging routes that invocation before importing Studio/provider SDKs.
The same limits and JSON/page protocol remain. All 124 affected regressions pass,
and relocated authenticated repeated/concurrent text previews, page bounds,
damaged/encrypted errors and shutdown pass. No installer/DMG was rebuilt. See
`STUDIO-PDF-ISOLATION-CHECKPOINT.json`. This closes the confirmed startup coupling
failure path, not the unclassified historical event. Connected/full and installed
OS acceptance for the new entrypoint remain separate.

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

**Full SDK profile (26 October, macOS arm64 backend verified).** `--profile full`
and `studio-desktop-full` now cover 44 SDK imports and three fixed offline checks
for speech data, cloud descriptions and gRPC messages. The clean managed-Python
build passes after Tauri-style file copying and relocation, including protected
startup, agent save, UI/PDF handling and shutdown. Kafka schema resources and
Eventlet hub/DNS discovery needed explicit collection rules. A native dependency
version snapshot contains 146 pinned packages. Connected/full build commands now
use the generated Tauri resource configuration. See
`STUDIO-DESKTOP-FULL-SDK-CHECKPOINT.json` and `STUDIO-DESKTOP-FULL-SDK.md`.

Keep full-profile installed app/signing and every live model/service/device/VM
check separate. These are Python packaging/import/data checks, not whole-feature
invocation certification. The historical intermittent PDF cause remains open.

**Friend-test full DMG (26 October).** A full SDK Apple Silicon DMG is now built
and verified, with its actual macOS 14 minimum declared. The app copied from the
DMG passes payload/signature checks, installed backend acceptance and actual native
first-run/Conversations/Quick Build/close checks. It is ad-hoc signed and not
notarized. The ignored `releases/` folder holds the shareable image, testing guide
and checksum. See `STUDIO-DESKTOP-FRIEND-DMG-CHECKPOINT.json`. Keep clean downloaded
quarantine acceptance on the friend's Mac, live integrations/models and signed
release delivery open.

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

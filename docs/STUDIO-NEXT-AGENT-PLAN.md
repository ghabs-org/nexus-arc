# Nexus Studio — next-agent delivery plan

**Distribution rule — user request, 26 October 2026:** rebuild or create a DMG
only after an explicit user request. Routine implementation must leave existing
DMGs and their recorded checksums unchanged.

Updated 27 October 2026. Continue in `nexus-arc`, reading `AGENTS.md` and the
current implementation plan first. Inspect the working tree before edits and
preserve work from other agents. This handoff identifies remaining delivery
capabilities; do not rebuild features already present.

## Current handoff — authoritative status

The full plan is not complete. The frozen inventory records 135 locally accepted
flags, including milestones 5 and 6; these are not a deployment/desktop readiness
percentage. Milestones 10–12 also have scoped local delivery. Preserve their
implementations and verify the remaining target environments rather than rebuilding
them. Older dated notes below are history, not additional missing implementations.

### Recently completed

| Slice | Current evidence | Remaining boundary |
| --- | --- | --- |
| Local Docker restart/lost replies | [2 real-engine checks](STUDIO-LOCAL-RECOVERY-TRANSPORT-CHECKPOINT.json) | Personal VM repetition; daemon/power-loss |
| PostgreSQL logical restore | [47 native/regression checks](STUDIO-POSTGRES-RESTORE-CHECKPOINT.json) | Actual VM/account-store recovery and deployment-specific physical database recovery |
| GCS/Azure storage SDKs | [44 SDK/regression checks](STUDIO-CLOUD-BACKUP-SDK-CHECKPOINT.json) | Real cloud IAM/retention/history; remaining timeout/retry scenarios |
| TLS certificate lifecycle | [49 real-engine/regression checks](STUDIO-TLS-LIFECYCLE-CHECKPOINT.json) | Production provisioning, automatic renewal, per-leaf revocation and established sessions |
| Linux speech and detached-child fix | [74 focused/real checks](STUDIO-SPEECH-LIFECYCLE-CHECKPOINT.json) | Non-Linux containment, installed applications, devices and VM |
| Direct TypeSafe decisions | [Provider and integration evidence](STUDIO-TYPESAFE-DECISION-CHECKPOINT.json) | Selected real account acceptance |

Counts overlap and must not be summed into a full-suite total. The Linux speech
fix changes source behavior: cancellation, shutdown, timeout and owner death stop
owned detached children. Its real Piper/Whisper roundtrip also passes. The tiny
model's name-recognition error remains documented. This fix is not deployed to
the VM or included in the existing DMG.

Native macOS authentication, workspace locking, bundled Python and full-SDK
tester installation already have proof. The macOS arm64 beta is ad-hoc signed,
not notarized; latest source fixes are not packaged. Final desktop release and
Linux/Windows installed-platform acceptance remain open. See
[tester evidence](STUDIO-DESKTOP-FRIEND-DMG-CHECKPOINT.json).

### Remaining work, in delivery order

1. **Local work while VM access is deferred.** Reproduce and close macOS speech
   child containment without weakening the Linux fix; finish Azure timeout/retry
   failure cases with actual SDKs; investigate the retained historical PDF stability
   gap. Exit: matching source/real-process or SDK evidence, focused regressions and
   explicit remaining platform limits. Do not rebuild a DMG.
2. **Selected accounts and physical devices.** Check configured integration
   permissions/authentication, API voice/embedding quality and billing paths;
   verify real phone/tablet microphone, playback, touch and accessibility. Exit:
   account/device results identify exactly what ran; unavailable prerequisites stay
   pending. Local HTTP/SDK fixtures and emulated widths are already available.
3. **Deferred personal VM operations.** When the user can interact again, prepare
   a reviewed candidate containing later source fixes, retain backup/inactive
   restore/rollback, then verify signed-in execution, controlled worker/proxy
   interruption and actual account-store/full-host restoration. Existing selected
   VM upgrade/worker evidence remains valid for its recorded version, not later
   code. Do not contact or modify the VM while the deferral remains active.
4. **Final evidence reconciliation.** Retain explicit exclusions and compatibility
   fixtures, check source/release/report hashes and update scoped acceptance.
   Production-specific certificate renewal/revocation and physical SQL/daemon
   recovery require their own chosen deployment procedures; fixture success does
   not certify them. Do not turn optional new-provider or broader-source semantics
   into silent additions to the frozen target.
5. **Milestone 9 last.** Finish installed Linux/Windows authentication, ownership,
   lifecycle and workspace flows; clean downloaded testing on another Mac;
   Developer ID/notarization, Windows signing and verified updates. Reuse the
   implemented shell/server/UI and packaging. Create a new DMG only on explicit
   request, then test that exact new artifact. Existing beta proof is not proof
   for a later source revision.

Completion requires both frozen scoped evidence and the remaining required
delivery gates. The desktop completion flags are reopened to match the explicit
pending release checks; no frozen feature acceptance flag is promoted or removed.

## What is already present

- Web/browser favicon serving and package inclusion now reuse the existing Nexus
  mark. Passive local speech readiness reports missing model/runtime dependencies
  before recording without downloading models or introducing API fallback.
  A later user-approved local Piper/Whisper roundtrip is now verified; physical
  devices, API accounts, installed Windows/Linux and personal VM checks remain open.
  Linux-container inference and child ownership have the later evidence above.

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

Private proxy-state follow-up (26 October): optional proxy-only SQLite receipts
now retain cleanup authority across restart/SIGKILL. Service-owned private storage,
exclusive writer locking and daemon/project binding protect the receipt set.
Restored grants allow only lookup/inspect/delete; the same upstream connection
verifies identity before forwarding each request. Storage errors fence creation
and execution. Legacy memory-only behavior and no-adoption enablement remain
compatible. Source regressions and actual local Docker drills are recorded in
`STUDIO-PROXY-STATE-CHECKPOINT.json`. No running workspace or installer changed.

Linux private-mount follow-up (26 October): framework/proxy recovery now
has actual Linux/arm64 mount-isolation evidence. Same-UID proxy and controller
containers have separate private authority; a writable controller's same-path
forgery does not alter proxy receipts, and a private-volume injection is denied.
Graceful and forced proxy restart retain cleanup-only access. The probe exposed ignored SIGTERM at Linux PID 1; cooperative shutdown now
closes the listener and ledger normally. All 54 native Linux storage/policy
checks, 231 affected regressions and 11 actual Docker drills pass without skips.
See `STUDIO-LINUX-PROXY-CHECKPOINT.json`. This uses local Docker named volumes;
no personal VM change, app build or DMG rebuild was performed.

SSH transport follow-up (26 October): a real local Linux/arm64 key-only SSH
gateway now exercises existing named-context snapshots through the filtering
proxy. The controller has neither Docker socket; the gateway receives only the
filtered socket. A forced disconnect stops the acknowledged running analysis
before reconnect, preserves pending cleanup while unreachable, and resolves it
after reconnect without another create/start. One actual transport drill and
161 focused regressions pass without skips. Disposable keys, volumes, containers
and the internal network are removed. See `STUDIO-SSH-PROXY-CHECKPOINT.json`.
Framework behavior and persisted formats are unchanged; no app or DMG rebuilt.

Docker TLS follow-up (26 October): the same isolated harness now verifies real
mutual TLS through the filtering proxy. A named Docker context retains trusted CA
and client credentials through the existing private snapshot. Missing/untrusted
clients and an untrusted server/wrong hostname are rejected. Active connection
loss stops the worker before reconnect; pending cleanup resolves without replay.
SSH and TLS pass the shared real-service gate, with 161 focused regressions and
zero skips. See `STUDIO-TLS-PROXY-CHECKPOINT.json`. No framework runtime change,
personal VM mutation or app/DMG rebuild was needed.

Host preflight follow-up (26 October): `nexus studio-host-check` now checks an
explicit Studio/proxy pair through the pinned Docker route. It reads selected
mount/state metadata and a fixed in-proxy stat probe, never environment, command
arguments, logs, ledger contents or service mutations. Reports redact paths/names
and distinguish failed from unavailable. Host canonical paths and inode ancestry
check aliases, parent/child exposure and hard-linked sockets; actual proxy UID,
socket access and private-store ownership/modes are checked separately. All 75
focused regressions, six real POSIX metadata cases and two Linux CLI/example
drills pass without skips. Docker Desktop correctly leaves host-source checks
unavailable. See `STUDIO-HOST-CHECKS-CHECKPOINT.json` and the personal-VM guide.
Run it on the actual daemon host to close that preflight; no VM or installer
changed. A successful preflight is not full deployment acceptance.

Linux MCP follow-up (26 October): real acceptance reproduced a double-forked
daemon surviving SDK group cleanup. Each supported Linux stdio session now has
an isolated child-subreaper owner with pidfd signalling and checked parentage.
It reaps only direct/adopted children after EOF/termination, including blocked
stdin. Normal close and owner SIGKILL stop escaped daemons in both session modes;
unrelated processes survive and calls are not replayed. All 46 host regressions,
40 Linux checks and two macOS source compatibility cases pass without skips.
The thin example supports `--detached`. Trusted frozen-worker dispatch/data
collection is prepared, but no app/DMG was built. See
`STUDIO-LINUX-MCP-CHECKPOINT.json`. Unsupported Linux logs its legacy SDK-only
policy; direct supervisor SIGKILL and stronger hostile-code containment stay open.

Personal VM discovery follow-up (26 October): the user supplied an SSH target and
completed its Tailscale identity check. Read-only live checks now verify the
public HTTPS shell, login routing, six anonymous private-route denials, cacheable
public assets and browser isolation. All 15 anonymous checks pass. A signed-in
account/session check remains pending. At discovery, the deployed image lacked recent recovery
modules and the optional private proxy store was not configured. The approved
follow-up below now records the deployed candidate and verified private store.

The live inspection also exposed a host-check reporting defect: a missing optional
store suppressed otherwise verifiable raw-socket isolation. Source checks are now
independent and missing store metadata is classified precisely. Seventy-seven
regressions pass without skips. The server image lacked the separate HTTP client
needed by delegated OAuth; `Dockerfile.studio` now explicitly installs the existing
`studio-oauth` extra. A versioned Linux/arm64 candidate passes OAuth initialization
without network and two actual file/SQLite server recovery/backup/restore drills.
The prepared artifact was subsequently approved and deployed. Namespace backup,
inactive restore, automatic rollback, corrected reapply and cooperative service
restart now pass. No DMG/app was built. See `STUDIO-VM-DISCOVERY-CHECKPOINT.json`
and the deployment acceptance section below.

Remaining acceptance:

- Actual personal-VM private mount, UID/GID and socket isolation now pass.
  Deployed worker recovery, power-loss and daemon restart remain separate.
  Durable storage is outside every Studio mount. Uncommitted/evicted/legacy
  ownership still requires review; no name-based adoption is permitted.
- The selected personal VM SSH connection is verified. Actual daemon
  restart/disconnection and frozen/installed acceptance remain open. Local real SSH loss/reconnect, context
  reconfiguration, unreachable transport and delayed-create recovery now have
  source proof. Actual local mutual-TLS verification and certificate rejection
  now pass too. Changes outside Docker context selection (for example SSH aliases,
  DNS or host credentials), certificate rotation/revocation and deployment TLS
  provisioning retain their own scope.
- Detached MCP host descendants on macOS/Windows, unsupported Linux kernels and
  direct supervisor SIGKILL remain separate. Supported Linux source now covers
  normal close and Studio owner death through the dedicated subreaper. A service
  cgroup/container is still required for a stronger containment boundary.
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

Done on 27 October 2026: direct TypeSafe decision provider is now implemented
and verified. The native adapter (`nexus/adapters/decisions/typesafe.py`) connects
directly to TypeSafe's System One API (`https://api.typesafe.ai/v1/systemone`)
using explicit `TYPESAFE_API_KEY` credentials. It reuses the shared typed schemas
for choice, boolean (`noul`) and ordered score questions. It strictly enforces
credential isolation without borrowing keys, retries transient failures up to three
times with exponential backoff, fails immediately on unauthorized/configuration
errors, and rejects image URLs.

Studio bindings, workbench decision runs, catalog presets (`decisionmodels`) and
`NexusTeamPolicy.decision_keys` support `typesafe` with default model `jev-1.13`.
The example consumer (`examples/nexus-bot/src/studio_interop_decisions.py`) supports
`--typesafe`. All 10 focused provider tests in `tests/test_studio_typesafe_decisions.py`,
114 decision/workflow regressions, and 181 Node tests pass without skips.
See `STUDIO-TYPESAFE-DECISION-CHECKPOINT.json`. No DMG was rebuilt and frozen
baseline acceptance counts are unchanged.

## Delivery rules

Each slice needs reusable framework code, an example consumer, relevant tests,
compatibility notes and a checkpoint naming the exact proof. Preserve mobile UI,
existing CLI subscriptions and private-by-default data handling. Run required
checks with zero unexplained skips; report optional live prerequisites separately.

The inventory's 135 accepted flags are recorded claims. They do not substitute
for the separate desktop release gate or actual live/device/VM evidence. Keep
whole-plan completion false until every required gate has matching proof.

## Personal VM deployment acceptance — 26 October 2026

The user-approved Studio-only upgrade is deployed and verified. The running
Linux/arm64 image matches the reviewed immutable candidate; all 653 installed
Nexus package files match their manifest hashes. The direct OAuth HTTP client is
present. Dedicated private proxy storage and its ownership ledger now pass all
12 host-layout checks, including actual host-source and raw-socket isolation.
All 15 anonymous HTTPS checks pass after a cooperative SIGTERM/zero-exit and
restart of both updated services. Five existing Nexus system services and the
router remain running; their source checkout and configuration are unchanged.

Private Studio namespace backups, verification and separate inactive restores
passed before each application attempt. An incorrect maintenance health-route
selection triggered automatic rollback, which restored the original image and
working routes. The corrected hosted readiness check passed on reapply. Live SQL
was never overwritten. The rollback proxy package includes the matching policy
helper needed for its captured source to start.

See [deployment evidence](STUDIO-VM-DISCOVERY-CHECKPOINT.json). Signed-in identity,
selected accounts, physical devices, deployed worker-recovery workloads,
full-host/auth-store restore and daemon/power-loss remain separate open checks.
Desktop delivery retains its existing gates. No DMG was rebuilt.

## Deployed worker boundary acceptance — 27 October 2026

The configured subscription-runner image now passes a real acceptance gate from
inside the deployed Studio controller through its existing filtered Docker
socket. Eleven boundary checks verify controller socket/private-store isolation,
denial of unscoped listings and unknown inspection, rejection of extra
capabilities, unapproved mounts and unconfigured images, owned stopped-worker
inspection, static worker execution, unstarted cleanup and retained absence
receipts. The successful gate acknowledges two disposable creates and starts one
static shell command. It mounts no credentials or workspace and invokes no
provider. No image download, service restart or configuration change is needed.

Postchecks find zero UUID workers, all five existing Nexus services active, the
router running, host-layout acceptance intact and all 15 anonymous HTTPS checks
passing. See [worker evidence](STUDIO-DEPLOYED-WORKER-CHECKPOINT.json) and the
explicit opt-in `tests/studio_deployed_worker_acceptance.py` gate. Required normal
regression discovery does not include this prerequisite-dependent live test.

This does not close deployed interruption/restart recovery or signed-in/provider
acceptance. At that checkpoint the VM had no `analysis_workers` profile. The subsequent
activation below closes that configuration gap for data, Python and media. Preserve that distinction in readiness
and future activation reviews. Physical devices, full-host/auth restore and
native installed-platform gates remain open. No DMG was rebuilt.

## VM analysis activation and cleanup identity fix — 27 October 2026

The personal VM now has the existing `data`, `python` and `media` profiles enabled
against an installed immutable worker image. CSV/JSON-to-SQLite, read-only SQL,
JQ, chart generation, Python execution and ImageMagick all pass through the live
controller's actual filtered proxy. The existing six-operation example creates
six private artifacts in a disposable workspace. Charts are 1200×800 PNGs;
ImageMagick produces the expected 2×2 image. Worker UID/GID, network denial,
read-only root, absent host mounts, process/open-file limits and unsafe-output
rejection pass. No provider/account call or production SQL test data is used.

Owner-death acceptance found a real defect in the slim server: Python's hardware
node discovery fell back to a process-local random value, changing recovery's
host hash between owners. The framework now preserves real-MAC hashes and uses
a validated Linux kernel boot UUID plus hostname only for random-node fallback.
Different boots/hostnames and old random-node records still require review;
no ownership grant is adopted. The record format and stored schemas are unchanged.

All 162 focused regressions and nine real local worker tests pass without skips.
An isolated VM candidate and the actual patched live controller both pass a
stopped-owner SIGKILL and explicit cleanup with zero create/start replay. Two
independent live processes also agree on host identity. All 653 installed source
files match the expected package, all 12 layout checks and 15 anonymous HTTPS
checks pass, and no UUID workers or production cleanup records remain. Other
five Nexus services and the router remain active. Fresh backups and inactive
restores passed before profile activation and before the server fix; live SQL
was never overwritten.

See [VM analysis evidence](STUDIO-VM-ANALYSIS-CHECKPOINT.json). Next operational
gates are signed-in tool use, live running-worker/uncertain-create recovery,
proxy/daemon interruption and full-host/auth-store restore. Kernel boot fallback
explicitly requires review after host reboot or changed controller hostname.
Vision/model setup, physical devices and native OS delivery remain separate.
No DMG was rebuilt, and frozen feature acceptance counts are unchanged.

## Local restart and lost-reply acceptance — 27 October 2026

The personal VM restart gate is deferred at the user's request. Work continued
locally with the actual installed Studio runtime, filtered proxy, private
ownership storage and Docker engine. Two opt-in checks pass without skips. An
actual running worker and its detached child stop on controller exit; restarted
proxy grants reject start and allow cleanup of both running/stopped records.

The second check disconnects the client only after the real daemon commits a
create or deletion. Unacknowledged creation stays uncertain, the program never
starts, and a lost deletion reply leaves cleanup pending. Authoritative absence
settles the final record without another forwarded mutation. One create and one
delete reach the daemon; two client DELETE requests reach the fault layer, with
one disconnected while offline. Both cases leave zero extra runs/artifacts,
unresolved records or validation-scope containers/volumes.

A reproducible offline fixture recipe and shared consumers now retain this
proof. Existing product recovery passes these cases; no runtime/API/schema change
was needed. See [local transport evidence](STUDIO-LOCAL-RECOVERY-TRANSPORT-CHECKPOINT.json).
The VM was not contacted or modified in this local task. Actual VM restart,
daemon/power-loss, authenticated/device/account and native release gates remain
open. No DMG was rebuilt and no frozen acceptance count changes.

## Native PostgreSQL logical restore — 27 October 2026

The earlier PostgreSQL-to-PostgreSQL gap now has a reproducible local gate. Two
real PostgreSQL 17.11 clusters run on a disposable internal Docker network with
no published ports or host-directory mounts. Current framework recovery code
and the existing example restore synthetic records into an empty target.

The check retains document search/revisions, conversations and artifact bytes;
disables restored schedules/connectors, revokes shares, marks external effects
uncertain and imports no executable jobs. Occupied record/job namespaces are
rejected without changes. Real child-process exits before and after commit
recover inactive; committed imports are not replayed. A real refused driver
connection returns the exact redacted CLI error. Restarting the target database
preserves records and completed receipts. Source and unrelated namespaces stay
unchanged. All scoped containers, volumes and the network are removed.

The separate gate passes alongside 46 existing SQL restore/driver regressions,
with zero skips. See [evidence](STUDIO-POSTGRES-RESTORE-CHECKPOINT.json) and
[the operator contract](../postman/contracts/studio-sql-restore.md). Existing
product recovery passed; no schema, authentication or runtime change was needed.
The example accepts explicit disposable source/target URLs and retains SQLite
as its default. VM/account-store recovery, physical PostgreSQL recovery, cluster
failover, power-loss and cloud-account checks remain open. The VM was not
contacted; no DMG was rebuilt and no frozen acceptance count changes.

## Actual GCS/Azure SDK backup acceptance — 27 October 2026

The earlier missing-SDK gap is closed for local source execution. Actual Google
Cloud Storage and Azure Blob Storage clients now read through independent local
Postman HTTP fixtures. Each captures two revisions, restores private bytes and
pinned document citations offline, preserves source metadata, bounds reads and
rejects missing, denied, corrupt or oversized objects without publishing a
snapshot. Empty objects avoid media reads; deliberately overlong responses fail.
GCS generation preconditions and 412/503 failures are additionally checked.

Two explicit SDK checks and 42 existing artifact-store/backup regressions pass,
with zero skips. No product runtime, authentication or schema changes were needed.
The reusable fixture and command are recorded in
[simulation context](../postman/simulations/studio-cloud-backup.context.md) and
[checkpoint](STUDIO-CLOUD-BACKUP-SDK-CHECKPOINT.json). Local emulator/synthetic
credentials do not prove cloud IAM, account permissions, retention/history or
Azure transient retry timing. Those checks remain open. The VM was not contacted,
no DMG was rebuilt and frozen acceptance counts remain unchanged.

## Local TLS certificate lifecycle — 27 October 2026

The existing real Docker client, filtered proxy and TLS gateway now pass an
explicit certificate lifecycle gate. Expired client/server certificates are
rejected. A same-CA server-leaf renewal works. Overlapping trust accepts old/new
clients; withdrawing the old CA rejects its client while the new client works.
A client retaining only the old server authority rejects the rotated server.
Actual gateway SSL reasons and Docker diagnostics establish the rejection paths.

One real-engine lifecycle check and 48 route/policy regressions pass without
skips. Fixture identities stay on a disposable internal network; only the
trusted proxy has the raw socket. All four volumes, containers and the network
are removed. No product runtime change, worker execution, provider request,
VM connection or DMG rebuild was needed. See
[evidence](STUDIO-TLS-LIFECYCLE-CHECKPOINT.json) and the existing recovery runbook.
Gateway restarts load changed certificates; automatic renewal/hot reload,
per-leaf revocation/CRL/OCSP, established-session invalidation and selected
production/VM TLS provisioning remain open. Frozen acceptance counts are unchanged.

## Real Linux local voice — 27 October 2026

The real Piper/Whisper job roundtrip now passes on Linux/arm64 in a disposable,
network-disabled container. It reuses the previously approved model files and
their verified hashes; no models are bundled or fetched during inference.
CPU-only PyTorch/Whisper and Piper generate a 3.1-second WAV and transcribe the
core sentence. The tiny model mishears the initial name as “Judio”; retain that
quality limit rather than claiming exact or general transcription accuracy.

One actual model-runtime gate plus 39 speech/readiness/pinning/cleanup regressions
pass, with zero skips. Private audio and temporary-file cleanup pass; the test
container is removed. Product runtime and existing Linux limits pass unchanged.
See [evidence](STUDIO-LINUX-SPEECH-CHECKPOINT.json) and the recovery-check recipe.
No VM connection, remote speech account, microphone or DMG rebuild was used.
Windows inference, physical Linux/installed-desktop operation, actual process-tree
interruption, device permissions and VM deployment remain separate. Frozen
acceptance counts are unchanged.

## Linux speech cancellation and ownership — 27 October 2026

Real local reproduction found a speech child-lifetime bug: cancellation waited
for its subprocess timeout and a detached resistant child survived. Linux speech
now reuses the existing isolated subreaper/pidfd cleanup with finite input and a
separate ownership pipe. Cancel, shutdown, timeout and controller death stop the
owned tree before temporary cleanup; no late audio artifact is published.
Missing ownership support fails before launch; no API fallback or replay occurs.

The framework fix, thin example and offline Linux fixture pass 72 focused
speech/MCP/worker-routing regressions plus actual Linux lifetime and real-model
gates, with zero skips. Finite input/output and errors remain compatible. See
[evidence](STUDIO-SPEECH-LIFECYCLE-CHECKPOINT.json). No schema, profile, API payload,
CPU/address-space/wall-limit or non-Linux execution changes. Upgrade package and
restart to activate the fix. The VM was not contacted; no DMG was rebuilt.
Windows/macOS containment, installed-platform and personal-VM repetition remain
open. Frozen acceptance totals are unchanged.

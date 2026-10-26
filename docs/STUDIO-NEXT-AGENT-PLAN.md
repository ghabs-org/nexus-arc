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

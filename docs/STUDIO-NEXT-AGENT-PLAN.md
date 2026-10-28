# Nexus Studio — next-agent delivery plan

**Distribution rule — user request, 26 October 2026:** rebuild or create a DMG
only after an explicit user request. Routine implementation must leave existing
DMGs and their recorded checksums unchanged.

Updated 28 October 2026. Continue in `nexus-arc`, reading `AGENTS.md` and the
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
| GCS/Azure storage SDKs & timeout/retry | [44 SDK/regression checks + timeout/503](STUDIO-CLOUD-BACKUP-SDK-CHECKPOINT.json) | Real cloud IAM/retention/history and live account restore |
| TLS certificate lifecycle | [49 real-engine/regression checks](STUDIO-TLS-LIFECYCLE-CHECKPOINT.json) | Production provisioning, automatic renewal, per-leaf revocation and established sessions |
| Speech child containment | [Linux/macOS evidence](STUDIO-SPEECH-LIFECYCLE-CHECKPOINT.json), [native Windows acceptance](STUDIO-WINDOWS-SPEECH-CHECKPOINT.json) | Installed applications, physical devices and VM |
| PDF contention reproduction & isolation | [18 PDF failure/concurrency checks + load proof](STUDIO-PDF-PREVIEW-CHECKPOINT.json) | Rebuild installed native app/DMG only when explicitly requested |
| Direct TypeSafe decisions | [Provider and integration evidence](STUDIO-TYPESAFE-DECISION-CHECKPOINT.json) | Selected real account acceptance |

Counts overlap and must not be summed into a full-suite total. The Linux and macOS
speech fixes change source behavior: cancellation, shutdown, timeout and owner death
stop owned detached children. Real Piper/Whisper roundtrips also pass. The tiny
model's name-recognition error remains documented. These fixes and the isolated PDF
frozen entrypoint are not yet deployed to the VM or included in the existing DMG.

Native macOS authentication, workspace locking, bundled Python and full-SDK
tester installation already have proof. The macOS arm64 beta is ad-hoc signed,
not notarized; latest source fixes are not packaged. Final desktop release and
Linux/Windows installed-platform acceptance remain open. See
[tester evidence](STUDIO-DESKTOP-FRIEND-DMG-CHECKPOINT.json).

### Native Windows speech acceptance — 28 October 2026

Windows source process ownership is now **native-verified on Windows Server 2022
x64**, with Python 3.11.9 and 3.13.17. Both jobs passed 37 checks without skips,
plus all seven native cases: cancellation, timeout, abrupt owner death, finite
input/stderr/nonzero status, queued cancellation, running Studio cancellation and
Studio shutdown. Model commands are synthetic; queue, ownership and cleanup are
real. The affected local regression gate also passed 108 tests with zero skips.

Native CI exposed a private-supervisor shutdown abort caused by its blocked daemon
stdin reader. The fix explicitly finishes job cleanup, flushes output and exits
only the isolated helper without Python finalization. Both native jobs pass on
source commit `5de733c831dbbd177699017d8826b032c2ab1c9b`.
[Passing CI run](https://github.com/ghabs-org/nexus-arc/actions/runs/40746477508).
All raw Windows source hashes were verified against that commit after exact
LF-to-CRLF checkout conversion; both original reports are retained in the
[checkpoint](STUDIO-WINDOWS-SPEECH-CHECKPOINT.json).

The work originated on `codex/windows-speech-20261028` and is now integrated
into `develop` as described below. No VM upgrade or installer build occurred. Real Windows model completion and
interruption now have the later evidence below; installed desktop flows, signing and updates remain separate gates.
See [repeatable instructions](STUDIO-WINDOWS-SPEECH-ACCEPTANCE.md).

### Real Windows speech models — 28 October 2026

Real Piper 1.8.0 and Whisper 20250625 now pass an ordinary Studio job roundtrip
on Windows Server 2022 x64/Python 3.11.9, with Torch 2.14.1+cpu, ONNX Runtime
1.31.1 and FFmpeg 9.0.2. Hash-verified approved public models produced a private
127,532-byte, 2.891-second WAV. Whisper transcribed the synthetic sentence
correctly. Temporary audio and ownership records were removed. No microphone or
speech API was used; model preparation occurred explicitly before the worker.

The real-model test passed without skips; 46 focused local checks also passed.
Both native lifecycle matrix jobs passed again in
[the same CI run](https://github.com/ghabs-org/nexus-arc/actions/runs/40748628074).
[Retained model evidence](STUDIO-WINDOWS-VOICE-CHECKPOINT.json) records versions,
model/source hashes and the exact tested commit. Models and audio are not CI
artifacts. No product runtime or packaging changed in this acceptance slice.
The later real-model interruption check below closes that source runtime gap.
Installed desktop, physical-device/API-account and VM acceptance remain separate. Milestone 9 stays last; no DMG was rebuilt.

### Real Windows model interruption — 28 October 2026

Piper and Whisper now pass cancellation and Studio shutdown after observed real
inference on Windows Server 2022 x64/Python 3.11. Each of the four cases verifies
that the model process exits, the run becomes cancelled/interrupted as expected,
no temporary audio or owner records remain, and no artifact is published.
Markers follow a real Piper ONNX chunk or a completed Whisper encoder forward;
hooks preserve model outputs and add no wait. The controller confirms that the
model is still active before interruption. An ordinary unwrapped model roundtrip
passes again in the same job.

[Passing run](https://github.com/ghabs-org/nexus-arc/actions/runs/40750333137)
records two model acceptance tests with four interruption cases, no failures or
skips. The 48 focused local checks pass too. Source hashes match the tested commit
with exact Windows checkout conversion; [retained evidence](STUDIO-WINDOWS-VOICE-CHECKPOINT.json)
keeps full outcomes and instrumentation boundaries. Timings are samples, not an SLA.
No product runtime change, merge, VM operation or installer build was required.
The remaining gates are installed desktop, physical devices, selected API accounts
and deferred VM operations; desktop milestone 9 stays last.

### Development branch integration — 28 October 2026

The complete Windows speech implementation and acceptance work is integrated
into `develop` by fast-forward from `codex/windows-speech-20261028`. There were
no incoming development changes or conflicts. The affected speech, ownership,
worker, bundling and plan gate passed **115 checks with zero skips**. All 13
source files covered by the retained native Windows reports match their recorded
hashes; integration did not change tested runtime code.

[Integration evidence](STUDIO-WINDOWS-SPEECH-CHECKPOINT.json) records the source
head and report hash. This updates the development branch only: no main-branch
merge, VM deployment or installer build was performed. The remaining work below
requires selected accounts, physical devices or the deferred deployment target;
installed desktop milestone 9 remains last.

### GitHub selected-account live reads — 28 October 2026

The existing authenticated GitHub account now passes the native Studio REST
connector's read path against `ghabs-org/nexus-arc`: account/repository identity,
issue and PR lists and detail lookup. All six remote calls were GET requests.
An unselected comment action was rejected locally before transport; no remote
mutation or message occurred. The temporary workspace retained only a credential
reference and was checked for absence of token bytes.

The separate unauthenticated Postman public contract passed four assertions;
31 related regression tests passed with zero skips. See
[scoped evidence](STUDIO-GITHUB-LIVE-READS-CHECKPOINT.json) and
[repeatable consumer](STUDIO-GITHUB-LIVE-READS.md).
This does not establish GitHub write/webhook behavior, shared-team isolation,
private repository access or personal-VM credentials. Other account acceptance
remains open; no real connector/workspace, VM or installer was modified.

### Current readiness and next target — 28 October 2026

The completion consistency audit reports 135 scoped local flags accepted, zero
inconsistencies and `plan_complete: false`. The desktop checkpoint remains open.
Windows source/model evidence and the selected GitHub live-read evidence retain
their narrower scope; they do not establish installed-app, device or VM readiness.
See the [readiness snapshot](STUDIO-DELIVERY-READINESS-CHECKPOINT.json).

No other common integration credential reference is exposed in the current Mac
process environment. This is a presence check, not proof that no other account
exists. Do not search credential files or invent additional local tests as a
substitute for the remaining live gates. The user confirmed VM access is available again on 28 October. SSH currently
requires a renewed Tailscale identity check, so no host discovery command has
completed yet. Resume read-only inspection after that check; prepare an exact
upgrade/backup/rollback review before changing live services. Desktop milestone 9
remains last, and a new DMG requires an explicit request. Do not repeat already accepted native CI without a change or
unresolved failure that warrants it.

### VM resumption preflight — 28 October 2026

The user selected VM acceptance and confirmed availability. Tailscale requires a
renewed SSH identity check; its link was presented only in chat. No remote
inventory command has completed and no VM state changed. The local deployment,
host-check and backup gate passed **57 checks with zero skips**.

Current candidate source is `f644af55c6a8a99b125b02c02a52e80c8d0f5905`.
Comparison against the historical VM base identifies 58 changed files, including
Dockerfile/Compose changes; it is not an exact comparison with the deployed
patched image. Obtain the actual manifest after SSH verification. Prepare exact
candidate, consistent backup, inactive restore and rollback evidence before any
new Studio-only upgrade. Earlier approval applied to the earlier artifact, not
an unspecified new revision. See [resume record](STUDIO-VM-DISCOVERY-CHECKPOINT.json).

### Current VM discovery — 28 October 2026

SSH identity verification completed. Studio and its proxy are running on the
ARM64 VM; the router and five existing Nexus units remain running. All 15
anonymous HTTPS checks pass. Exact comparison of 548 installed source/assets
finds only four Windows/desktop changes and the new Windows helper absent. The
VM already has current Linux application behavior; do not deploy an unnecessary
new backend image.

Current configuration does not match the historical durable-proxy record:
`PROXY_STATE_DIR` is unset, the dedicated store is not mounted, and its expected
host directory is absent. Host layout reports seven passed checks and five
failed/unavailable. Raw socket isolation still passes. No generated analysis/CLI
containers exist. Do not run disruption/recovery acceptance against this layout.

A concrete repair is prepared but **not applied**: back up the private selected
Compose file, stop Studio cooperatively, ensure workers are gone, append durable
state settings/helper mounts while preserving the existing overlay, create the
isolated service-owned store, recreate only the proxy without pull/build and
start the unchanged Studio container. Exact merged configuration validates.
Preserve original proxy stack/image and overlay for rollback; require all host
layout and anonymous HTTPS checks afterward. No auth database, credentials,
repository source, other Nexus service or installer changes are proposed.

[Current VM evidence](STUDIO-VM-DISCOVERY-CHECKPOINT.json) retains redacted results.
The private reviewable plan stays outside the repository. A new maintenance
approval is required because earlier deployment approval covered a different
artifact. Signed-in identity, controlled recovery and full-host/account-store
restore remain separate gates. No VM change has been applied in this discovery.

### Approved VM proxy repair — 28 October 2026

The user approved the concrete Studio/proxy repair. It is now applied and
live-verified: original overlay backup checksums passed; Studio stopped
cooperatively; the reviewed append-only private Compose change and isolated
ownership store were installed; only the proxy was recreated; the original
Studio container restarted with the same image. Both image identities are
unchanged. No source update, auth/database/credential migration or image pull/build
occurred. Maintenance took 10.224 seconds in this observed run.

All 12 daemon-host layout checks pass, including private-source/raw-socket
isolation and mode/ownership of the now-present ledger. All 15 anonymous HTTPS
checks pass both from the VM and externally. Five Nexus units and the router/
other running containers remain unchanged; no generated worker was created or
left behind. Rollback was prepared but not needed. Private backup paths and
operator artifacts remain outside the repository.

[Current acceptance record](STUDIO-VM-DISCOVERY-CHECKPOINT.json) supersedes the
pre-repair drift snapshot. An existing signed-in session is still unverified;
the verifier's missing-session identity result must not be counted as an
anonymous-route failure. Controlled worker/proxy recovery and full-host/account-
store restore remain separate gates. No DMG or installed application was built.

### Remaining work, in delivery order

1. **Local work while VM access is deferred (completed 27 October).** macOS speech
   child containment is closed without weakening the Linux fix; Azure timeout/retry/503
   failure cases are verified; the historical 25 October PDF `KeyError: 'pages'` timeout
   is reproduced under 8-process CPU contention on the pre-isolation frozen binary and
   proven resolved by isolated worker dispatch; `docs/USAGE.md`, audit/plan completion
   gates (`STUDIO-M9-DESKTOP-CHECKPOINT.json`), and local checkpoint `source_sha256`
   hashes are reconciled. Do not rebuild a DMG unless explicitly requested.
2. **Selected accounts and physical devices.** Windows source ownership now has
   real runner evidence, including real-model completion and interruption on
   Python 3.11. Installed desktop and physical-device checks remain separate. GitHub public-repository reads now have selected-account live proof;
   check other configured integration permissions/authentication, API voice/embedding quality and billing paths;
   verify real phone/tablet microphone, playback, touch and accessibility. Exit:
   account/device results identify exactly what ran; unavailable prerequisites stay
   pending. Local HTTP/SDK fixtures and emulated widths are already available.
3. **VM repaired; remaining acceptance.** SSH, same-image durable proxy repair,
   all host-layout checks and anonymous HTTPS are verified. No backend image
   upgrade is needed. Retain a reviewed candidate containing later source fixes, retain backup/inactive
   restore/rollback, then verify signed-in execution, controlled worker/proxy
   interruption and actual account-store/full-host restoration. Existing selected
   VM upgrade/worker evidence remains valid for its recorded version, not later
   code. The prior deferral is lifted. The approved proxy repair is complete. Current gates: an existing signed-in
   session, scoped recovery and separate restoration acceptance.
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

## Consolidated 27 October 2026 delivery record

All 27 October 2026 slices are folded into this single record:

1. **Deployed worker boundary & VM analysis activation ([worker](STUDIO-DEPLOYED-WORKER-CHECKPOINT.json), [VM analysis](STUDIO-VM-ANALYSIS-CHECKPOINT.json)):**
   - Eleven boundary checks pass from inside the deployed Studio controller through its filtered Docker socket (`tests/studio_deployed_worker_acceptance.py`).
   - Activated `data`, `python` and `media` profiles on the personal VM against the immutable worker image; the six-operation workflow (CSV/JSON-to-SQLite, SQL, JQ, 1200×800 PNG chart, Python, 2×2 ImageMagick) passes with strict UID/GID, network denial and read-only root.
   - Fixed slim-server random hardware node fallback by preserving real-MAC hashes and using validated Linux kernel boot UUID + hostname only when hardware node discovery is random. All 162 focused regressions, 9 local worker checks, 12 layout checks and 15 anonymous HTTPS checks pass.

2. **Local restart and lost-reply acceptance ([local transport](STUDIO-LOCAL-RECOVERY-TRANSPORT-CHECKPOINT.json)):**
   - With VM restart deferred, two opt-in local Docker/proxy checks verify that controller exit stops active workers and detached children, and that post-commit client disconnects on create/delete leave zero extra runs, artifacts or orphan containers.

3. **Native PostgreSQL logical restore ([PostgreSQL restore](STUDIO-POSTGRES-RESTORE-CHECKPOINT.json)):**
   - Two disposable PostgreSQL 17.11 clusters on an internal Docker network verify logical restore into an empty target, inactive schedules/connectors, revoked shares, occupied-target rejection, commit-boundary process crash recovery and database restart across 47 checks with zero skips.

4. **Actual GCS/Azure SDK backup & resilience ([cloud SDKs](STUDIO-CLOUD-BACKUP-SDK-CHECKPOINT.json)):**
   - Real `google-cloud-storage` and `azure-storage-blob` SDKs against local Postman HTTP simulations verify two-revision capture, offline restore, bounded range reads, GCS generation preconditions (412/503) and Azure connection reset, timeout and HTTP 503 abort handling across 44 SDK/regression checks plus resilience unit tests without partial state publication.

5. **Local TLS certificate lifecycle ([TLS lifecycle](STUDIO-TLS-LIFECYCLE-CHECKPOINT.json)):**
   - Real Docker client, filtered proxy and TLS gateway verify expired client/server rejection, same-CA server-leaf renewal, overlapping CA rotation and old-CA withdrawal across 49 checks with zero skips.

6. **Linux & macOS speech child containment ([Linux voice](STUDIO-LINUX-SPEECH-CHECKPOINT.json), [speech lifecycle](STUDIO-SPEECH-LIFECYCLE-CHECKPOINT.json)):**
   - Real Piper/Whisper roundtrip passes on Linux/arm64 in a network-disabled container (40 checks; tiny-model name transcription limit retained).
   - Linux subreaper/pidfd (`74` checks) and macOS process-group + `libproc` descendant tracking (`nexus.core.studio_mcp_supervisor`) stop detached, SIGTERM-resistant children within `0.03s` on cancel, shutdown, timeout and controller death before temporary audio cleanup.

7. **Intermittent PDF failure load reproduction & closure ([PDF preview](STUDIO-PDF-PREVIEW.md), [PDF checkpoint](STUDIO-PDF-PREVIEW-CHECKPOINT.json), [PDF isolation](STUDIO-PDF-ISOLATION-CHECKPOINT.json)):**
   - Reproduced the 25 October intermittent `KeyError: 'pages'` (`extraction_available=false`, `error_code="pdf_timeout"`, `"pages"` omitted) under 8-process CPU contention at concurrency 8 using the pre-isolation frozen binary (`/Applications/Nexus Studio.app/Contents/Resources/sidecar/nexus-sidecar`, built before `790fbe3`): `16/16` direct worker calls (`15.014s–15.034s`) and `8/8` `StudioService.artifacts.preview` calls (`15.024s–15.040s`) timed out importing the full `nexus` + AI SDK graph before reaching `pypdf`.
   - Under the identical 8-process CPU contention and concurrency 8, current isolated source dispatch passes `16/16` in `0.743s–0.908s` and `FROZEN_ENTRYPOINT` isolated dispatch passes `16/16` in `0.893s–1.034s`. Payload/time limits (`16 MiB`, `15s` wall, `10s` CPU) remain unchanged.

8. **Audit/plan alignment, `docs/USAGE.md` rewrite & checkpoint hash reconciliation:**
   - Reconciled `STUDIO-M9-DESKTOP-CHECKPOINT.json` (`milestone_complete: false`, `whole_plan_complete: false`) and tightened `tests/studio_completion_audit.py` so `--require-complete` exits `1` while Milestone 9 is open (`plan_complete: false`, `inconsistencies: []`).
   - Rewrote `docs/USAGE.md` around `examples/nexus-bot` and the current `nexus-arc` API and removed the out-of-date notice.
   - Recomputed and synchronized `source_sha256` / `reviewed_source_sha256` across all local checkpoint JSON records. No VM was contacted and no DMG was rebuilt.

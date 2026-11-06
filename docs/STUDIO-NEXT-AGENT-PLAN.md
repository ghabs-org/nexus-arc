# Nexus Studio implementation plan

Updated: **6 November 2026**. Delivery status: **open**.

Latest addition: optional macOS native Nexus sign-in, PKCE device grants,
Keychain storage, revocation and guided local Codex/Claude Code/Antigravity setup
are included in the signed/notarized **1.0.3 Apple Silicon DMG**, built on explicit
request. See [release evidence](STUDIO-DESKTOP-1.0.3-CHECKPOINT.json). Startup, guide,
Account-menu presence and clean close were observed. Hosted deployment, provider
callback registration, manual dialog click-through and real account acceptance
remain pending in **DESKTOP-05**. Windows/Linux account storage and guided terminal
launch are implementation gaps. The old release is preserved; rebuild again only
on an explicit user request.

This is the current action queue. The [structured task record](STUDIO-ROADMAP.json)
contains prerequisites, ownership, dependencies and acceptance criteria for every
remaining task. Earlier dated entries are preserved in the
[delivery history](STUDIO-DELIVERY-HISTORY.md); their old next steps are not new work.
The original approved specification remains in the operator-owned
`STUDIO-IMPLEMENTATION-PLAN.md` and frozen requirement records.

## What is complete

| Area | Recorded status and boundary |
| --- | --- |
| Original feature inventory | 135 local acceptance flags: 119 fixture-verified records, 12 implemented constraint records and four explicit exclusions. These are not 135 live service certifications. |
| Milestones 1–8 | Scoped local acceptance, including workflow/Quick Build milestone 5 and connector/protocol milestone 6. Do not rebuild them from stale prose. |
| Catalog | All 31 pinned identities accounted for: 27 supported entries and four exclusions. Aliases can share an implementation; live vendor accounts are tracked separately. |
| Added milestones 10–12 | Scoped local acceptance for declarative compatibility, skill distribution and OpenClaw. OpenClaw live channel/gateway checks remain in LIVE-01. |
| UI and local additions | All ten original design-audit source fixes; System/Light/Dark; local fonts/icons; offline evaluation comparison; chat/Quick Build draft recovery; workflow canvas undo/redo; opt-in desktop notifications. |
| Browser verification | Scoped phone/tablet/desktop layout, keyboard, viewer and review-flow evidence. Some final save/download actions and performance metrics are not established by that record. |
| macOS release | Signed/notarized/stapled 1.0.3 exists locally with current source, native accounts and full-SDK packaging. Installed runtime and updater signature/version checks pass. The historical 1.0.2 install/rollback proof retains its original scope. |
| Direct browser identity login | GitHub/GitLab plus previously linked LinkedIn/X/Meta accounts, configured-provider buttons, protected callbacks and session-reference fallback. Local acceptance only; deployed provider checks remain pending. See [browser login](STUDIO-BROWSER-LOGIN.md). |
| LLM subscription connections | Codex, Claude and Antigravity connection buttons on sign-in; guarded per-user pages, selection retention and existing CLI account flows. 178 focused checks and synthetic browser acceptance passed; live provider sign-in and deployment remain pending. |
| Additional AI account shortcuts | Copilot and OpenCode entry points reuse existing account controls and encrypted API-key saving. 189 focused checks and synthetic browser saves passed; live provider invocation and deployment remain pending. |

Latest recorded feature checks: **320 frontend checks**, including 20 draft checks,
7 graph authoring/undo checks, and 8 notification checks, plus **35 backend/bundling checks**,
with zero failures/skips. These counts come from the
[enhancements checkpoint](STUDIO-ENHANCEMENTS-CHECKPOINT.json).

## What is actually missing

**Known product implementation:** Windows/Linux installed native updates. The
[current updater](../apps/studio-desktop/src-tauri/src/updates.rs) returns early
outside macOS, and application discovery searches for an `.app` bundle. This needs
platform-specific adaptation and qualification; calling it only a testing gap is
incomplete. Keep this work in desktop milestone 9, last.

**Release configuration/delivery:** Windows signing and timestamping, fresh
reviewed desktop artifacts, actual cross-platform installed behavior and a genuine
prior-release upgrade. The existing macOS signing pipeline is already implemented;
do not repeat certificate setup without evidence that it is broken.

**Acceptance evidence:** final save/download UI flows, measured performance,
physical devices/assistive technology, selected live accounts/database servers,
and recovered-host/full-machine operation. An unavailable test environment does
not establish a missing adapter implementation or a successful live check.

## Execution order and task board

| Order / ID | Task | Kind | Current availability |
| --- | --- | --- | --- |
| 1 / WEB-01 | Final reviewed save, explicit downloads and fresh-workspace re-import | Validation | Completed; local browser acceptance verified |
| 1 / WEB-02 | Performance baseline for history, tables and graphs | Validation | Completed; baseline recorded in STUDIO-PERFORMANCE-BASELINE.md |
| 2 / DEVICE-01 | Physical phone/tablet, screen reader, voice and installed/offline web app | Validation | Ready for operator target-device checks |
| 2 / LIVE-01 | Selected-account integration matrix, voice/embeddings and OpenClaw channels | Validation | Needs selected accounts and exact action scope |
| 2 / DB-01 | SQL Server and Oracle real-server contracts | Validation | Needs disposable instances/credentials |
| 3 / OPS-01 | Recovered-host browser/login | Validation | VM work deferred by user |
| 3 / OPS-02 | Full-host disaster recovery | Validation | Deferred; follows OPS-01 |
| 4 / DESKTOP-01 | Windows/Linux installed-update lifecycle | Implementation | Code complete; native host qualification in DESKTOP-04 |
| 4 / DESKTOP-02 | Windows installer signing and timestamping | Release configuration | Needs signing identity and Windows host |
| 4 / DESKTOP-03 | Package current source into fresh desktop artifacts | Packaging | Held until explicit build request |
| 4 / DESKTOP-04 | Install/auth/ownership/lifecycle on another Mac and Windows/Linux | Validation | Needs fresh artifacts and actual hosts |
| 4 / DESKTOP-05 | Genuine previous-release data upgrade and rollback | Validation | Needs actual old/new releases |
| 4 / DESKTOP-06 | Native menu against chosen signed-update distribution path | Validation | Needs distribution decision and artifacts; publication remains withheld |

**Phase 1 Complete (WEB-01, WEB-02).** Next available tasks are Phase 2 gates:
`DEVICE-01` (physical device, voice and assistive tech checks), `LIVE-01`
(selected live provider accounts) and `DB-01` (disposable SQL Server and Oracle instances).
Do not ask for VM access, accounts or a new DMG before doing these available tasks.
Fix defects found by acceptance at their responsible layer; do not add unrelated
features to a verification task.

## Phase 1: finish local acceptance

**WEB-01:** use synthetic resources in a disposable workspace. Complete the actual
reviewed dataset/judge save, then read saved revisions back through the UI. Click
report, dataset/judge and experiment download buttons; verify the downloaded
filenames, bytes and pinned identities/revisions. Re-import supported handoffs into
a fresh disposable workspace. Check keyboard and phone/desktop use. Exit with
file/revision evidence, no automatic download and zero unintended provider runs.
Existing backend fixtures remain useful; a plan preview is not the final save.

**WEB-02:** record real performance evidence for declared browser/device settings
and small/large history, table and graph data. Capture load/layout-shift metrics,
interaction latency, tool versions and measurement method. Define explicit budgets
and triage measured bottlenecks before declaring acceptance. Accessibility, SEO
and Best Practices scores do not stand in for performance; distinguish lab
observations from field Core Web Vitals. No performance defect is asserted yet.

## Phase 2: devices and selected services

**DEVICE-01:** follow the [physical-device checklist](STUDIO-PHYSICAL-DEVICE-ACCEPTANCE.md).
Record model/OS/browser and each result for portrait/landscape, keyboard/safe areas,
text scaling, touch, VoiceOver/TalkBack, real microphone allow/deny/retry,
transcription, playback/speed/Stop and installed-web-app reopening. Test offline
cached-shell/status behavior and reconnect without replay; private API data must
not become an offline cache. Check draft recovery across a real app/browser
interruption. API transcription needs a deliberately selected provider.

**LIVE-01:** keep an entry-by-entry implementation/fixture/configured/live matrix,
including aliases and explicit exclusions. Use selected existing accounts first.
Verify authentication/permissions, selected voice/embeddings and provider behavior
with actual result scope. OpenClaw's live Telegram/Discord and remote-gateway
cases are still pending; the gateway case also respects the VM deferral. Use
explicit test destinations/action scope for sends or paid calls. Unavailable
vendor accounts remain **live validation pending**; catalog coverage stays intact.

**DB-01:** use disposable SQL Server and Oracle schemas with least-privilege
credentials. Verify real connection/pooling, discovery, bounded queries, parameter
validation and recovery. Confirm read-only defaults; enabled writes need an
explicit reviewed test scope. Record server/driver versions and cleanup. Existing
local PostgreSQL/MySQL/MariaDB/MongoDB and fixture results keep their own scope.

## Phase 3: operations, deferred

**OPS-01:** after the user resumes VM work, verify an isolated recovered HTTPS
host's browser/login, account/workspace isolation and private responses, with no
queued external action replay. Keep live Studio separate and record snapshot/source
versions. [Recovered-host login preparation](STUDIO-RECOVERED-HOST-LOGIN.md) already exists.

**OPS-02:** verify full OS/service/ingress/daemon or controlled power-loss recovery,
including state, definitions, documents/indexes, artifacts and credential references.
Record restart order, recovery time, no duplicate external effects and rollback.
Selected-service and encrypted-copy restores are already recorded; they do not
replace full-machine acceptance. Do not contact or change the VM during deferral.

## Phase 4: desktop milestone 9, last

**DESKTOP-01:** Code implementation complete for macOS, Windows, and Linux application
discovery and backup preparation in `apps/studio-desktop/src-tauri/src/updates.rs` and
`nexus/core/studio_desktop_release.py`. Unit tests cover `.app` discovery, Windows
folder/executable discovery, and Linux AppImage/folder paths. Physical native qualification
on Windows/Linux hosts remains pending in DESKTOP-04.

**DESKTOP-02:** configure Windows signing identity and timestamping through the
existing Tauri seam where sufficient. Verify publisher identity, tamper rejection
and installation on Windows. Keep private keys/passwords out of Git. Configuration
and native verification remain missing; a new custom signing framework is not
assumed necessary.

**DESKTOP-03:** only after an explicit build request, package a reviewed source
revision with source/dependency manifests, current UI/fonts/icons/drafts and
backend fixes. Produce local signed/notarized artifacts and checksums as applicable.
Run bundled full-profile checks. Keep the old release and its evidence intact;
do not publish or replace artifacts automatically.

**DESKTOP-04:** test those exact artifacts on another physical Mac and installed
Windows/Linux: authentication, workspace chooser/import/ownership, full-profile
features, declared external prerequisites, close/crash/reopen and uninstall.
Source CI runners alone are insufficient. Native update checks require the
qualified DESKTOP-01 path and appropriate signed artifacts.

**DESKTOP-05:** create a disposable workspace with an actual previously shipped
release, then upgrade with the candidate. Verify conversations, definitions,
schedules, documents/indexes and artifacts, pre-upgrade backup and a compatible
restore/rollback after controlled failure. Synthetic prior-version metadata does
not close this test.

**DESKTOP-06:** use the installed native update menu against the selected authorized
HTTPS distribution path and actual versioned signed artifacts. Verify download,
signature/version enforcement, backup, replacement and restart across two releases.
A prepared local feed is not a deployed-feed test. **Public publication is a user-held
distribution choice, not a missing product feature.** Release files stay local
unless publication is explicitly requested.

## Approved additions and enhancements

| ID | Feature | Status | Evidence |
| --- | --- | --- | --- |
| DRAFT-01 | Unsent chat and Quick Build draft recovery | Implemented | `docs/STUDIO-DRAFT-RECOVERY-CHECKPOINT.json` |
| IDEA-01 | Workflow canvas undo/redo with bounded validated edit history | Implemented | `docs/STUDIO-ENHANCEMENTS-CHECKPOINT.json`, `tests/studio_graph_browser.test.cjs` |
| IDEA-02 | Opt-in run-completion and approval notifications with private-content limits | Implemented | `docs/STUDIO-ENHANCEMENTS-CHECKPOINT.json`, `tests/studio_notifications.test.cjs` |

Draft recovery and the workflow undo/redo and opt-in notification features are
implemented outside the frozen 135-item original feature inventory.
They leave the remaining release and acceptance gates below open.

## Completion and handoff rules

- A task passes only with reproducible evidence for its named source, artifact and
  environment. `ready`, `needs-*`, `deferred` and `held` are never passes.
- Implementation, configuration, fixtures, live services and installed artifacts
  are separate statuses. Preserve exclusions and the frozen 135 flags.
- Record failures and unavailable prerequisites. Any accepted limitation/exclusion
  requires an explicit recorded decision; do not silently mark it complete.
- Keep milestone 9 last, VM work deferred, new builds request-only and publication
  withheld. Do not ask again for authorization already granted within its scope.
- Every future framework implementation uses the shared layer, a thin example
  consumer, relevant checks and scoped evidence. No frontend framework rewrite.
- Completion audits check record consistency, not behavioral equivalence. Whole-plan
  completion stays false while required delivery gates remain open.

Evidence: [coverage reconciliation](STUDIO-COVERAGE-RECONCILIATION.md),
[broader browser checkpoint](STUDIO-BROWSER-CHECKS-CHECKPOINT.json),
[draft recovery](STUDIO-DRAFT-RECOVERY-CHECKPOINT.json),
[signed Mac release](STUDIO-DESKTOP-SIGNED-RELEASE-CHECKPOINT.json),
[OpenClaw checkpoint](STUDIO-M12-OPENCLAW-CHECKPOINT.json).

# Nexus Studio delivery status

Updated: **10 November 2026**. Whole-plan completion: **open**.

Use the [current implementation plan](STUDIO-NEXT-AGENT-PLAN.md) for next work and
[structured roadmap](STUDIO-ROADMAP.json) for task IDs, prerequisites and acceptance.
Earlier chronological next steps are in the [history](STUDIO-DELIVERY-HISTORY.md).
This status is a summary, not a second task queue.

## Completed, within recorded scope

- The frozen inventory has 135 accepted flags, including four exclusions. All 31
  catalog identities are accounted for: 27 supported entries and four exclusions.
  Aliases may share code. This is local coverage, not 27 live account certifications.
- Milestones 1–8 and added 10–12 have scoped local acceptance. Workflow/Quick Build
  milestone 5 and connector/protocol milestone 6 do not need to be rebuilt.
- The ten original UI source findings, System/Light/Dark, local fonts/icons,
  offline evaluation comparison and chat/Quick Build draft recovery are implemented.
- Scoped browser checks cover layouts, keyboard, viewers and review flows. Historical
  draft feature checks pass 309 frontend and 35 backend/bundling tests,
  with zero failures/skips. These suites were not rerun for this plan-only update.
- Workflow canvas undo/redo and opt-in notifications are implemented additions.
- Bundled release notes, assistant version lookup, a Release notes menu page and
  version-note build requirements are implemented. Automatic routing, model
  explanations/feedback and a configurable Studio router timeout are in source.
- 1.0.5 is the latest fully checkpointed signed/notarized local DMG. Source and
  retained local build metadata record 1.0.6 with a missing release date and
  incomplete frozen-content evidence. Neither proves later source fixes are in an
  installed artifact. See [versioned release history](STUDIO-RELEASE-NOTES.md).

## Missing implementation and release work

**DESKTOP-01:** Cross-platform application discovery and update backup preparation
implemented for macOS, Windows, and Linux in `apps/studio-desktop/src-tauri/src/updates.rs`
and `nexus/core/studio_desktop_release.py`. Unit tests pass across all target OS paths.
Native host qualification on physical Windows/Linux runners remains pending in DESKTOP-04.

**DESKTOP-02..06:** Windows signing configuration, fresh reviewed desktop packages,
installed cross-platform checks, genuine old-release/schema upgrade/rollback and
native update distribution-path acceptance remain. New builds require an explicit
request. Public publication remains withheld and is not a missing product feature.

## Missing validation

- **DEVICE-01:** real microphone/playback/touch, screen readers and installed/offline
  web-app behavior on target devices.
- **LIVE-01 / DB-01:** selected real-account/provider checks, OpenClaw live channels,
  and actual SQL Server/Oracle contracts. Missing accounts do not imply missing adapters.
- **OPS-01 / OPS-02:** recovered-host browser/login and full-machine recovery retain
  their separately recorded acceptance scope. This audit establishes no new VM
  deployment, login or recovery result.

**Next available work: Phase 2 gates (DEVICE-01, LIVE-01, DB-01).** Phase 1 local
validation (`WEB-01` reviewed saves/downloads/re-import, `WEB-02` performance baseline)
is complete. Next work requires target devices, selected live accounts, or disposable
SQL Server/Oracle databases. Operational work requires a separately scoped task;
desktop milestone 9 stays last.

Workflow undo/redo and notifications are implemented additions rather than
original-plan blockers. Device/account/release gates require actual
results or an explicitly accepted scope decision; they are not automatically passes.

## Evidence boundaries

The [1 November verification snapshot](STUDIO-DELIVERY-STATUS-CHECKPOINT.json),
[broader browser record](STUDIO-BROWSER-CHECKS-CHECKPOINT.json) and
[2 November draft record](STUDIO-DRAFT-RECOVERY-CHECKPOINT.json) remain historical
source/environment snapshots. Do not rewrite their hashes after documentation
changes or combine their overlapping counts into a completion percentage.

This update changes planning documents only. It does not execute the task board,
change product code, contact the VM, invoke providers, rebuild installers or publish.

# Nexus Studio current delivery status

Date: 1 November 2026. Whole-plan completion: **open**.

This status supersedes stale next-step prose in earlier dated records. It does
not replace their exact source/environment evidence or promote inventory flags.

## Approved addition — 2 November 2026

[Unsent draft recovery](STUDIO-DRAFT-RECOVERY.md) is now implemented for chat and
Quick Build, with 309 frontend/35 backend checks and scoped browser verification.
It is separate from the frozen inventory and leaves the delivery gates below
open. This follow-up adds product source; the 1 November no-code-change and hash
statements below describe their original verification snapshot.

## Completed locally

- The frozen inventory retains 135 accepted records, including four exclusions.
  Catalog coverage records 27 supported entries and four exclusions; aliases do
  not imply independent implementations. Local acceptance is not account-level
  live certification. Milestones 5 and 6 do not need to be rebuilt.
- All ten original design-audit source findings have implementations, including
  role-based corner/layer/elevation tokens, pressed feedback, interface-only
  punctuation and immediate dialog expansion without dimension transitions.
- System/Light/Dark controls, local IBM Plex fonts and Tabler assets are present.
- Offline evaluation report comparison is implemented in framework, CLI, example
  consumer and Studio. It is no longer an optional unimplemented next task.
- The 1 November browser checkpoint records local Chromium checks for evaluator,
  experiment, import/export review, diagnostics, standalone viewers and diagrams
  across phone/tablet/desktop widths and both palettes. Treat preview/review
  evidence as such; it is not proof of every final save, real-device permission
  or installed-platform flow.

## Fresh verification in this follow-up

All 20 paths in `STUDIO-BROWSER-CHECKS-CHECKPOINT.json` matched their recorded
SHA-256 values before the documentation changes in this follow-up. The app/test
code was unchanged; historical checkpoint hashes are not rewritten to hide later
documentation changes. Browser and Lighthouse claims are reused recorded evidence,
not fresh measurements by this verification pass.

- `node --test tests/studio_*.test.cjs`: 289 passed, zero failures/skips.
- Focused source-map, completion-audit, requirements, catalog and evaluation-gate
  Python suite: 43 passed, zero failures/skips.
- Normal completion audit: exit 0, 135 accepted flags, no aggregate claim
  inconsistencies, `plan_complete: false`.
- `--require-complete`: expected exit 1 because delivery remains open. This is
  not a failed implementation regression or a certificate of behavioral parity.

## Remaining delivery gates

| Gate | Remaining acceptance | Prerequisite/boundary |
| --- | --- | --- |
| Real devices and assistive technology | Physical phone/tablet microphone permission, recording, playback/stop, touch, screen-reader use and installed/offline web-app behavior. | Requires the actual target devices and assistive technology. Emulated widths and automated accessibility scores do not close this gate. |
| Selected live integrations | Account authentication/permissions, selected API voice and embeddings, provider quality/billing paths, real SQL Server and Oracle server contracts. | Requires selected configured accounts or server instances. Existing local databases/voice and selected GitHub proof keep their own scope. Do not silently enable another provider or make paid calls. |
| Recovered-host/full-machine operation | Recovered-host browser/login, ingress, daemon and OS/power-loss recovery. | VM work remains deferred. A selected-service or encrypted-copy restore is not full-machine acceptance. |
| Desktop milestone 9, last | Another physical Mac; installed Linux/Windows authentication, ownership and lifecycle; Windows signing; genuine older-release/workspace upgrades. | Mac 1.0.2 is already signed/notarized but lacks later fixes. New desktop builds require an explicit request; public release/update-feed publication is withheld. |

No additional missing product implementation is established by this focused
verification. That is narrower than proving no undiscovered defects exist.
The public update-feed decision is a release choice, not permission to publish.

## Evidence

- [Current implementation plan](STUDIO-NEXT-AGENT-PLAN.md)
- [Verification checkpoint](STUDIO-DELIVERY-STATUS-CHECKPOINT.json)
- [Recorded broader browser checks](STUDIO-BROWSER-CHECKS-CHECKPOINT.json)
- [Frozen coverage reconciliation](STUDIO-COVERAGE-RECONCILIATION.md)

No product code, example behavior, credential, VM, installer or public release
changed in this follow-up. Do not rebuild completed UI fixes from historical
audit findings; continue with an available remaining acceptance environment.

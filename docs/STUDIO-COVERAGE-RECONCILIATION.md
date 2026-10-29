# Studio delivery evidence reconciliation — 29 October 2026

## Decision

The original feature inventory has scoped local acceptance. The entire delivery
plan is **not complete**. Milestones 5 and 6 have local implementation/fixture
evidence; neither milestone needs to be rebuilt merely because target-account or
deployment acceptance remains open. Recent evaluation work extends that baseline.

This audit checks recorded claims, source mappings, evidence-file existence and
hashes. It does not rerun every adapter, certify runtime equivalence or establish
quality with real models. The new machine-readable companion,
`STUDIO-COVERAGE-RECONCILIATION.json`, retains every requirement and catalog
identity, its recorded status, and its evidence references. File hashes identify
this checkout's evidence, including uncommitted changes; they do not authenticate
test results or imply that these files were shipped.

## Frozen baseline

- 135 acceptance flags are true: 119 records say `fixture-verified`, 12 say
  `implemented`, and four are explicit exclusions. The 12 implemented records
  describe architecture, scope and delivery constraints, not 12 missing adapters.
- All 31 catalog identities remain present: 27 `supported-native-reviewed` entries
  and four platform-product exclusions. Alias entries remain individually listed;
  27 entries do not mean 27 independent implementations.
- The original inventory has zero `configured` and zero `live_verified` flags.
  Those flags were not promoted from newer selected-environment checkpoints.
  Separate checkpoints record actual local databases, voice runtimes, VM GitHub
  reads and other scoped acceptance. Zero inventory flags does not erase that
  evidence; equally, that evidence does not certify every integration account.
- Milestone clause/exit totals remain 6/6, 8/8, 8/8, 7/7, 10/10, 9/9, 10/10 and
  9/9 for milestones 1–8. Added milestones 10–12 retain their separate local
  gates. Desktop milestone 9 remains open and last.

Frozen versions, catalog commits, identities, statuses and acceptance flags were
not changed. No source from a later reference release was added to this target.
The repository review remains pinned to commit
`6edcf30e1168f526458b7318f199674a45659e3b`; it is not a fresh upstream inspection.

## Stale statements and their current interpretation

Earlier dated checkpoints remain historical evidence. The following statements
must not become fresh implementation tasks:

| Earlier statement | Current evidence and interpretation |
| --- | --- |
| `DELIVERY-01` says milestones 7 and 8 remain partial; `DELIVERY-03` says no feature gate is complete. | Milestone 7's final checkpoint and milestone 8's exit checkpoint have local completion flags. Whole-plan completion remains false. The observations are stale; their scope/delivery requirements have not changed. |
| Several catalog observations say full entry-specific acceptance, gateway files, CLI final acceptance or all live database contracts are pending. | The 31-row catalog invocation audit records 27 supported native reviews and four exclusions. Its checked evidence defines local scope. Account acceptance stays separate. Milestone 6 records actual local PostgreSQL/MySQL/MariaDB/MongoDB checks; SQL Server/Oracle server behavior remains unverified. |
| Desktop's initial pending list says Developer ID, notarization and a full-profile installed macOS app are missing. | `STUDIO-DESKTOP-SIGNED-RELEASE-CHECKPOINT.json` records signed, notarized, stapled 1.0.2 and installed local verification. Other Macs, installed Linux/Windows, Windows signing and genuine prior-release migration remain open. |
| The signed-release source note says only a producer packing fix differs from current source. | That statement describes its creation time. A fresh manifest comparison finds 19 changed pinned paths and 11 additional Python/JavaScript/CSS/HTML source files. All 668 unique pinned paths still exist. The old release is not a package of the current source tree. |
| Worker/import notes say multiple evaluators and external evaluation export are missing. | Later evaluator-set, experiment-export and evaluation-export checkpoints supersede those next-step notes. Their bounded profiles and strict rejection rules still apply. |
| The first repository comparison says evaluation execution is sequential and Apple signing prerequisites are missing. | Bounded agent experiments now support 1–4 workers and 1–4 copied evaluators. Workflow-target experiments still require one worker. The signed macOS checkpoint resolves local Apple prerequisites. |
| Historical VM notes request another signed-in run or imply all recovery is complete. | The final-target checkpoint records authenticated operation and an independent selected-service restore. Recovered-host browser/OAuth and OS/daemon/power-loss disaster recovery remain open. VM work is currently deferred by the user. |

The existing completion audit correctly reports zero **aggregate** inconsistencies
and `plan_complete: false`. It does not inspect prose for these stale statements.
Its result must never be described as zero behavioral gaps.

## Recent deliveries mapped to the plan

These are scoped extensions, not newly accepted frozen flags. Their checkpoint
test totals overlap and must not be added together.

| Delivery | Related frozen requirements | Evidence and boundary |
| --- | --- | --- |
| Inline AI dataset drafting | M05-06, M08-05, M08-06 | `STUDIO-DATASET-DRAFTING-CHECKPOINT.json`; explicit provider, editable review and separate save. Real model quality remains unverified. |
| Input/reference file roles | M02-04, M08-05, M08-06 | `STUDIO-EVALUATION-FILES-CHECKPOINT.json`; pinned bounded extracted text, judge-only references. Original multimodal inputs are outside this profile. |
| Private shared-snapshot continuation | M08-03, M08-04 | `STUDIO-SNAPSHOT-CONTINUATION-CHECKPOINT.json`; authenticated private copy, grants and idempotent explicit continuation. No source-owner authority transfers. |
| Skill and OpenAPI connector/tool bindings | M05-05, M05-09, M06-02 | Skill-reference and connector-node import checkpoints; explicit bindings and reviewed activation. External deployment directives remain rejected. |
| Dataset/judge and experiment imports | M05-05, M08-06 | Evaluation-package and experiment-definition import checkpoints; explicit files/revisions, conversion review and native plan/apply. Saving starts no AI. |
| Parallel agent experiments and copied evaluators | M08-06, M08-07 | Worker/evaluator checkpoints; 1–4 workers and judges, all-evaluator policy, one target invocation per slot, durable no-replay receipts. Parallel workflow pause controls and other acceptance policies are not supported. |
| Offline evaluation report gate | M08-06 | `STUDIO-EVALUATION-GATE-CHECKPOINT.json`; recomputes complete report evidence without providers. It does not authenticate report authorship. |
| Experiment declaration/handoff export | M05-05, M08-06 | `STUDIO-EXPERIMENT-EXPORT-CHECKPOINT.json`; reference-only export with exact dependency pins. It does not embed agents, workflows or dataset contents. |
| Dataset/CSV/judge package export | M05-05, M08-06 | `STUDIO-EVALUATION-EXPORT-CHECKPOINT.json`; supported text/template data and native settings survive fresh-workspace import. Unsupported checks, context/file roles and nonportable settings reject without partial files. |

## Remaining delivery gates, in order

1. **Recent browser acceptance.** Exercise the new experiment/evaluator/import and
   export controls at phone, tablet and desktop widths, including keyboard use,
   review/save, diagnostics and explicit downloads. Interface-logic tests are
   available; visual acceptance remains pending. The earlier local-origin access
   denial must not be retried or bypassed without a newly authorized path.
2. **Selected accounts and real devices.** Verify chosen providers' permissions,
   API voice/embedding behavior and model quality. On a physical phone/tablet,
   verify microphone permissions, recording/playback/stop, touch/accessibility and
   installation. No credentials, paid calls or external mutations were used in
   this audit. Each adapter keeps its own live scope; catalog visibility is not
   configuration or successful account authentication.
3. **Recovered-host operation.** Finish distinct-host browser/OAuth, OS/ingress and
   daemon/power-loss recovery. Earlier service-copy restore evidence remains
   valid within its recorded scope. All VM work stays deferred.
4. **Desktop delivery, last.** Test another physical Mac and installed Linux/Windows
   lifecycle, sign Windows delivery, and test a genuine prior release/workspace
   schema upgrade. Current rollback evidence uses synthetic prior-version metadata
   on candidate code. The signed 1.0.2 Mac release remains local. Public feed
   publication/native-menu verification is withheld by user choice, not a reason
   to publish automatically. Later source changes require a separately requested
   build; this audit does not rebuild or repackage anything.

No additional missing implementation is established by this records-only audit.
That is narrower than proof that no implementation gaps exist. Before another
product slice, resolve an available acceptance path above. If those environments
remain unavailable, an offline before/after evaluation-report comparison is a
separate useful extension, not an unmet frozen requirement. It should require
compatible case/variant identities and expose removed, missing or nonterminal
evidence rather than presenting it as an improvement. It must never run providers
or lower saved thresholds automatically.

## Validation performed for this audit

The focused inventory/source-map/catalog/completion suite passes 16 tests with
zero failures or skips:

```sh
.venv/bin/pytest -o addopts='' -q tests/test_studio_completion_audit.py tests/test_studio_requirements.py tests/test_studio_source_map.py tests/test_studio_catalog_closure.py
.venv/bin/python tests/studio_completion_audit.py
.venv/bin/python tests/studio_completion_audit.py --require-complete
```

The normal audit exits 0 with 135 accepted flags, no aggregate inconsistencies and
`plan_complete: false`. `--require-complete` must exit 1; that is the expected
open-delivery result, not a failed implementation regression. Evidence-path and
hash checks, release-asset checks and their exact counts are in the companion.
These checks do not rerun earlier checkpoint test counts. No browser, VM,
provider, release upload, new installation or DMG build was performed.

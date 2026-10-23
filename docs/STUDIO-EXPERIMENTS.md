# Experiment Lab

Open **Experiment Lab** from the menu or `/#experiments`. Create a named experiment
using an evaluation suite revision, one to four provider/model variants, runs per
case and a minimum pass rate. Saving never runs cases. The editor shows planned
case executions before an explicit run.

Each variant must meet the threshold independently. With two variants, two repeats
and one case, four case executions run. If Baseline passes twice and Weak fails
twice, a 50% threshold rejects the experiment: Weak did not meet its own threshold.
A strong variant cannot hide a regression in another variant.

Runs per case range from 1–20. Maximum 400 planned case executions are allowed at
launch. Judges and workflow steps can add provider calls and tool effects beyond
that count. Variants run sequentially, followed by cases and repetitions. Runtime
errors stop the experiment; cancellation uses existing job controls. Repetitions
are deliberate executions, not automatic retries.

## Revisions and safety

Definitions use existing workspace catalog history, optimistic edits,
archive/restore, JSON export and native configuration releases. Project members
can read definitions; builders/owners can edit; operators can run them through
existing run permissions. Reports retain existing account/owner visibility.

An experiment pins its evaluation suite revision. If that suite changes, review
and update the experiment before launching again. No silent repinning occurs.
At queue time, the server snapshots experiment configuration, cases, variants,
repeats and thresholds. Later catalog edits or archival do not alter queued cases.
Existing target-definition and external-dependency policies remain in force;
this does not freeze every external tool, model or target definition.

Workflow repetitions receive distinct checkpoint identities. Named experiments
accept non-interactive workflows. Approval/input gates, including named children,
are rejected before dispatch because evaluation checkpoints do not support the
interactive workflow resume path. Use the existing workflow runner for those
workflows. Experiments never bypass approval or repeat an unconfirmed effect.

## Reports

Reports show planned and recorded executions, per-variant counts, repetition
numbers and threshold acceptance. A fully graded rejection fails the run but
preserves the complete report. Runtime errors and interrupted/cancelled runs never
establish acceptance from partial results. Workspace operational summaries
distinguish a fully graded threshold rejection from incomplete execution.

Existing ad-hoc suite/variant comparisons retain one execution per case and their
previous non-enforcing run behavior. Old report records remain readable. Storage
is additive and requires no migration. Configuration and Quick Build use the
shared catalog validators and dependency checks.

The [API contract](../postman/contracts/studio-experiments.md) defines resource
fields, limits, launch requests and report additions.

## Local evidence

- Synthetic provider checks cover repetition ordering, independent thresholds,
  partial provider failure, cancellation and immutable queued configuration.
- Local workflow checks verify distinct repetition checkpoint paths and reject
  interactive gates before dispatch. Catalog/configuration/build compatibility,
  permissions and private report visibility use existing services.
- Frontend checks cover pinned revision selection, execution estimates, form
  serialization, report acceptance states and named launches through `/api/runs`.
- `tests/studio_experiments_acceptance.py` starts a disposable full Studio. Sign in
  as synthetic Alice, edit Comparison experiment, add Weak with model override
  `weak`, save and run. Both variants repeat twice; the local injected provider
  deliberately fails Weak. Saved Postman assertions verify definition and report.
- Browser acceptance covers editing, planned count changes, threshold rejection,
  report detail and 390px/820px layouts. The framework consumer is
  `examples/nexus-bot/src/studio_datasets.py`.

No live provider account is contacted by these checks. Personal-VM, physical-device
and live provider validation remain separate. Interactive experiment workflows
need a future resume integration; normal workflow approval behavior is unchanged.

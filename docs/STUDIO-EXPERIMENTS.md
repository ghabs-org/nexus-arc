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
repeats, thresholds, workflow definitions, nested workflows and runtime limits.
Later catalog edits or archival do not alter queued cases or paused workflow steps.
Existing target-definition and external-dependency policies remain in force;
this does not freeze every external tool or model.

Workflow repetitions receive distinct checkpoint identities. Approval and input
steps pause the current case, including steps inside maps and named workflows.
Open the run in **Activity** to inspect the current variant, case and repetition.
Use **Approve and continue** or **Provide input** to resume that case, then the
remaining cases. The review inbox also supports approval decisions. Invalid typed
input leaves the case waiting. Rejecting or stopping ends the experiment and
runs configured cancellation cleanup; cleanup steps can also request review.

Each repetition requires its own decisions. Old buttons and replies cannot approve
a later pause. Completed steps and graded case results survive continuation and
file/SQL service restarts. Experiments never bypass approval or automatically
repeat an unconfirmed effect. Runtime errors still stop execution for inspection.

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
- Local workflow checks verify distinct repetition checkpoint paths, approval and
  typed-input continuation, nested map gates, file/SQL restarts, reviewed cleanup,
  stale decisions and continuation while a previous worker is still finishing.
  Catalog/configuration/build compatibility,
  permissions and private report visibility use existing services.
- Frontend checks cover pinned revision selection, execution estimates, form
  serialization, report acceptance states and named launches through `/api/runs`.
- `tests/studio_experiments_acceptance.py` starts a disposable full Studio. Sign in
  as synthetic Alice, edit Comparison experiment, add Weak with model override
  `weak`, save and run. Both variants repeat twice; the local injected provider
  deliberately fails Weak. Saved Postman assertions verify definition and report.
- Browser acceptance covers editing, planned count changes, threshold rejection,
  report detail and 390px/820px layouts. The framework consumer is
  `examples/nexus-bot/src/studio_datasets.py`. An interactive consumer is
  `examples/nexus-bot/src/studio_experiment_review.py`.

No live provider account is contacted by these checks. Personal-VM, physical-device
and live provider validation remain separate. Existing non-experiment workflow
approval and input requests retain their previous formats.

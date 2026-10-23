# Saved Experiment Lab runs

`/api/catalog/experiments` uses existing workspace catalog permissions, optimistic
revisions, history and archive/restore. Authenticated project members can read;
builders/owners can save. Saving, importing or inspecting never executes cases.

Canonical resource data:

```json
{"kind":"experiment","version":1,"evaluation_id":"policy-checks","evaluation_revision":1,"runsPerExample":3,"passThreshold":1,"variants":[{"name":"Baseline","provider":"codex","model":""}]}
```

The suite ID and positive integer revision are required. `runsPerExample` is an
integer from 1–20, default 1. `passThreshold` is finite from 0–1, default 1. There
are 1–4 uniquely named variants using existing provider/model/effort fields.
Unknown fields, unsupported versions, booleans as numbers and invalid providers
are rejected. Model overrides are at most 200 characters.

Launch through existing `POST /api/runs`:
`{"kind":"evaluation","experiment_id":"my-experiment"}`.
It requires existing operator-or-higher run permission and CSRF. Suite or variant
overrides are rejected for named experiments. The active suite must still match
the pinned revision; edits require explicit review/update of the experiment.
Maximum 400 planned case executions = cases × variants × runsPerExample.
Judges, workflow steps and tools can add provider calls/effects beyond that count.

Before queuing, the server snapshots the experiment, suite cases, variants,
repeat count and threshold. Caller-supplied snapshots/settings are overwritten.
Later edits do not change the queued cases or grading configuration. Existing
target-definition, authorization, activation and job recovery policies still apply.
This does not freeze every external provider/tool dependency.

Execution is sequential: variant, case, repetition. Repetitions receive distinct
workflow checkpoint identities; they are deliberate executions, not retries.
Each repeat can run workflow tools. Workflows containing approval/input gates
(including named children) are rejected before dispatch; interactive workflow
execution remains in the existing workflow runner. Runtime
errors stop immediately; cancellation follows existing job behavior. No automatic
retries, provider fallback or new scheduling/execution service is added.

Reports retain existing fields plus `experiment_id`, `experiment_revision`,
`experiment_name`, `runs_per_example`, `pass_threshold`, `expected_total`,
`complete`, `acceptance`, `evaluation_status` and per-variant summaries. Each case
entry includes repetition and variant index. Partial/error reports have
`complete:false` and `acceptance:null`. Cancellation/interruption is authoritative
in the source run; incomplete reports never establish a pass.

On full completion, every variant must meet the threshold independently, using
passed repetitions divided by planned repetitions across all its cases. A strong
variant cannot hide a weak one. A rejected threshold writes a complete failed
report and fails the existing run with a readable error. No-results/partial runs
cannot pass. Existing ad-hoc suite/variant runs remain one execution per case and
retain their previous non-enforcing run behavior when case checks fail.

Native configuration validate/plan/apply/export and Quick Build share the new
catalog validator. No state migration is needed. Existing records and reports
without these optional fields remain readable.

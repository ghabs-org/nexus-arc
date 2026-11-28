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
`maxWorkers` is an integer from 1–4, default 1. More than one worker is supported
for agent targets only; workflow suites require one worker because their approval
and input controls address one current case. This restriction is validated before
queueing, without silently reducing a requested worker count. Parallel work uses
the existing parent job lease and authorization, not a separate execution service.
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

With one worker, execution is sequential: variant, case, repetition. Parallel
agent execution dispatches slots in that order with at most maxWorkers target/judge
pairs in flight. A slot holds its worker through grading; judge calls do not add
another worker. Results remain sorted by variant, case and repetition, even when
completion order differs. Invocation traces identify their case checkpoint.
Provider text streams do not overwrite the parent report during parallel work.
Repetitions receive distinct
workflow checkpoint identities; they are deliberate executions, not retries.
Each repeat can run workflow tools. Approval/input gates pause the parent
evaluation run without grading the unfinished case or dispatching later cases.
Existing run approval and input operations resume its current case checkpoint,
then continue the remaining repetitions and variants. Completed cases and completed
workflow nodes are retained across continuation and file/SQL service restarts.
Each repetition requires its own decisions; approval never grants future repeats.
The execution view and review inbox show the current case's saved workflow.
Input schemas, ownership, execution permission and duplicate-decision checks remain
those of the workflow runner. Invalid input leaves the case waiting. Rejection
or cancellation stops the experiment; configured cleanup handlers still apply.
An incomplete report always has `complete:false` and `acceptance:null`.
Workflow definitions, nested definitions and limits are pinned before dispatch;
later edits do not change queued or paused cases. Runtime
errors stop new dispatches; already-started parallel slots drain and save their
outcomes. Cancellation cancels owned workers and waits for their cleanup; external
effects may still complete. Each parallel slot has a durable dispatch receipt
written before invocation. Completed results survive shutdown/restart. Interrupted
or uncertain dispatched slots are never automatically replayed or accepted as
passed, including on accidental reentry. A fresh experiment run is a deliberate
new execution. Partial reports retain dispatched slot ordinals for inspection.
No automatic
retries, provider fallback or new scheduling/execution service is added.

Waiting experiment runs also expose an opaque `evaluation_checkpoint` token.
Send it unchanged in `POST /api/runs/{id}/resume` alongside `decision`, or in
`POST /api/studio/runs/{id}/input` alongside `values`. It binds the decision to that pause.
Missing or stale tokens return a validation error without changing the checkpoint.
Refresh run details before retrying. Ordinary workflow requests remain compatible
and do not require this field. The review inbox uses its existing digest and
confirmation flow, with this token bound to the reviewed run.

Reports retain existing fields plus `experiment_id`, `experiment_revision`,
`experiment_name`, `runs_per_example`, `pass_threshold`, `expected_total`,
`complete`, `acceptance`, `evaluation_status` and per-variant summaries. Each case
entry includes repetition and variant index. Parallel reports also record
`max_workers` and zero-based `dispatched_slots`. Partial/error reports have
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

Optional copied evaluator sets retain primary identity, require explicit all-
evaluator acceptance, and add individual grading results. See
`studio-experiment-evaluators.md`. Existing experiments without that selection
retain inline case-judge behavior. Grading progress persists after each judge;
unfinished `pending_grading` evidence never counts as a completed case.

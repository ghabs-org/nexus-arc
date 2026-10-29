# Parallel agent experiments

Experiment Lab's saved experiment editor now includes **Parallel workers**. Set
an integer from 1–4. Existing saved experiments without `maxWorkers` continue to
use one worker. The declaration importer also accepts and preserves `maxWorkers`
from 1–4. Saving and importing still start no AI execution.

A worker executes one case/repetition for one variant, including its judge call,
before taking another slot. The case × repetition × variant limit remains 400;
parallelism does not reduce total planned executions or model usage. Selected
subscription/API providers retain their ordinary billing and service limits.
Two workers permit at most two target/judge pairs in flight in this experiment.
Multiple independent Studio jobs retain their existing scheduling behavior.

Slots dispatch in variant, case, repetition order. Completion can occur in any
order; saved report results retain canonical variant, case, repetition order.
Each variant must independently meet its pass threshold. Invocation traces link
both target and judge to the slot's case checkpoint. Parallel providers use final
answers rather than competing to write the parent's single text stream. Activity
shows execution events, and the report shows configured worker count.

Parallel work currently supports **agent targets**. Workflow-target experiments
must use one worker, preserving their existing approval/input continuation and
cleanup rules. A higher worker count for a workflow is rejected before queueing;
it is never silently reduced. Parallel workflow approvals would need controls
that address several concurrent paused cases and are outside this change.

Every slot writes a dispatch receipt before calling its target. The report's
zero-based `dispatched_slots` identifies attempted slots, including attempts with
no final graded result. Results persist as each slot finishes. Provider/runtime
errors stop new dispatches while already-started slots finish and retain their
outcomes. Failed or partial reports never establish acceptance.

Cancel stops new dispatch and cancels owned workers. Studio waits for their
cleanup before releasing the parent job; already-started external effects may
still complete. Shutdown/interruption leaves completed results and dispatch
receipts intact. SQL workers inherit the existing parent job lease and fencing;
there is no second scheduler, job queue or authorization service.

Restart never automatically repeats a dispatched target or judge. Accidental
executor reentry refuses an interrupted/failed dispatch rather than treating a
missing result as a retry instruction. Inspect the partial report before starting
a fresh experiment run: a new run deliberately repeats the experiment and can
incur usage or external effects again. This change does not add a generic retry
button for uncertain agent/tool effects.

Existing records need no migration. Configuration export includes the additive
`maxWorkers` setting; older runtimes that reject unknown fields need an update
before consuming those configurations. Existing single-worker workflow and
ad-hoc evaluation behavior remains covered by regression tests.

The framework runner is `nexus/core/studio_evaluation_workers.py`, consuming the
existing evaluator, report store and job lifecycle. The thin executable example
is `examples/nexus-bot/src/studio_experiment_workers.py`. It runs six synthetic
cases with two workers, without any external model account. Contracts remain in
`postman/contracts/studio-experiments.md` and
`postman/contracts/studio-experiment-import.md`.

Local tests cover worker bounds, out-of-order completion, target/reference
isolation, judge ownership of the same worker, source pinning, HTTP permissions,
failure draining, cancellation, file/SQL restart and no uncertain replay. Visual
browser acceptance and account-dependent provider concurrency remain pending.
No VM work, desktop rebuild or release publication is part of this slice.

# Grade one answer with multiple evaluators

In Experiment Lab, edit a saved experiment and choose up to four evaluators.
Choose the primary evaluator explicitly. Selecting evaluators copies their saved
settings and revisions into the experiment; later catalog edits do not change
those copies. Leaving all selections empty keeps each case's existing judge.
Selected evaluator copies replace inline case judges for this experiment.

Every selected evaluator must pass the case, together with its existing phrase
and structured checks. The primary evaluator labels reports; it does not override
another judge's failure. Each variant must still meet the explicit experiment
pass-rate threshold. Scores are shown separately; acceptance never averages
judges together. The supported policy is explicitly `all`.

One case execution runs the target once. Each selected judge grades that same
answer in order, within the case's worker. Four evaluators can add four model
calls per target execution, in addition to the target's own tools and model calls.
Saving, importing, inspecting and exporting never start AI. Running uses the
existing subscription/API configuration and account billing.

Reports show each evaluator's name, copied revision, primary marker, pass count
and mean score. Case details retain individual checks and reasons, including
choice labels. Existing case checks remain visible. Task reference answers and
grading-reference files continue to reach judges only, not the target.

If a judge fails or returns invalid evidence, later judges for that case stop.
Completed grades and the failing judge remain inspectable; new case dispatch
stops while already-started parallel cases drain normally. An invalid result
cannot become acceptance through a zero experiment threshold.

The report saves the target answer and completed grades after each judge. Cancel
or shutdown can leave **Unfinished grading** details, without counting that case
as complete. Existing durable dispatch receipts prevent automatic replay of a
partly graded target or judge. Starting a new experiment run deliberately repeats
its executions; it is not recovery of uncertain external effects.

Exported report checking validates the evaluator manifest, primary ID, individual
identities/revisions, complete grader set, scores, pass outcomes and matching flat
case checks. Missing, duplicate or contradictory evidence rejects. This checks
recorded consistency, not cryptographic authenticity or model quality. Reports
without the new evaluator fields retain their previous behavior.

Declaration import supports multiple source evaluator aliases. Expand **Bind
multiple evaluators**, enter every `evaluatorIds` alias exactly, and map each to
a saved evaluator revision. The first alias identifies the judge selected above
that section; the source's `primaryEvaluatorId` determines which selected judge
is primary. Missing/extra aliases, duplicate native judge IDs and stale revisions
reject. Legacy single-evaluator import remains supported.

Conversion reports the native all-evaluator acceptance adaptation before save.
Review the converted resources and save plan explicitly. The copied suite keeps
the primary inline judge for ordinary suite consumers; named experiment execution
uses all its copied evaluator settings. Native configuration export includes the
complete copied set and policy. [Reviewed declaration export](STUDIO-EXPERIMENT-EXPORT.md)
now provides a companion preserving native settings and exact dependency pins;
it is not a self-contained dataset/evaluator package.

These are additive optional configuration/report fields; no persisted-record
migration is needed. Older runtimes need an update before consuming configurations
with evaluator sets. Agent worker limits stay 1–4. Workflow experiments stay at
one worker, preserving their existing approval/input continuation.

The framework implementation is `nexus/core/studio_experiment_judges.py`, using
existing judge validation, execution, jobs and report storage. The thin consumer
`examples/nexus-bot/src/studio_experiment_evaluators.py` runs two answers through
two judges with a synthetic provider: six invocations, no external service.
The API contract is `postman/contracts/studio-experiment-evaluators.md`.

Visual browser acceptance and account-dependent grading quality/concurrency remain
pending. No VM update, desktop rebuild or release publication occurred.

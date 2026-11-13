# Multiple experiment evaluators

Saved experiment data optionally includes all three fields: `evaluators`,
`primaryEvaluatorId`, `evaluatorPolicy`. `evaluators` contains 1–4 distinct copied
definitions `{id, revision, name, judge}`. IDs are native resource IDs; revision is
a positive integer, name is bounded text, and judge uses the existing validated
judge schema. These are reviewed configuration copies, not live resource lookups
or proof of provenance. `primaryEvaluatorId` must match a copied ID.
`evaluatorPolicy` must explicitly equal `all`. No field silently enables another
policy. With these fields absent, existing inline case judges behave unchanged.

Saving, inspecting, importing and exporting start no model run. Queue-time
preparation overwrites caller-supplied settings and snapshots copied evaluators,
including reference/template requirements, before dispatch. Existing owner/builder
catalog permissions, CSRF, resource revisions and run authorization apply.

Each case/repetition/variant invokes its target once. Copied evaluators grade
that same answer sequentially within the case's worker. All case checks and all
selected judges must pass for the case to pass. Each variant must still meet its
explicit experiment pass threshold. The primary evaluator labels reports; it
does not override other evaluator failures. There is no score averaging across
evaluators, provider fallback, automatic retry or repeat of the target for grading.
At most four judge calls are planned per target execution, plus any target tools.

Runtime failure or invalid evidence stops later graders for that case and new
case dispatch; existing parallel slots drain normally. Completed grader results
and the failing grader remain in the partial report. A zero pass threshold cannot
turn incomplete/errored evidence into acceptance. Cancellation/interruption retains
the existing durable whole-slot no-replay receipt.

Reports add an evaluator manifest (`id`, `revision`, `name`), primary ID, policy,
per-case `evaluators` with individual checks and outcomes, and per-evaluator
summaries. Flat case checks include `evaluator_id` on judge checks. Offline report
gating validates the complete matching grader set and flat/nested consistency;
missing, duplicated or contradictory evidence cannot pass. Existing reports
without the manifest keep their previous format and gate behavior.

Declaration import accepts `bindings.evaluators` mapping every declared alias to
an exact existing `{id, revision}`, with no missing/extra aliases. Legacy singular
`bindings.evaluator` remains accepted for a single alias. It explicitly reports
the native all-evaluator acceptance adaptation, retains primary identity and
requires the existing conversion/save review. No source paths or URLs are read.

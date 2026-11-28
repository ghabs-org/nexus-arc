# Reviewed experiment-definition import

`POST /api/studio/configuration/experiments/convert` requires existing workspace
owner authorization and CSRF. Request fields are `experiment` (resource object or
bounded YAML/JSON string), `bindings`, explicit `pass_threshold`, and optional native
`variants`. There are no filesystem paths, file discovery or provider calls.

The resource accepts `kind: experiment`, `name`, optional `description`, and spec
fields `datasetId`, `targetAgent`, `evaluatorIds`, `primaryEvaluatorId`, optional
`runsPerExample` (default 1) and `maxWorkers` (default 1). This bounded profile
requires 1–4 distinct evaluators including primaryEvaluatorId. `maxWorkers` accepts integers
1–4 for the explicitly bound agent target, using the existing native executor.
Booleans, noninteger counts, duplicate/missing evaluators, unsupported fields and worker
counts outside that range reject. Worker concurrency is never silently discarded.

Explicit bindings contain exactly `dataset: {id, revision}`,
`evaluators: {sourceAlias: {id, revision}, ...}`, `target: {id, revision}`. Every
declared evaluator alias needs exactly one binding. Legacy singular `evaluator`
remains accepted for one declared evaluator. Dataset/evaluator revisions
are positive integers; target revision is its current definition SHA-256. All
resources must currently exist, be unarchived and match the selected revisions.
Bindings map external aliases to explicitly selected native resources. Binding
several aliases to the same evaluator rejects rather than duplicating billing.

Success returns `200 {document, source, adaptations, planned_executions}`.
The native document contains a fresh evaluation suite plus an experiment pinned
to suite revision 1. Dataset cases are copied; only their judge settings are
replaced by the selected evaluators at execution. The suite's inline judge keeps
the copied primary for existing suite consumers. The experiment additionally
contains independent evaluator copies, native primary ID and explicit `all`
policy. This is a reviewed native acceptance adaptation: primary labels results;
every selected judge must pass the case. Existing checks, expected responses,
file roles and context stay intact. No source resource is updated. A fresh suite
per conversion avoids rewriting a prior imported suite or guessing its next
revision. The experiment uses normal native variants, repeats and pass threshold;
total cases × variants × repeats must not exceed 400.

Conversion creates no resources, plans or runs. Existing native plan/apply provides
review and explicit save. Source dataset/evaluator copies are independent afterward;
target definitions and execution dependencies follow existing run-time pinning.
Neither a binding nor import grants private artifact access or activates an agent.
Native export retains canonical data. External-format experiment export and
alternative evaluator acceptance policies remain outside this profile. See
`studio-experiment-evaluators.md` for grading and offline evidence requirements.

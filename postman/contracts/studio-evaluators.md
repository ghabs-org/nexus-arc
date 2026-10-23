# Reusable evaluators and choice scoring

Evaluators use `/api/catalog/evaluators` with existing catalog save, read, history
and archive/restore operations. Team reads require project access; writes require
builder or owner access. Evaluators are shared within the current workspace like
evaluation suites and datasets. Existing CSRF and optimistic revision rules apply.

Canonical resource `data`:

```json
{
  "kind": "evaluator",
  "version": 1,
  "type": "llm_judge",
  "provider": "codex",
  "model": "",
  "rubric": "Assess whether the answer is supported by the reference passages.",
  "threshold": 0.7,
  "choices": {"Supported": 1, "Partial": 0.5, "Unsupported": 0}
}
```

`kind`, `version` and `type` default to the displayed values when omitted. Unknown
fields, unsupported versions/types, non-finite scores and boolean scores fail
validation. Provider is explicit. Existing model/rubric limits apply. Threshold
is a finite number from 0 through 1. Optional `choices` contains 2–20 unique,
nonempty labels, each at most 100 UTF-8 bytes without surrounding whitespace or control characters,
mapped to finite scores from 0 through 1.

Saving, inspecting, exporting or applying an evaluator to a draft makes no model
request. Studio explicitly copies provider/model/rubric/threshold/choices into all
current draft cases; phrase checks, structured checks and references stay intact.
The source revision is shown in the receipt. Later evaluator edits or archival do
not change saved suites/datasets or queued suite snapshots. There are no live
evaluator references or new execution services in this slice.

The existing case `judge` object accepts optional `choices`. Without choices,
legacy rubric judges still expect `{"score":0.8,"reason":"..."}`. With choices,
the configured provider must return `{"choice":"Supported","reason":"..."}`.
The configured map determines the score; provider-supplied scores are rejected.
Unknown/missing labels, duplicate fields, malformed answers and missing reasons fail the check.
Pass means mapped score >= threshold. Reports retain label, mapped score,
threshold, reason, provider and model. Invalid answers include an explicit error;
they never switch provider, retry or fall back to numeric scoring.

Judging runs only when a saved evaluation is explicitly executed. It sends task,
answer, rubric and selected reference passages to the selected provider and uses
that account's usage. Existing provider errors stop evaluation through the current
runner. These model grades are estimates, not independent correctness guarantees.

Existing inline judges, datasets, run routes and report fields remain readable.
Native configuration validate/plan/apply/export and Quick Build use the same
catalog validators. Additive storage needs no migration. Named repeated
experiments remain separate planned work.

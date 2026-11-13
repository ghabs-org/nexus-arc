# Reusable AI evaluators

Open **Library → Evaluators** or `/#evaluators`. Save grading instructions, an
explicit provider/model and a pass threshold from zero through one. Optional
choice labels map to fixed scores, one per line:

```text
Supported = 1
Partial = 0.5
Unsupported = 0
```

Without choices, the existing numeric rubric judge still works. With choices,
the provider selects one exact label. The saved mapping determines the score;
the provider cannot override it. A score at or above the threshold passes.
Unknown labels, missing reasons and malformed answers fail the check. Provider
errors use the existing evaluation failure flow without retries or fallback.

Evaluator definitions use existing catalog revision history, optimistic edits,
archive/restore, configuration releases and JSON export. They are shared with
the current workspace. Authenticated project readers can inspect them; builders
and owners can edit. Saving or inspecting an evaluator never invokes AI.

## Apply grading instructions

Edit a dataset or evaluation suite and expand **Apply a saved evaluator**. Select
an evaluator, then choose **Replace AI judges in all draft cases**. Review the
copied provider, rubric, threshold and choices before saving. Existing phrase
checks, structured checks and reference passages remain intact.

The receipt displays the copied revision. This is explicit copying, not a live
reference: later evaluator edits or archival never change a saved dataset, suite
or queued suite snapshot. Adding new cases afterward does not automatically apply
the evaluator to those cases; apply again explicitly if needed.

Judging runs only when the saved evaluation is explicitly executed. The selected
provider receives the task, answer, rubric and selected reference passages and
uses that account's usage. Model grades are estimates. Reports show the selected
choice, mapped score, threshold, reason and invalid-answer errors.

## Compatibility and evidence

Storage is additive; no state migration is needed. Existing inline numeric judges
and reports remain supported. Native configuration validate/plan/apply/export and
Quick Build share the catalog validator. Named repeated experiments remain
separate implementation-plan work.

The [API contract](../postman/contracts/studio-evaluators.md) documents the version
1 `llm_judge` definition and response rules. Local tests cover invalid definitions,
score mapping, malformed answers, provider identity/errors, queued-copy isolation,
permissions, revision conflicts, configuration and legacy numeric grading.

Run `PYTHONPATH=. python examples/nexus-bot/src/studio_datasets.py` for a framework
consumer using synthetic providers. Full-app acceptance uses
`tests/studio_evaluators_acceptance.py`: sign in as synthetic Alice, edit Evidence
acceptance, apply Evidence judge, save, explicitly run checks and inspect the
report. It injects a local provider that returns Partial, so no account is called.
Saved Postman assertions verify the evaluator and resulting report. Browser checks
cover copying with preserved references, explicit execution and 390px/820px views.
Live provider accounts, physical devices and personal-VM deployment remain separate.

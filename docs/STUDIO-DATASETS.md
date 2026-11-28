# Reusable evaluation datasets

Open **Library → Datasets** or `/#datasets`. Create cases manually or import a CSV,
YAML or JSON file. Every imported case is validated before it replaces the draft.
Save to create a versioned workspace resource. Saving or importing never runs AI.

Each case contains task input and at least one phrase check, structured assertion
or explicitly configured judge. Reference passages use the existing `contexts`
list. **Structured checks and optional AI judge** exposes these settings. An exact
reference answer can be imported through CSV's `expected` column:

```csv
input,expected
Review policy,Approved
```

A YAML dataset can use phrase checks and reference passages:

```yaml
kind: dataset
version: 1
cases:
  - input: Summarize the return policy
    contains: [thirty days]
    contexts:
      - Returns are allowed within thirty days of purchase.
```

Datasets support up to 100 cases and 900 KiB of source/normalized content. Imports
reject duplicate keys, unknown fields, YAML aliases and executable object tags.
YAML also uses the existing portable configuration parser's depth and node limits.
The [API contract](../postman/contracts/studio-datasets.md) documents CSV columns
and JSON forms. **Export JSON** downloads the canonical dataset for re-import.

Datasets share the existing evaluation-suite workspace visibility. Authenticated
project members can read them; builders and owners can save or archive them.
Do not use this library for account-private source material. Archival, restore,
revision conflicts and history use existing catalog controls.

## Use cases in a suite

Create or edit an evaluation suite. Expand **Use a saved dataset**, choose a
dataset, then select **Replace cases from dataset**. Review the copied cases and
save the suite. The copy receipt displays the source revision. Cases become
independent suite data: editing or archiving the source does not change the suite.
Running the saved suite uses the existing queued suite snapshot and report flow.
No new evaluation executor, scheduling system or authentication service is added.

This is explicit copying, not a live dataset reference. Replacing cases again is
an explicit draft edit. Saved evaluator definitions, choice scoring and named
repeated experiments remain separate implementation-plan work.

## Compatibility and verification

Dataset storage is additive and requires no state migration. Native configuration
validate/plan/apply/export, Quick Build resources and backups use the existing
catalog. Existing inline evaluation cases and JSON imports remain supported.

Local evidence covers CSV/YAML/JSON round trips, malformed-file rejection,
permissions/CSRF/revoked access, workspace visibility, revision history, archive
and restore, native configuration round trips, all-resource build lifecycle and
queued suite independence. Provider execution uses injected synthetic providers;
no live accounts are contacted.

Browser checks use `tests/studio_datasets_acceptance.py`: rejected CSV preserves
draft cases, dataset copying saves a suite, and a later dataset revision leaves
that suite unchanged. Mobile/tablet checks use 390px and 820px widths. Saved
Postman assertions cover the dataset library and CSV parser. Physical-device and
personal-VM acceptance remain pending.

Run the framework consumer with:
`PYTHONPATH=. python examples/nexus-bot/src/studio_datasets.py`.

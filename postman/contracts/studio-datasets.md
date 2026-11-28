# Evaluation datasets

Datasets are reusable workspace resources under `/api/catalog/datasets`.
They use existing catalog reads, optimistic saves, history and archive/restore.
Team readers require an authenticated project session; writes require builder or
owner access. Like existing evaluation suites, datasets are shared within the
current Studio workspace, not private account records. They never reference or
read an arbitrary server path.

Canonical `data` is `{"kind":"dataset","version":1,"cases":[...]}`.
There must be 1–100 cases. Cases use the existing evaluation schema: `input`,
`contains`, `excludes`, `checks`, `judge`, `contexts`, `context_source`.
Each case needs at least one check or explicit judge. Unknown dataset/case/check
fields are rejected. Saving a dataset never runs cases or contacts a provider.

`POST /api/studio/datasets/parse` accepts `{"format":"csv|yaml|json","content":"..."}`.
It requires builder access, returns `200` with canonical dataset data and persists
nothing. Errors return `400`; missing project authentication returns `401` and
insufficient role returns `403`. Existing body limits and CSRF rules apply.
Source content is limited to 900 KiB; normalized data must also fit that limit.

- JSON and YAML accept a case array or an object containing `cases`, with optional
  `kind: dataset` and `version: 1`. Duplicate keys, YAML aliases, custom object tags,
  unsupported versions and unknown envelope fields are rejected.
- CSV requires a unique `input` header and at least one assertion column.
  Allowed columns are `input`, `expected`, `contains`, `excludes`, `contexts`,
  `checks` and `judge`. `expected` means an exact text-output check. Other assertion
  or context columns contain JSON values matching the case schema. Empty optional
  cells are omitted. Quoted multiline values and UTF-8 BOM are supported.
  Extra/missing cells and unknown columns are rejected. Entire-file validation
  completes before any draft cases are replaced.

Example CSV:

```csv
input,expected
What is the status?,Approved
```

Studio's Datasets library supports creating/importing/editing datasets and JSON
export. The evaluation editor's **Replace cases from dataset** copies a selected
current dataset into its draft. The source revision is displayed at copy time;
the cases become independent editable suite data. Saving the suite and explicitly
running it uses the existing immutable `suite_snapshot`. This slice adds no live
dataset references or new evaluation executor. Dataset changes and archival never
change saved suites, queued runs or old reports.

Existing suite JSON import, suite routes, run formats and catalog consumers stay
compatible. Existing configuration export/plan/apply and backup include datasets
through the shared resource catalog. No state migration is needed.

## Inline AI drafts

`POST /api/studio/design` additionally accepts `design_kind: "dataset"`, a nonempty
`task` (up to 16000 characters), optional `conversation` (up to 16000 characters),
`case_count` (integer 1–100, default 5), existing `provider`, and optional `model`.
It returns `202` with an ordinary private design run. Poll `/api/runs/{id}`;
only a completed run carries `design_proposal: {name, data}`. Generation uses
existing account routing and an empty temporary workspace. Only supplied request
text enters dataset generation; agent/workflow inventories and existing cases
are not automatically included. Account usage applies. Saving never invokes AI.

Builder authorization and CSRF are required; direct `/api/runs` design submissions
cannot bypass the builder check. Existing run ownership protects generated content.
Invalid counts fail with `400` before queuing. Invalid provider output fails the
run without a proposal or catalog write. Dataset schema, output size, exact case
count and duplicate input checks apply. Generated judges/provenance are rejected;
users may configure judges separately through existing reviewed editor controls.

The dataset and evaluation editors expose **Draft cases with AI**. Generation
preserves existing unsaved cases. Users review editable JSON and explicitly
append or replace it; JSON edits pass `/api/studio/datasets/parse` before applying.
Append rejects duplicate normalized inputs and totals above 100. Replacement is
explicit and replaces all cases. Closed editors ignore late results. Applying
never saves a resource or runs an evaluation. Existing catalog save/history and
immutable evaluation snapshots remain unchanged. No persisted schema migration
is needed; older clients need no changes.

## Pinned evaluation file roles

Cases additionally accept `input_files` and `reference_files`: lists of up to five
`{id, revision}` artifact references, with explicit positive integer revisions.
Duplicate references and additional reference fields are rejected. Reference files
require an explicit case judge; phrase/structured checks alone do not consume them.
These optional fields survive dataset import, catalog save and suite copying.

Starting an evaluation authorizes each selected current artifact and historical
revision for the requesting user. Archived current artifacts cannot start new
runs. Before any execution, the server copies bounded extracted text plus source
ID/revision, filename and SHA-256 into its private job payload. Caller-supplied
file snapshots are discarded. Editing/archiving source artifacts after queueing
cannot change the copied input or grading reference. No arbitrary paths or URLs
are accepted. Shared dataset references grant no access to private artifact bytes.

UTF-8 text, PDF and Office extraction reuse the document service. Each file must
have nonempty text up to 16000 characters; no silent truncation. The complete file
snapshot must fit 900 KiB. Unsupported binary, missing extraction dependencies,
invalid revisions, denied access or exceeded limits reject queuing before a run.
Input-file text joins task input as untrusted material. Reference-file text is
sent only to the configured judge, separately from input/answer. It never enters
the target invocation or workflow payload. Reports expose selected file roles and
source revisions/digests, not reference text. Explicit report export remains private.

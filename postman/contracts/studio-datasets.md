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

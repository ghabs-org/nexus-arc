# Explicit evaluation package conversion

`POST /api/studio/configuration/evaluations/convert` requires existing owner
configuration access, authentication and CSRF. It accepts `dataset` and `evaluator`
resource documents (objects or bounded YAML/JSON strings), `files` mapping exactly
the dataset's `spec.examples_file` basename to supplied UTF-8 CSV text, an explicit
`binding: {alias, provider, model?}`, and optional native `threshold` (default 0.7).
No server path, relative directory, URL or implicit sibling-file search is allowed.

Dataset schema is `kind: dataset`, `name`, optional `description`, and
`spec: {examples_file: basename.csv}`. CSV headers must be exactly
`sequence_number,prompt,expected_response`, in any order. There must be 1–100
complete rows, with unique positive integer sequence numbers and bounded nonempty
prompt/reference text. File text is limited to 900 KiB and the whole request to
2 MiB. Duplicate YAML/JSON keys, aliases, unknown fields, missing/extra files and
unsupported encodings fail before any write or provider call.

Evaluator schema is `kind: evaluator`, `name`, optional `description`, and
`spec: {type: llm_judge, model: alias, promptTemplate, choiceScores}`. The explicit
binding alias must match model. Templates support only `{{ Prompt }}`,
`{{ Expected Response }}` and `{{ Response }}`, each exactly once in this imported
profile. Unsupported expressions or includes reject conversion. Native runtime
renders values once as untrusted JSON strings and requires its normal validated
JSON choice/reason response. It does not run a general template engine.

Success returns `200 {document, dataset, evaluator, source, adaptations}`. The
native configuration contains only the two new draft resources; it creates no
suite, run or scheduled job. Cases copy judge settings and `expected_response`;
reference text reaches the judge only. Source includes CSV digest and sequence
numbers in original row order. Adaptations explicitly describe native JSON
response format, choice scoring, JSON data insertion and native threshold.

Existing native validate/plan/apply handles review, conflict checks and explicit
save. Conversion never saves or activates anything. Native export preserves the
resulting canonical resource data. Reviewed external-format package export is
defined separately in `studio-evaluation-export.md`; experiment operations have
their own profile. Explicit empty descriptions now remain empty, while omitted
descriptions retain the existing default. Invalid input returns `400`; missing identity
`401`; insufficient role or CSRF `403`.

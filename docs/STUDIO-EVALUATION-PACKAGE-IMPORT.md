# Import a dataset and judge template

In Library → Datasets or Evaluators, select **Import dataset + judge**. Shared
servers require workspace-owner access; local desktop uses its existing access.
Select the dataset YAML/JSON, evaluator YAML/JSON and exact referenced CSV. Choose
the evaluator's model alias, a native provider, optional model and pass threshold.

Conversion reads only those supplied file texts. It never searches server folders,
opens a source path, resolves a URL, installs anything or calls a model. Review
conversion adaptations and the native resources, then review the save plan and
explicitly save. Saved datasets and evaluators retain existing workspace sharing.
No suite, run or schedule is created. Copy dataset cases into a suite and review
before explicitly running it; target/judge accounts incur usage only then.

The dataset's `spec.examples_file` must be a CSV basename. Explicitly supply that
file, without directories or additional files. Supported CSV columns are exactly
`sequence_number`, `prompt`, `expected_response`. There must be 1–100 complete rows
with unique positive sequence numbers. Row order is retained; sequence numbers
and the source digest are reported during conversion. UTF-8 BOM, quoted commas,
multiline cells and Unicode are supported. A file is limited to 900 KiB and the
whole package to 2 MiB. Native input/reference text limits still apply.

The evaluator supports `type: llm_judge`, an explicitly bound `model` alias,
`promptTemplate` and `choiceScores`. The imported template must contain each of
these variables exactly once:

- `{{ Prompt }}`: case task input.
- `{{ Expected Response }}`: reference answer, reserved for the judge.
- `{{ Response }}`: actual target output.

Variables are substituted in one pass as JSON strings containing untrusted data.
Inserted text is never interpreted as another template. Expressions, includes,
duplicate variables and unknown placeholders reject conversion. Template source
is limited to 16000 characters; rendered data is limited to 128 KiB. Native
choices, finite score bounds and label validation are reused.

The target receives only its ordinary task input. Reference answers are stored in
`expected_response` and rendered only for the judge. They remain editable in case
settings and survive native export, saved-dataset copies and queued suite snapshots.
Judge templates remain editable in evaluator settings and survive copying to cases.
A template requiring an expected response rejects cases that have none.

The import explicitly adapts a bare-label response into Nexus's validated JSON
choice/reason response. Native threshold defaults to 0.7 and is shown before save;
choose it explicitly when another threshold is needed. No source threshold or
model provider is guessed. Model grading quality remains account-dependent.

Existing cases and evaluators without templates retain their behavior. These are
additive optional native fields; use an updated runtime for template-bearing data.
Reviewed external-format roundtrip is now available for the bounded inverse
profile through [dataset/evaluator package export](STUDIO-EVALUATION-EXPORT.md).
Arbitrary native case checks, context, file roles and judge rubrics still use
native JSON export. Experiment import/export is a separate profile. No
persisted-data migration is required.

The framework entry point is `StudioConfiguration.convert_evaluations(data, actor)`.
The HTTP contract is `postman/contracts/studio-evaluation-import.md`.
The thin local example is `examples/nexus-bot/src/studio_evaluation_import.py`;
its three synthetic input files are in `examples/nexus-bot/config/evaluation-import`.

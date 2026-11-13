# Export a dataset and template judge

In Library → Datasets or Evaluators, select **Export dataset + judge**.
Workspace owners can export; desktop uses its existing local access. Choose exact dataset/evaluator
revisions and an explicit external model alias. Export validates their contents
without reading artifacts, inspecting managed credentials or invoking AI.

Select **Prepare export for review**. Review diagnostic paths if the configuration
cannot be represented. No partial files are produced. Existing native JSON and
configuration export remain available for unsupported native features.

A supported package has four separately downloadable files:

- `dataset.yaml` names the dataset and its `cases.csv` sibling.
- `evaluator.yaml` contains the model alias, fixed template and choice scores.
- `cases.csv` contains complete prompts and expected answers in original row order.
- `evaluation-handoff.json` embeds the three inputs plus explicit native provider,
  model and pass threshold settings.

Each download requires an explicit click after preview. These files contain
private evaluation text and judge instructions. Managed credentials, file bytes,
agent definitions, experiments, runs and schedules are not embedded.

The companion is self-contained for the dataset and evaluator. Select **Import
evaluation handoff** in a fresh workspace, choose the JSON and convert for review.
The original source resources need not exist or remain unarchived. Source IDs and
revisions are descriptive; they never authorize fetching another workspace or
resource. The corruption-check digest is not author authentication.

Review the provider, model, threshold, copied cases and grader before selecting
**Review save changes**. Inspect the native plan, then explicitly save. Conversion
creates no plan, resource or execution. Saving creates no suite or AI run. Account
credentials and billing still belong to the destination workspace and current
user; review them before explicitly running a later evaluation.

The external declarations alone do not retain native provider, model and threshold
bindings. They also do not establish identical rendering or acceptance behavior
in another runtime. Nexus reimport retains its validated JSON choice/reason and
one-pass untrusted JSON variable substitution.

This exporter is the strict inverse of the existing CSV/template import profile:

- Every case needs input, expected response and the exact selected judge settings.
  Additional phrase/structured checks, context, provenance and file roles reject.
- The evaluator needs choice scores, all three fixed template variables and the
  import profile's rubric. Arbitrary native rubrics and numeric-only judges reject.
- Names must retain their name-derived IDs. Current revisions must match exactly.
  Native data and metadata must survive conversion preflight without changes.
- Row order and Unicode/quoted/multiline text are preserved. Sequence numbers are
  regenerated as 1..N because original CSV sequence IDs are not stored in cases.
- Each external input is at most 900 KiB; the complete prepared set is at most
  2 MiB. Oversized or nonrepresentable metadata is rejected, never truncated.

Explicit empty descriptions stay empty. Absent descriptions retain the existing
import default; nontext descriptions reject. These are additive conversion rules;
existing native records and configuration formats need no migration.

The new handoff is independently versioned as `nexus-studio-evaluation-handoff/v1`.
Its parser shares the experiment handoff's bounded JSON, duplicate-key, finite-
number, version and digest checks. Existing three-file import remains available.

Framework methods are `StudioConfiguration.export_evaluations(data, actor)` and
`StudioConfiguration.convert_evaluation_handoff(data, actor)`. The contract is
`postman/contracts/studio-evaluation-export.md`. The thin example
`examples/nexus-bot/src/studio_evaluation_import.py` now exports, converts and
explicitly saves the package in a fresh workspace without calling a provider.

This is a dataset/single-evaluator package, not a complete agent/workflow runtime
or deployment bundle. Visual browser, physical-device and account-dependent live
acceptance remain pending. No VM update, DMG rebuild or release publication occurred.

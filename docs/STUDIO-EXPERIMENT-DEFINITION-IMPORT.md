# Import an experiment definition

In Library → Experiment Lab, select **Import experiment**. Shared servers require
workspace-owner access; local desktop uses its existing authorization. First save
a dataset, evaluator and target agent. Dataset/evaluator packages can be imported
through **Import dataset + judge** in their respective library pages.

Paste an experiment YAML/JSON declaration, then explicitly select the dataset,
primary evaluator and agent revisions. Source aliases do not discover resources,
read server folders, fetch URLs or choose providers. Set the native minimum pass
rate explicitly. The API also accepts the existing native experiment variants;
the dialog defaults to the saved agent configuration.

```yaml
kind: experiment
name: Status regression
spec:
  datasetId: source-status-cases
  targetAgent: source-status-agent
  evaluatorIds: [source-status-judge]
  primaryEvaluatorId: source-status-judge
  runsPerExample: 2
  maxWorkers: 1
```

This bounded profile accepts 1–4 distinct evaluators including `primaryEvaluatorId`
and integer `maxWorkers` from 1–4 for its bound agent target. Omitted repeat and
worker settings default to one. Each worker owns one target/judge pair through
grading; the worker count is preserved in the native draft and saved experiment.
Repeat counts use the existing native range of 1–20. Duplicate/missing evaluators,
invalid worker counts, unknown fields and stale resource revisions reject conversion.
Unsupported concurrency is never silently discarded. The error dialog lets you
explicitly edit and resubmit the source while retaining the selected bindings.

Conversion returns a native draft containing a fresh evaluation suite and an
experiment pinned to that suite at revision 1. It copies dataset cases and
replaces only their judge settings with selected evaluator copies. Other checks,
expected answers, file references and context remain intact. The existing
template validator checks that every copied case supplies required references.

Dataset and evaluator sources remain unchanged and independent after saving.
Reimporting creates another suite; updating an experiment uses the ordinary
reviewed resource revision flow. Previous suites remain available. Agent/provider
settings and other execution dependencies are pinned when a run is queued through
the existing executor. Selecting an agent revision for conversion does not freeze
all future agent edits. Private-file permissions are still checked when running.

Review conversion adaptations and the draft, then select **Review save changes**.
Inspect the native change plan before selecting **Save reviewed experiment**.
Conversion, planning and saving start no model run, activate no resource and
schedule nothing. Run the saved experiment separately after reviewing provider
settings and billing. Judge calls add usage beyond the displayed target count.
Cases × variants × repeats must not exceed the native limit of 400 executions.

The framework entry point is `StudioConfiguration.convert_experiment(data, actor)`.
The owner/CSRF-protected HTTP endpoint is
`POST /api/studio/configuration/experiments/convert`.
See `postman/contracts/studio-experiment-import.md` for the full request contract.
The thin example is `examples/nexus-bot/src/studio_experiment_import.py`, using the
synthetic `config/evaluation-import` files. It converts, plans and saves without
calling a model.

Existing experiments and configuration formats retain their behavior. There is
no persisted-record migration. Canonical native export is supported; external
experiment declarations can now be exported with a required native companion;
see [reviewed export and handoff](STUDIO-EXPERIMENT-EXPORT.md). This reference
export does not embed dataset/evaluator dependencies. Multiple evaluator
bindings and the explicit native all-evaluator policy are described in
[multiple evaluators](STUDIO-EXPERIMENT-EVALUATORS.md). Native workflow-target
experiments require one worker. See
[parallel experiment execution](STUDIO-EXPERIMENT-WORKERS.md) for cancellation,
durable dispatch receipts and recovery limits. Browser acceptance is pending because the local-origin
permission gate blocked this session's disposable browser check. Backend HTTP,
interface-logic tests and the local example have been verified separately.

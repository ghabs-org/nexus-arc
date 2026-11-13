# Export an experiment declaration and native handoff

In Experiment Lab, workspace owners can select **Export declaration** beside a
saved experiment. Desktop uses its existing local access. The existing **Export
JSON** action remains available for canonical native configuration.

Choose the exact revision of a dataset whose ordered cases match the experiment's
pinned suite. Checks, expected answers, contexts, provenance and file roles must
match. Export compares effective cases without inline judges because the selected
experiment evaluator copies replace them during named experiment execution.

Select **Prepare export for review**. Studio checks the experiment revision,
pinned suite, target agent, dataset and copied evaluator revisions/settings. It
reads no artifact bytes, invokes no provider and saves no configuration. Changed
or unsupported dependencies produce specific diagnostic paths and no export files.
Native configuration export remains available when a declaration cannot represent
the saved configuration.

Review the notes and file previews. Each download requires its own explicit click:

- **experiment.yaml** contains the external experiment declaration: name,
  description, dataset/agent/evaluator aliases, primary judge, repeats and workers.
- **experiment-handoff.json** retains native thresholds, variants, exact dependency
  revisions and the import profile's explicit all-evaluator acceptance behavior.

Keep the companion file for native reimport. The declaration alone cannot carry
all native settings. These are reference files, not a self-contained deployment
or resource package. Dataset cases, document contents, grader instructions, agent
definitions, credentials, runs and schedules are not embedded. Do not assume an
external runtime understands the companion's native settings or acceptance rules.

For reimport, select **Import handoff**, choose the companion JSON, then select
**Convert for review**. Studio parses the original text strictly, checks its
digest and validates exact current dependency bindings through the existing
experiment converter. Duplicate keys, unsupported fields/versions, nonfinite
numbers, corruption and stale references reject before any plan or save.

Review conversion adaptations and the generated native draft. Select **Review
save changes**, inspect the plan, then explicitly **Save reviewed experiment**.
No AI runs during any of these steps. Reimport creates a fresh suite and uses the
ordinary reviewed experiment revision flow; it does not rewrite the old suite.
The imported suite uses the primary evaluator as its inline judge, while named
experiment execution uses the complete copied evaluator set. Original standalone
suite judges are outside this reference export and remain unchanged at the source.

The handoff requires its native dependencies to exist at the recorded revisions.
It does not discover a server folder, fetch a URL, open an artifact or install
anything. Target edits or evaluator updates after export require a new reviewed
export or explicit ordinary import bindings. File permissions are still checked
when a run queues; export does not grant access to referenced files.

The digest detects corruption, not authorship. Descriptive source identity is
shown for review and never determines which resource to fetch or mutate. Review
the actual native change plan before saving an untrusted handoff.

Declaration export currently requires an agent target, a copied evaluator set
matching the current catalog, a matching dataset and a name-derived experiment
ID. Workflow targets, per-case-only judges, stale pins, changed copied judges,
custom IDs and unmatched datasets return diagnostics. There is no lossy fallback.
Self-contained [dataset/evaluator package export](STUDIO-EVALUATION-EXPORT.md)
is now available separately for its strict CSV/template inverse profile. It does
not embed target agents or turn this experiment reference export into a complete
deployment bundle.

The reusable methods are `StudioConfiguration.export_experiment(data, actor)` and
`StudioConfiguration.convert_experiment_handoff(data, actor)`. Contracts are in
`postman/contracts/studio-experiment-export.md`. The thin example
`examples/nexus-bot/src/studio_experiment_import.py` now verifies export/reimport
without starting a run. The local regression also applies a reviewed handoff and
executes preserved variants/repeats through a synthetic provider.

The handoff is versioned independently as `nexus-studio-experiment-handoff/v1`.
Existing configuration, experiment and report formats need no state migration.
Visual browser and real-provider acceptance remain pending. No VM deployment,
desktop rebuild or release publication occurred.

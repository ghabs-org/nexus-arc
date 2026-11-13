# Dataset and evaluator declaration export

`POST /api/studio/configuration/evaluations/export` requires existing workspace
configuration owner access and CSRF. Request: `{dataset:{id,revision},
evaluator:{id,revision}, model_alias}`. IDs and positive integer revisions are
explicit; model_alias is the chosen external model name, never a URL to fetch.

Success: `200 {valid:true, diagnostics:[], source, notes, files}`. Four fixed-name
files are produced: `dataset.yaml`, `evaluator.yaml`, `cases.csv`, and
`evaluation-handoff.json`. Dataset prompts and expected answers are embedded;
review/download is explicit. No artifacts, paths, URLs, credentials, agent
definitions, experiments, runs or schedules are read or embedded. Native provider,
model and threshold settings live in the companion; external model alias alone
does not preserve account/billing configuration.

The supported inverse profile is the existing three-column CSV/template importer.
Cases must have input, expected_response, empty phrase checks and the selected
judge copy. Extra structured checks, context, file roles and differing case judges
reject with path/code diagnostics, never silently dropping data. The selected
evaluator must have supported choice scores, all three fixed template variables,
and the import profile's rubric. Arbitrary native rubrics or numeric-only judges
remain supported by native JSON export, not this external declaration profile.
Names must preserve name-derived IDs. Exact metadata/data equality is checked by
reusing conversion preflight before files are exposed. Row order is preserved;
new sequence numbers 1..N are explicit because native cases do not retain original
source sequence IDs. Empty descriptions remain empty; omitted descriptions retain
the importer's existing default.

Invalid/stale/archived or unrepresentable saved resources return
`200 {valid:false, diagnostics, files:[]}`. Malformed requests return 400. The
whole prepared file set is at most 2 MiB and each external input at most 900 KiB.

`POST /api/studio/configuration/evaluations/handoff/convert` accepts
`{handoff:<JSON text>}`. It uses the shared strict bounded JSON/version/digest
reader, rejects duplicate keys/nonfinite numbers/corruption, then reuses native
evaluation package conversion. The independently versioned format is
`nexus-studio-evaluation-handoff/v1`. It embeds source documents, CSV and explicit
native binding/threshold, so native source resources need not exist at destination.
Source IDs/revisions are descriptive only. The digest is not author authentication.

Success returns the existing conversion report plus handoff_source. Conversion
creates no plan, saved resource or execution. Existing native plan review,
optimistic conflict checks and explicit apply are required. Export/conversion
never call providers or check provider credentials. Existing singular dataset,
evaluator and three-file import/export interfaces remain available.

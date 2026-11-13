# Reviewed experiment declaration export and handoff

`POST /api/studio/configuration/experiments/export` uses existing configuration
owner authorization and CSRF. Request: `{experiment_id, revision, dataset:{id,
revision}}`, with positive integer revisions and native IDs. Export reads only
explicitly selected catalog resources and the selected suite's target definition.
It never reads artifact bytes, queries external services, invokes AI or saves state.

Success returns `200 {valid:true, diagnostics:[], files, source, notes}`. Files
contain a fixed-name `experiment.yaml` declaration and
`experiment-handoff.json` companion. The declaration includes name, description,
dataset/agent/evaluator IDs, primary evaluator, repeats and workers. The companion
preserves native variants, threshold, all-evaluator policy through the existing
import profile, exact current dependency bindings and descriptive source identity.
A SHA-256 digest detects corruption; it is not a signature or authority grant.

The declaration alone cannot preserve native execution semantics. Review notes
before explicitly downloading either file; use the companion for native reimport.
This is a reference export, not a self-contained resource/deployment package.
Dataset cases, judge instructions, target definitions, files, credentials, runs
and schedules are not embedded. Dependencies must already exist at exact revisions.

Unsupported or changed saved state returns `200 {valid:false, diagnostics, files:[]}`.
Diagnostics have path/code/message and never return a partially usable export.
Requirements: current experiment revision, current suite matching its pinned
revision, agent target, copied evaluator set matching current unarchived catalog
revisions/settings, and explicitly selected dataset cases matching the suite's
effective cases. Inline case judges are ignored for comparison because selected
experiment evaluators override them; copied primary becomes the imported suite's
inline judge, as existing import documents. A custom experiment ID that cannot
survive name-derived import is rejected. Native configuration export remains
available for unsupported declarations. No lossy fallback is generated.

`POST /api/studio/configuration/experiments/handoff/convert` requires the same
owner/CSRF protections and accepts `{handoff: <JSON text>}` up to 2 MiB. Parsing
rejects duplicate keys, nonfinite numbers, unsupported fields/versions and invalid
digests. It calls the existing experiment converter, including exact revision and
template/file-reference validation, and returns the existing native draft report.
It creates no plan, saved resource or run. Separate plan review and explicit apply
remain required. Source identity is descriptive, never an instruction to mutate
or fetch a path, URL or account. Exported dependency pins never bypass file access.

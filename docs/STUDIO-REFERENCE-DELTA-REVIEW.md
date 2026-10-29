# Reference comparison follow-up — 29 October 2026

This is a bounded follow-up to the frozen implementation inventory, not a new
claim of complete upstream parity. The original 135 accepted evidence flags and
27 supported catalog entries retain their existing scope. External accounts,
physical devices and installed desktop acceptance remain separate. The user has
deferred all VM work and requires an explicit request before another DMG build.

The current reference release page lists version 2.455.0 and describes its Go
runtime. Its public repository says the new source is forthcoming. Public docs
and examples do not establish full runtime equivalence, and no new installed
reference artifact was inspected in this follow-up.

## Findings and next slices

| Capability | Nexus evidence inspected | Follow-up |
| --- | --- | --- |
| Draft evaluation datasets with AI | `quick_build.py` already supports dataset resources, with review and explicit save. The dataset editor in `operations.js` supports manual cases, saved evaluators and file import. | A dedicated inline draft/append interaction remains a UX gap. Reuse existing generation and job services; do not build a second provider path. Verify duplicate handling and preserve unsaved edits. |
| Evaluation release thresholds | Saved experiments already apply thresholds per variant through `studio_experiments.py`. | Completed local addition: export full report JSON and check it offline with `nexus eval check-report`. Incomplete/error evidence blocks. Before/after regression comparison remains a separate candidate. |
| Separate evaluation input and grading-reference files | `studio_evaluations.validate_case` supports text input, structured checks and copied reference passages with provenance. It does not define separate input-file and expected-file fields. | Add explicitly pinned artifact roles through existing artifact authorization. Only the input reaches the target; expected files are reserved for grading. Include queued-run pinning and cross-user denial tests before accepting. |
| Continue a shared snapshot in a new conversation | `shared.js` loads a read-only, expiring snapshot with artifact downloads; no continuation control was found in that viewer. | Review backend sharing/session services before implementing a private copy. Access must be checked at copy time; no public token or owner authority may transfer to the new chat. |
| Exact artifact-version navigation from Activity | Public reference docs describe opening the producing session at the referenced version. | Not fully audited in this slice. Trace Nexus execution events, artifact revisions and viewer navigation before calling anything missing. |

These are new comparison candidates, not reasons to reopen already accepted
frozen implementations. A dedicated inline dataset draft is the next small local
product slice. Separate evaluation-file roles follow because they require more
authorization and queued-run coverage. Desktop milestone 9 remains last.

## Sources

- [Current public repository](https://github.com/SolaceProducts/solace-agent-mesh)
- [Current release information](https://github.com/SolaceProducts/solace-agent-mesh/releases)
- [Dataset authoring and file roles](https://docs.solace.com/Agent-Mesh/Framework/evaluating-agent-performance/datasets.htm)
- [Evaluation results and upgrade gates](https://docs.solace.com/Agent-Mesh/Framework/evaluating-agent-performance/index.htm)
- [Activity, artifact versions and shared-chat continuation](https://docs.solace.com/Agent-Mesh/Framework/using-agent-mesh/monitoring-activity.htm)

The source pages were inspected on 29 October 2026. This list does not claim an
exhaustive review of every current upstream feature or connector.

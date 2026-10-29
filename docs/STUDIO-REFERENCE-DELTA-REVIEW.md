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
| Draft evaluation datasets with AI | `quick_build.py` already supports dataset resources, with review and explicit save. The dataset editor in `operations.js` supports manual cases, saved evaluators and file import. | Completed locally on 29 October: inline provider/model/count selection, validated editable preview, explicit append/replace and separate save. Existing designer jobs are reused. Duplicate, invalid output and unsaved-edit preservation checks pass; see STUDIO-DATASET-DRAFTING-CHECKPOINT.json. Real model quality remains unverified. |
| Evaluation release thresholds | Saved experiments already apply thresholds per variant through `studio_experiments.py`. | Completed local addition: export full report JSON and check it offline with `nexus eval check-report`. Incomplete/error evidence blocks. Before/after regression comparison remains a separate candidate. |
| Separate evaluation input and grading-reference files | `studio_evaluations.validate_case` supports text input, structured checks and copied reference passages with provenance. It does not define separate input-file and expected-file fields. | Completed locally on 29 October for bounded extracted text: explicit input/reference revisions, prequeue authorization/copy, judge-only references, import/editor/report support. Tests cover private historical/current access and queued-copy pinning; see STUDIO-EVALUATION-FILES-CHECKPOINT.json. Original multimodal inputs are outside this slice. |
| Continue a shared snapshot in a new conversation | `shared.js` loads a read-only, expiring snapshot with artifact downloads; no continuation control was found in that viewer. | Completed locally: authenticated private continuation, exact file copies, grant/expiry checks and idempotent explicit retry. No token or owner authority transfers. See STUDIO-SNAPSHOT-CONTINUATION-CHECKPOINT.json. |
| Exact artifact-version navigation from Activity | Public reference docs describe opening the producing session at the referenced version. | Audited locally: recorded revisions already open exact artifact previews, including archived sources. Legacy events without a recorded revision now label their current-version fallback explicitly. Jump-to-producing-session navigation was not added. |

These are new comparison candidates, not reasons to reopen already accepted
frozen implementations. Inline dataset drafting and bounded evaluation file roles now have scoped local
acceptance. Private snapshot continuation and the bounded artifact-version audit
now also have scoped local evidence. Further upstream syntax/import and deployment
acceptance work remain separate. Desktop milestone 9 remains last.

## Sources

- [Current public repository](https://github.com/SolaceProducts/solace-agent-mesh)
- [Current release information](https://github.com/SolaceProducts/solace-agent-mesh/releases)
- [Dataset authoring and file roles](https://docs.solace.com/Agent-Mesh/Framework/evaluating-agent-performance/datasets.htm)
- [Evaluation results and upgrade gates](https://docs.solace.com/Agent-Mesh/Framework/evaluating-agent-performance/index.htm)
- [Activity, artifact versions and shared-chat continuation](https://docs.solace.com/Agent-Mesh/Framework/using-agent-mesh/monitoring-activity.htm)

The source pages were inspected on 29 October 2026. This list does not claim an
exhaustive review of every current upstream feature or connector.

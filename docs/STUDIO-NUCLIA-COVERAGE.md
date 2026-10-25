# Nuclia source reconciliation

This is a source-based scope audit, not a new provider or reference-runtime test.
It reconciles accumulated native evidence with frozen commit
`7599710a38318a336c47cf605e4774d4add24d8e`. It does not promote whole-entry,
milestone or plan completion.

Inspected source bytes:

| Source | SHA-256 |
| --- | --- |
| `nuclia_rag_tool.py` | `f52ad878d0d501c08d50b670376c4468fc2cb4e918a68e4f45d49dd868cb440f` |
| `nuclia_rag_tool_config.py` | `83621a743f310064f22e595b8e73e0c9af280bd33b00849797f54b204c3fa9b7` |

Original source names and bytes remain in the external audit archive. Product
code uses native implementations and loads no reference Python packages.

## Implemented native equivalents

| Frozen behavior | Native equivalent and behavioral evidence |
| --- | --- |
| Service URL, knowledge box and service token | Matching connection binding, server-managed environment reference and canonical request scope; `test_studio_nuclia_descriptors.py`, `test_studio_enterprise.py` |
| Query, top-k, user prompt, Markdown preference, rephrasing | Fixed validated ask settings and conflicting-caller rejection; `test_studio_nuclia_descriptors.py`, `test_studio_nuclia.py` |
| Typed template parameters and legacy context parameters | Versioned typed templates, defaults, nullability, server-bound plain paths and hidden server parameters; `test_studio_nuclia_templates.py` |
| Rephrasing/filter templates | Non-recursive declared substitution, immutable configured filters and explicit strict-policy import; `test_studio_nuclia_interop.py` |
| Audit metadata | Explicit typed/context-backed templates, disabled default, spoof rejection and 10 KiB outbound limit; `test_studio_nuclia_audit.py` |
| Neighbouring paragraphs and title/summary hierarchy | Explicit reviewed retrieval preservation, normalized counts and expanded citation snapshots; `test_studio_nuclia_retrieval.py` |
| Provider status and no-context responses | Plain output compatibility, explicit no-context result and failure before partial publication; `test_studio_nuclia.py` |
| Inline/file answers, filename, description and citation flags | Explicit answer options, safe local filenames, plain output retained and private artifact revisions; `test_studio_nuclia_presentation.py` |
| Default, no-citation and model-footnote modes | Explicit modes with identifier-bound footnotes and revision-pinned local links; `test_studio_nuclia_presentation.py` |
| Original and augmented citation positions/pages | Exact private paragraph/field snapshots with provenance; `test_studio_nuclia.py`, `studio_nuclia_context_acceptance.py` |
| File originals through a token broker | Explicit authenticated canonical file download to a private immutable artifact; `test_studio_nuclia_documents.py`, `studio_nuclia_acceptance.py` |
| Evaluation publication with all original retrieved contexts | Explicit private full-retrieval capture, uncited markers, feedback promotion and separately selected judging; `test_studio_retrieved_evidence.py`, `studio_nuclia_acceptance.py` |
| Python descriptor/configuration | Non-executing reviewed native bindings and source/native export; `test_studio_nuclia_descriptors.py`, `test_studio_nuclia_presentation.py`, `test_studio_retrieved_evidence.py` |

Files above are under `tests/`. Their associated checkpoints retain exact runs,
counts, scope, hashes and example consumers. The latest presentation/full-context
evidence is linked from [Nuclia setup](STUDIO-NUCLIA.md). Existing recorded runs
are historical evidence; reading this table does not rerun them.

## Declared adaptations and open comparison

The strict binding profile requires explicit review. General Python expressions,
executable formatting and positional-only footnotes are rejected. Plain paths
and scalar placeholders are supported. Missing required/filter inputs fail
instead of silently dropping a filter. Top-k is bounded to 50 rather than the
source configuration's 200; private captures retain their documented count/byte
limits. Citations use exact paragraph snapshots rather than grouping every source
by remote URL and page. Supported original files use authenticated local snapshots
rather than distributing ephemeral provider tokens. These are deliberate native
policies, not claims of byte-for-byte runtime equivalence.

Broader expression compatibility and independent source/native runtime comparison
remain open. The catalog entry remains partially accepted until the supported
native profile and explicit rejected constructs have their final acceptance review.
Live permissions, answer/retrieval quality, billing, downstream judge quality and
personal-VM/device operation remain separately unverified.

## Items outside the frozen promise

`_format_answer_with_citations` skips sources without `data.files`, selects the
first file field, and constructs a broker URL with a page fragment. It does not
offer a general non-file/split attachment original service. Current native
conversation attachment browsing adds capability beyond that file-only behavior.

The broker URL refers to the remote object when fetched. The inspected code
does not pin a historical remote original revision. Native downloads similarly
report `remote_revision_pinned: false`, while the saved local copy and original
cited paragraphs retain immutable revisions. A historical remote-original service
and general non-file/split originals are additional backlog, not established
missing clauses in this frozen catalog source.

The optional evaluation block gathers original retrieved paragraph text and
publishes question, answer, contexts, status and identifiers. It does not calculate
scores. Native private capture/promotion replaces that transport; judging stays
explicit. Excluded external broker delivery is not a remaining runtime dependency.

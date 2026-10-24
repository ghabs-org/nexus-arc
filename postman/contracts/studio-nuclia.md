# Nuclia opt-in generation contract

Server-bound template parameters can declare `context_path` as one to eight plain
object keys. The server supplies selected workspace/identity metadata; runtime
arguments cannot override it. The converter accepts only plain dotted context
expressions. Such parameters are omitted from the agent parameter description.
Missing/invalid required values reject before HTTP. No automatic file, credential
or environment metadata is included.

Native connections may additionally store `ask_settings` with fixed `top_k`,
`rephrase`, `prefer_markdown` and `prompt` values. The invocation validator checks
these settings during save and before requests. A conflicting caller override
fails before HTTP. Matching values are accepted. Search behavior and connections
without these settings retain their existing defaults. Studio's ordinary
connection editor preserves these settings while editing other fields.

`convert_tool_descriptor(..., policy="nexus-strict-v1")` adapts supported Python
Nuclia descriptors without importing their module or resolving environment values.
`bindings.nuclia` on the existing configuration conversion route explicitly binds
the descriptor tool name to matching native connection data. Connections expose
`ask` only; broader actions, differing settings and missing/implicit policy fail.
Successful reports contain `adaptations`, which the import review displays before
planning. See `studio-interop-configuration.yaml` and
`studio-nuclia-descriptors.assertions.js`. Conversion creates no connection or
activation. Source-backed export retains the descriptor and explicit binding;
native configuration export retains fixed settings and credential references.

Ask responses may cite validated `augmented_context.paragraphs` or `.fields`.
Each cited text block becomes a private revision with exact text, provider page,
SHA-256 and augmentation type. Unknown/missing identities, conflicting duplicate
sources and malformed blocks reject before artifact publication. Only cited text
enters evaluation context. Conversation/field text evidence does not imply that
its remote binary attachments have been downloaded.

Existing connector fields and default `query` permissions stay unchanged.
`ask` is a newly selected read action using the same configured knowledge box
and identity-scoped credential reference. It is never enabled by catalog browsing.

- `query` posts to `/api/v1/kb/{id}/find` and retains search pagination/raw JSON.
- `ask` posts to `/api/v1/kb/{id}/ask`, with `X-NUCLIA-SERVICEACCOUNT: Bearer ...`,
  `X-Synchronous: true` and `citations: default`.
- Query text is bounded to 4,000 bytes, top_k to 1..50, filters to 8 KiB/eight
  levels, prompt user/system text to 4,000 bytes each. Nonzero ask pages fail.
- Synchronous ask accepts at most 256 KiB with a configured 90-second read/processing timeout.
- Success requires a documented status, answer string, retrieval resources and
  a citation mapping whose paragraph IDs exist. Error/unknown status fails.
- Only cited paragraph text becomes evidence. No-context results stay explicit.
- Private artifacts retain answer/source snapshots. Run citations pin exact
  artifact revisions and retain the original provider page separately.
- Cancellation, revoked execution access, changed connectors and exhausted run
  artifact/citation capacity block publication before saving any result.
- Provider failures never trigger implicit retry, credential fallback or remote
  original-document downloads. No source access tokens enter citation metadata.

Authority: official Nuclia Ask/filter documentation and SyncAskResponse/
KnowledgeboxFindResults models. Frozen plugin guide at commit
`7599710a38318a336c47cf605e4774d4add24d8e` identifies richer remaining template,
remote-document broker and evaluation-publication semantics.

Run `postman/simulations/studio-nuclia.sim.yaml` followed by explicit
`tests/studio_nuclia_acceptance.py`. The application and HTTP transport run for
real against the dependency stand-in. Its synthetic failure markers and attempts
counter are instrumentation, not a vendor API. Unsupported routes return 501.
No live account is used. Owner mock discovery is unavailable while Postman CLI is
signed out; Orbit discovery returned 403. Official vendor sources supplied the
fixture shapes. See `docs/STUDIO-NUCLIA.md` for setup, migration and remaining work.

Versioned connector `template` is optional. Version 1 accepts typed parameter
schemas, a `{{query}}` query prompt and a JSON filter template. Runtime values live
in `arguments.parameters`. Whole-value placeholders preserve JSON types;
replacement is non-recursive. Invalid/undeclared values fail before I/O. Runtime
filters cannot replace a configured filter. Native configuration validate/plan/
apply/export uses the same validators; defaults never enter tool descriptions.
The simulation's templated case additionally requires the expanded region/query
and resource filter. Both ordinary and templated consumers retain nine requests
per example, with invalid typed values producing no request.

The optional reference template-settings consumer now converts the frozen plugin's
three template fields through `convert_template_settings(..., policy="nexus-strict-v1")`
before using native configuration plan/apply. Conversion reports its behavior
adaptations and rejects unsupported settings. It creates no API route and does
not import complete Python tool descriptors. The third real-HTTP acceptance
variant uses the same provider contract and retains nine provider requests;
invalid conversion does not resolve credentials or make provider requests.

## Native private evaluation evidence

Nuclia connector data optionally accepts boolean `capture_evaluation`, absent or
false by default. Only `ask` captures evidence. It adds a private
`retrieval-evidence.json` artifact and an `evaluation_evidence` reference to the
answer result. The versioned `nexus-retrieval-evidence/v1` document contains the
effective query, provider answer/status, bounded optional learning ID, selected
connector/run IDs, cited text and paragraph identities. It contains no raw
provider metadata, credential headers, original-document URLs or ephemeral
tokens. Context scope is explicitly `cited-paragraphs-only`.

Existing `POST /api/studio/feedback/promote` optionally accepts
`evidence: {artifact_id, revision}` and an explicit `judge` configuration. The
artifact's current and pinned owners, generated-run provenance, format and rated
run must match before context is copied. Unknown/foreign/unrelated uploads fail;
failed promotion creates no evaluation. Ordinary promotion retains its old
phrase-only behavior. No judge or provider runs during promotion.

Evaluation cases optionally retain `contexts` (up to 20 nonempty passages, each
16,000 UTF-8 bytes, 64,000 bytes combined and 96,000 serialized JSON bytes) and
`context_source: {artifact_id, revision}` metadata. The source reference records
where an editable copy came from; evaluation never dereferences it or grants
artifact access. Only an explicitly configured judge receives the copied text,
marked as untrusted reference data. Existing phrase and structured checks remain
unchanged. Judge model usage belongs to the selected account.

Capture preflight enforces the existing 40-artifact run limit, including the
extra report. Reports are capped at 160,000 serialized bytes. Validation, late
cancellation, revoked execution rights and changed connector settings stop
publication before any evidence artifact is created. Native execution events
contain only artifact/revision references, provider and status. The bounded
learning ID remains in the private artifact/result, never content-free traces.

The fourth HTTP simulation consumer exercises capture, feedback promotion and a
synthetic explicitly selected judge. It uses the same nine Nuclia requests and
makes no live model/account call. Existing three consumers remain compatible.

## Native original-file access

- Explicit optional `download_original` action; query-only defaults remain.
- `POST /api/studio/original-document` accepts exactly `source_run_id` and
  zero-based integer `source_index` (0..39), plus host-injected identity fields.
  Identity always comes from the authenticated host. Returns HTTP 202 with an
  existing `kind: tool` run; clients poll the same run rather than resubmit.
- Saved citation must belong to a completed visible run and carry an unchanged,
  enabled Nuclia connection reference plus a valid file-field identity.
- Upstream route: `GET /api/v1/kb/{kb}/resource/{rid}/file/{field}/download/field`,
  authenticated using `X-NUCLIA-SERVICEACCOUNT`, without token-bearing URLs.
  Only HTTP 200, no redirects/range requests, maximum 16 MiB and 60 seconds.
- Output is a private local revision with SHA-256, retrieval time, artifacts and
  `remote_revision_pinned: false`. Remote history is not inferred.
- Completed owner/citation/configuration receipts reuse one snapshot across
  restart and concurrent publication. Final source/configuration/ownership,
  job cancellation and artifact-limit checks precede saving/reference updates.
- Browser links target local artifact revisions. Provider metadata, ephemeral
  tokens and credential-bearing URLs are never copied to them.

The fifth real-HTTP simulation consumer makes ten provider requests (the usual
nine plus one binary download), verifies bytes and an explicit cached repeat.
Four separate HTTP failures cover denied, redirect, partial and oversized reads.
The new binary option on the shared bounded transport leaves JSON/text modes
unchanged. `postman/contracts/studio-original-document.assertions.js` verifies
the actual disposable Studio job endpoint, not a mock of that endpoint.

# Nuclia answers with private citation snapshots

Nuclia connections retain their existing `query` search action. The new `ask`
action explicitly enables provider-side answer generation through the same
Nexus connector, permissions and scoped credential reference. No new login,
provider fallback, model installation or frontend build is introduced.

## Setup and billing

Configure the existing Nuclia connection with service origin, knowledge-box ID
and `token_env`. New and existing defaults remain `actions: ["query"]`.
Add `ask` to the selected actions only when you want Nuclia's generative API;
then explicitly enable the connector. Nuclia account usage is separate from
Codex/Claude Code subscriptions. Dataset filters narrow retrieval and are not an
access-control replacement; Nuclia and Nexus account permissions still apply.

An answer request uses:

```json
{
  "action": "ask",
  "arguments": {
    "query": "What is the return policy?",
    "top_k": 10,
    "filter_expression": {"field": {"prop": "resource", "id": "selected-resource"}},
    "prompt": {"user": "Use {context} to answer {question}"},
    "rephrase": false
  }
}
```

`query` and `ask` both support bounded `filter_expression` objects. The outer
shape accepts `field`, `paragraph` and `operator: and/or`. Filters are limited to
8 KiB and eight nested levels; provider-specific predicate semantics are validated
by Nuclia. Unknown action arguments fail instead of disappearing silently.
`query` retains `page: 0..100` and `top_k: 1..50`. Answer generation rejects
nonzero pagination. `ask` additionally accepts explicit `user/system` prompt
text and boolean `rephrase`. Use existing Nexus prompt variables to prepare
application-specific values. Provider `{context}` and `{question}` placeholders remain literal for Nuclia;
expand application variables before calling this adapter. It executes no template code.

The adapter sends `citations: default` and `X-Synchronous: true` to `/ask`.
The synchronous transport sets a 90-second read/processing timeout and accepts at most 256 KiB.
It does not stream partial provider answers. Existing search request defaults
and response JSON stay unchanged.

## Evidence and output

A successful answer returns `output`, explicit `status`, `sources`,
`provider_citations`, `artifacts` and `untrusted: true`. Nuclia's answer-span
mapping is retained; Nexus does not invent or reinterpret those offsets.
Each cited paragraph must exist in returned retrieval data. Uncited paragraphs
are not presented as evidence. Ambiguous or missing paragraph identities fail.
At most twenty cited paragraphs are accepted; narrow larger queries.

Nexus saves a private answer Markdown artifact and one private text snapshot per
cited paragraph. Snapshots retain knowledge-box/resource/field/paragraph IDs,
the provider's original page value and exact retrieved text. These are retrieved
paragraph snapshots, **not copies of the original PDF or original document
revisions**. No provider document URL, ephemeral token, source URL credential,
raw retrieval metadata or account header is copied into citation records.

During an agent/workflow run, snapshots use existing artifact provenance and
execution events. Run and conversation citations point to the saved snapshot's
exact revision. The existing source list opens that private artifact preview;
`page: 1` refers to the snapshot's first text section. `original_page` separately
retains Nuclia's page number without assuming its numbering convention. Replacing
or reindexing a remote document does not change previously saved source text.

`no_context` and `no_retrieval_data` remain explicit outcomes with no fabricated
sources. Provider error/unknown statuses, malformed responses, missing citation
paragraphs, redirects, non-success HTTP responses and oversized data fail before
any artifact is saved. No automatic retry or alternate account/provider is used.
Explicit workflow retries remain controlled by existing read-action policy.

Before local publication, Nexus rechecks run ownership/status/cancellation,
workspace execution access and the selected connector's unchanged configuration.
It checks run citation/artifact capacity before publishing through the shared
artifact service under the existing workspace lock.

## Local acceptance

Start the repository dependency simulation:

```sh
postman simulation run postman/simulations/studio-nuclia.sim.yaml --port 49873 --output ndjson
```

Then exercise the actual connector, HTTP transport, agent loop and citation API:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia.py
PYTHONPATH=. python -m pytest tests/test_studio_nuclia.py tests/studio_nuclia_acceptance.py -q -o addopts=''
```

The consumer uses a disposable workspace, synthetic credentials and deterministic
AI. It proves default search compatibility, explicit generation permission,
filter/prompt forwarding, cited synthesis, private snapshot previews, revision
pinning, no-context output and six provider failure cases with one attempt each.
Missing credentials produce no provider request. Separate fixtures cover late
cancellation, completed runs, access revocation, changed connectors and run limits.
The stand-in follows official synchronous response models; its failure query
markers and `/fixture/attempts` route are test-only instrumentation.

## Compatibility and remaining catalog work

Saved configurations do not gain `ask` automatically. New response fields apply
to the new action only; `query` responses retain their original shape. Existing
unknown arguments that were ignored are now rejected explicitly. No persisted
schema migration or credential migration is needed.

This is scoped local acceptance, not complete Nuclia plugin parity. Configured
runtime parameter declarations and reusable filter/rephrasing templates now
have scoped native configuration acceptance, described below. Original remote-document viewing
through provider broker/ephemeral URLs remains separate work; native file-field access
is now described under "Save original files from citations" below. This answer slice provides
Nexus-authenticated local evidence snapshots instead. The plugin's Solace REMi
publishing is excluded; native cited-only evaluation evidence is described below.
Live Nuclia generation quality, predicate behavior, account permissions,
original-document access and billing remain unverified. Milestones 7 and 8 remain
open.

Sources:

- [Official Ask contract](https://docs.rag.progress.cloud/docs/rag/advanced/ask/)
- [Official filter contract](https://docs.rag.progress.cloud/docs/rag/advanced/search-filters/)
- [Official synchronous response model](https://github.com/nuclia/nucliadb/blob/main/nucliadb_models/src/nucliadb_models/search.py)
- [Frozen plugin guide](https://github.com/SolaceLabs/solace-agent-mesh-core-plugins/blob/7599710a38318a336c47cf605e4774d4add24d8e/sam-nuclia-tool/docs/nuclia_rag_tool_user_guide.md)

## Stored typed query templates

Nuclia connector data now accepts an optional, versioned `template`. Configure it
through existing configuration-as-code validate/plan/apply operations or the
framework catalog API. Ordinary Studio connection edits preserve it; this release
does not add a separate visual template designer.

```json
{
  "version": 1,
  "parameters": [
    {"name": "region", "type": "string", "required": true},
    {"name": "document", "type": "string", "default": "selected-resource"}
  ],
  "query_prompt": "Region: {{region}}; Question: {{query}}",
  "filter_expression": {
    "field": {"prop": "resource", "id": "{{document}}"}
  }
}
```

Call the selected connection with `arguments.query` and
`arguments.parameters: {"region": "emea"}`. Parameters support string, boolean,
integer and finite number types, explicit required/nullable flags and typed
defaults. Booleans are not accepted as integers. Missing referenced values,
unknown names, wrong types, unsupported template versions and invalid defaults
fail before any provider request. Original query text remains required.

Templates reuse Nexus `{{name}}` prompt syntax. A placeholder occupying an entire
filter value preserves its native JSON type, including null. Interpolated text
uses JSON spellings for boolean/number/null values. Replacement runs once; braces
inside a supplied value remain literal. No template code, attribute lookup,
format expression or dynamic object key executes. The query prompt must contain
`{{query}}`. Template definitions are limited to 16 KiB, thirty parameters and
eight nested levels; expanded query/filter bounds remain unchanged.

A stored filter is always applied server-side. A caller cannot supply a competing
runtime filter; that call fails instead of weakening or silently replacing the
stored expression. Filters still narrow retrieval rather than grant document
access. Only the selected account's provider permissions authorize documents.

Agent tool descriptions advertise declared parameter names/types and selected
actions. Configured default values stay out of those descriptions and resolve
server-side. Do not put credentials in parameter defaults; keep existing
credential references separate. Native configuration exports/imports retain the
full versioned template. A complete preflight rejects an invalid resource before
saving any earlier resource in the same import.

Exercise both ordinary and templated behavior with the existing local simulation:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia.py --templates
PYTHONPATH=. python -m pytest tests/test_studio_nuclia_templates.py tests/studio_nuclia_acceptance.py -q -o addopts=''
```

This supersedes the earlier missing native parameter/template item. Original
remote-document broker access and live account acceptance remain open. Native
evaluation evidence is described below. Public shell cache version is now 40.

## Importing SAM template settings

The framework helper `nexus.core.studio_nuclia_interop.convert_sam_template`
converts the frozen plugin's `template_parameters`, `prompt_rephrasing` and
`filter_expression_template` object into a native version-1 template. Select
`policy="nexus-strict-v1"` explicitly and review the returned `adaptations` before
placing `result["template"]` on a connector through configuration validate/plan/
apply. The helper performs no writes, credential resolution, Python imports,
package installation or activation.

```python
from nexus.core.studio_nuclia_interop import convert_sam_template

result = convert_sam_template(
    {
        "template_parameters": [
            {"name": "region", "type": "string", "required": True}
        ],
        "prompt_rephrasing": {"template": "Region: {region}; Question: {query}"},
        "filter_expression_template": {
            "field": {"prop": "label", "labelset": "Region", "label": "{region}"}
        },
    },
    policy="nexus-strict-v1",
)
```

This is an explicit adaptation, not a lossless import profile. The selected
policy declares these differences:

- Runtime values use `arguments.parameters`, replacing SAM's top-level values.
- Missing required values fail; an explicit null does not select a non-null default.
- Missing referenced values fail instead of falling back to the original query.
- A configured filter is never removed because a value is absent or empty.
  Explicit null/empty values remain in the filter and are subject to Nuclia's
  validation; no unfiltered request is substituted.
- Whole-value placeholders preserve JSON scalar types. Interpolated boolean,
  number and null values use JSON spellings instead of Python formatting.

The frozen Python model's default `nullable: true` is made explicit. Declared
required/type/default values still pass the native validator. Named scalar
`{name}` placeholders become `{{name}}`; escaped braces remain literal when
unambiguous. Attribute/index lookups, format specifications, conversions,
undeclared variables and ambiguous escaped braces fail. Context expressions,
audit metadata, connection settings and all unknown fields fail rather than
being discarded. Supply only the three template settings above, not an entire
tool descriptor. No original provider defaults, credentials, account ID or
broker configuration are inferred. Importing complete SAM Python tool
descriptors into the agent configuration bridge remains separate work.

The example exercises conversion, native plan/apply, explicit reactivation,
agent invocation and private revision-pinned evidence against the local HTTP
simulation:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia.py --sam-template
PYTHONPATH=. python -m pytest tests/test_studio_nuclia_interop.py tests/studio_nuclia_acceptance.py -q -o addopts=''
```

Existing connector records need no migration. Generated configurations use the
same optional native `template` field and the existing validators. No endpoint,
credential or activation state changes during pure conversion. Live Nuclia,
independent SAM runtime comparison, original-document broker access, context
expression mapping remain unverified or unimplemented as noted above. Native
cited-only evaluation evidence is described below.

## Native evaluation evidence and feedback

Enable **Save private evaluation evidence** in a Nuclia connection to preserve a
reusable evaluation sample for each `ask` result. Configuration-as-code uses
`capture_evaluation: true`; absent/false retains the previous artifact behavior.
Search-only `query` is unchanged. Capturing evidence creates no extra provider
request and never selects or invokes an AI judge.

The private `retrieval-evidence.json` artifact records the effective query,
provider answer/status, optional learning ID, connector/run IDs and cited
paragraph text/identities. It deliberately uses cited passages only. It does not
claim to reconstruct every hidden provider prompt or all uncited retrieval.
Raw provider metadata, credential headers, account tokens and remote source URLs
are excluded. A metadata-only retrieval event links its immutable artifact
revision in the existing execution timeline. The learning ID remains private
artifact/result data, not a trace label or metrics attribute.

After rating a completed answer, open **Response feedback**, select **Create
evaluation case**, and choose a saved retrieval under **Reference context**.
Promotion checks ownership, the rated run, server-generated provenance and the
selected revision. Uploading a similarly named JSON file does not grant that
provenance. Replacing or archiving the report does not change an already pinned
revision. The resulting evaluation stores an editable copy of the cited text
and its source reference. No artifact is fetched when that evaluation runs.

Edit the evaluation and expand **Structured checks and optional AI judge** to
review reference passages or select an explicit judge. Without a judge, phrase
and structured checks retain their existing behavior. A selected judge receives
the copied passages as untrusted reference data and uses that provider/account's
billing. Judge scores are estimates, not a factuality guarantee. Promoting or
editing a case does not invoke the judge. Copied context stays in the private
owner-scoped evaluation and its configuration exports until you remove it.

Context is bounded to twenty passages, 16,000 UTF-8 bytes per passage, 64,000 raw
UTF-8 bytes combined and 96,000 serialized JSON bytes. Evidence JSON is limited
to 160,000 bytes. Oversized capture fails before saving any result; narrow the
retrieval. The report consumes one additional slot in the existing run artifact
limit. Cancellation, revoked access and connector changes retain the same final
publication checks. Trace and public-sharing defaults remain unchanged; sharing
this report requires explicitly selecting it as an artifact.

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia.py --evidence
PYTHONPATH=. python -m pytest tests/test_studio_retrieval_evidence.py tests/studio_nuclia_acceptance.py -q -o addopts=''
```

This provides a native evidence-to-evaluation equivalent for the plugin's REMi
question/answer/context event, with an explicit cited-only scope. Solace topic
publishing remains excluded. Independent REMi scoring parity and live Nuclia or
judge quality are not claimed. Complete SAM Python tool descriptors, context
expressions and original remote-document broker access remain open.

No state or credential migration is required. Existing evaluation cases have no
new required fields; old exports remain readable. New optional evidence/context
fields use existing catalog validation, revision history, configuration
preflight/export and backup services. Public shell cache version is now 40.

## Save original files from citations

Select the optional **download_original** action in a Nuclia connection's
**Enabled actions** field before asking a question. Existing connections remain
query-only by default. After the answer completes, expand its sources and choose
**Save current original**. Studio queues an existing tool job, copies the cited
file into a private artifact and replaces the button with **Open saved original**.
The original paragraph snapshot and its pinned citation revision stay unchanged.

This supports Nuclia file fields (`f/<field-id>`). Text/link/conversation fields
and old citations without the saved connection reference do not advertise an
original-file action. Ask again after enabling the action or changing the saved
connection. Selection uses an owned completed run and saved citation index;
clients cannot supply provider URLs, resource IDs, account credentials or file
paths. The server builds the canonical knowledge-box/resource/file endpoint from
that citation and the unchanged connection.

The download uses the selected Nexus identity's service-account credential and
the provider's [authenticated file endpoint](https://docs.rag.progress.cloud/docs/api/).
This provides native authenticated document access without distributing Nuclia
ephemeral tokens or requiring a separate document broker/account ID. Browser
links remain local authenticated artifact links. No provider token, signed URL,
raw source URL or response header carrying credentials becomes a citation,
artifact filename, receipt or execution event. Credentials remain server-managed.
Only a complete HTTP 200 response is accepted; redirects and partial HTTP 206
responses fail. Existing destination checks, disabled ambient proxy settings,
60-second bounded transfer and a 16 MiB artifact limit apply.

The saved original reflects the remote file when fetched. Nuclia can replace
that file after the answer was generated; the endpoint does not promise a
historical revision. The response explicitly returns `remote_revision_pinned:
false`, the local SHA-256, retrieval time and immutable artifact revision. Read
the separately pinned paragraph for the exact evidence used by the earlier
answer. Unknown media types use a `.bin` filename; supported PDF, Office,
image/text/JSON types use the existing artifact viewers.

Repeated requests for the same owner, citation and saved connection reuse one
private artifact revision. Receipts survive restart. Concurrent reads may reach
the provider twice but publish only one local snapshot. A connection change,
disabled action, revoked project access, unavailable source or cancelled copy
job blocks publication/reuse. Copying consumes an existing run artifact slot.
The frontend polls the same job after submission and never automatically repeats
a POST after a lost response. An explicit retry reuses a completed receipt.

```sh
postman simulation run postman/simulations/studio-nuclia.sim.yaml --port 49873 --output ndjson
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia.py --originals
PYTHONPATH=. python -m pytest tests/test_studio_nuclia_documents.py tests/studio_nuclia_acceptance.py -q -o addopts=''
```

The consumer runs actual binary HTTP, job submission/polling, private revision
downloads and cached retry. Additional HTTP fixtures reject denied, redirected,
partial and oversized responses. Native fixtures cover cross-user access,
changed connections, cancellation, artifact limits, concurrent publication and
restart recovery. No live Nuclia account is used. Orbit returned unrelated
provider endpoints; the official Nuclia file API supplied the route contract.
The existing local simulation was reused; owner mock discovery remains
unavailable while Postman is signed out.

No schema/credential migration is required. New citations optionally retain a
configuration digest, file-field identity and source selector. Older citations
remain readable. The version-1 `source-originals` receipts use existing state,
artifact, backup and authorization services. Public shell cache version is 40.
Complete SAM descriptor/context-expression mapping, augmented or conversation
attachment originals, vendor ephemeral-link equivalence, historical original
revisions and live Nuclia/VM acceptance remain open. Milestones 7 and 8 are not
complete.

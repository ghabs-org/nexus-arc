# Nuclia answers with private citation snapshots

The supported native profile has scoped local acceptance. See the
[final Nuclia checkpoint](STUDIO-NUCLIA-FINAL-CHECKPOINT.json) and
[source reconciliation](STUDIO-NUCLIA-COVERAGE.md) for declared adaptations,
current behavioral evidence and separate live/provider/VM checks. This does not
complete the whole integration catalog or milestone 8.

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

Citations can also refer to provider-augmented paragraphs or complete field text.
Nexus saves exact cited text and its augmentation type in private revisions;
uncited augmented text stays out of answers' evidence and evaluation datasets.
Malformed or ambiguous augmented identities fail before publication. Conversation
text evidence also supports explicit browsing and saving of its attached files.

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

### Conversation files

With `download_original` selected and the connection explicitly enabled, expand
a conversation citation's source list and choose **Browse attachments**. This
reads the selected conversation page's metadata, then displays files with
**Save file** controls. **Next page**, **Previous page** and file-offset controls
keep larger results explicit. Listing alone does not download binary files.
Only message IDs, safe filenames and canonical file choices are saved; message
text and provider attachment URLs/tokens are discarded. Referenced file fields
are supported. Non-file and split references are counted as unsupported.

Saving queues the existing original-document job and captures one private file
revision. Later requests reuse its saved receipt, including after restart.
Choices belong to the source owner and connection configuration. Source changes,
revoked execution access, cancelled jobs and changed selections prevent saving.
Previously cited text remains pinned. Remote historical file availability is
not promised; the first captured local copy is the retained original.

The API still accepts the original two source selectors for file citations.
Conversation listing adds `operation: "list"`, provider `page` and file `offset`;
saving adds the returned opaque `attachment_id`. Caller-supplied URLs, message
IDs or attachment indexes are rejected. Provider metadata must be a complete
HTTP 200 JSON response up to 256 KiB; selected binary files retain the existing
16 MiB limit. Both calls use the configured account and 60-second bounded reader.

`examples/nexus-bot/src/studio_nuclia_attachments.py` exercises two pages, direct
message attachments, a referenced file field and receipt reuse against the local
dependency simulation. Local browser checks cover 390px, 820px and 1280px widths;
physical mobile devices and a live Nuclia account remain separate acceptance.
The [attachment checkpoint](STUDIO-NUCLIA-ATTACHMENTS-CHECKPOINT.json) records scope
and pending work. This extends the original-file slice without claiming whole
plugin acceptance.

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
Nexus-authenticated local evidence snapshots instead. The plugin's external platform REMi
publishing is excluded; native cited-only evaluation evidence is described below.
Live Nuclia generation quality, predicate behavior, account permissions,
original-document access and billing remain unverified. Milestones 7 and 8 remain
open.

## Server-bound template context

Template parameters may declare `context_path: ["workspace", "name"]` or
`["identity", "nexus_id"]`. These values come from the selected server workspace
and authenticated execution identity. They are omitted from the agent's tool
parameter description. A caller cannot override them through `arguments.parameters`.
Missing values follow the existing explicit default/required/nullable rules;
wrong types fail before the HTTP request. Paths contain one to eight plain
object keys, with no attribute access, function calls or executable expressions.

The explicit template converter now maps plain dotted `context_expression`
values to native paths. Other expression syntax is rejected. Applications can
provide `connection_options.enterprise_options.context_resolver(identity,
connector_id)` as a server callback for additional explicitly selected metadata.
No files, environment variables, credential stores or arbitrary run state are
included automatically. Do not place secrets in metadata selected for remote queries.
There is no caller-supplied context argument on the HTTP/tool surface.

Run `examples/nexus-bot/src/studio_nuclia_context.py` against the existing Nuclia
simulation to verify actual forwarding and override rejection before networking.

Sources:

- [Official Ask contract](https://docs.rag.progress.cloud/docs/rag/advanced/ask/)
- [Official filter contract](https://docs.rag.progress.cloud/docs/rag/advanced/search-filters/)
- [Official synchronous response model](https://github.com/nuclia/nucliadb/blob/main/nucliadb_models/src/nucliadb_models/search.py)
- Frozen plugin guide (archived audit evidence)

## Stored typed query templates

### Explicit provider audit metadata

Version-1 templates may include the following optional field:

```json
{
  "version": 1,
  "parameters": [
    {"name": "workspace", "required": true, "context_path": ["workspace", "name"]}
  ],
  "audit_metadata": {
    "enabled": true,
    "fields": {"workspace": "{{workspace}}", "environment": "personal"}
  }
}
```

Both search and generated answers send enabled fields to Nuclia's audit
metadata. Configure only values you intend that provider to receive and retain
in activity logs. Nothing is inferred from files, credentials or arbitrary
runtime state. No audit metadata is sent without explicit configuration.
Setting `enabled` to false retains the templates but sends no audit fields.

Keys are static, bounded strings. Values use declared scalar parameters and
the original `{{query}}`, with the same server-context protection as query and
filter templates. Numeric, boolean and null values become JSON-spelled strings;
empty rendered strings are omitted. The entire dictionary must fit the
provider's serialized 10 KiB limit, including JSON escaping. Missing values,
undeclared parameters, attribute/index expressions and caller-supplied
`arguments.audit_metadata` fail before HTTP. Failed fields never silently
disappear from an enabled request. Audit fields are not automatically copied
into evidence artifacts, citation metadata or traces.

Strict template-settings and Python descriptor conversion now support
`audit_metadata`, including legacy single-brace scalar placeholders. Review
explicitly identifies outbound metadata and the stricter failure behavior.
Existing descriptor bindings with empty, disabled audit settings retain their
previous native shape; existing configurations gain no fields automatically.
File-backed and SQLite state preserve enabled/disabled templates across restart.
Ordinary connection edits preserve the existing versioned template. Use
configuration validate/plan/apply or the catalog API to edit these settings.

Run `examples/nexus-bot/src/studio_nuclia_audit.py --base
http://127.0.0.1:<port>/nuclia` against the existing local simulation. It exercises
search, answers, preflight rejection, disabling and provider failure without
retry. See [audit metadata evidence](STUDIO-NUCLIA-AUDIT-CHECKPOINT.json).

### Query and filter templates

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

## Importing portable template settings

The framework helper `nexus.core.studio_nuclia_interop.convert_template_settings`
converts the frozen plugin's `template_parameters`, `prompt_rephrasing` and
`filter_expression_template` object into a native version-1 template. Select
`policy="nexus-strict-v1"` explicitly and review the returned `adaptations` before
placing `result["template"]` on a connector through configuration validate/plan/
apply. The helper performs no writes, credential resolution, Python imports,
package installation or activation.

```python
from nexus.core.studio_nuclia_interop import convert_template_settings

result = convert_template_settings(
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

- Runtime values use `arguments.parameters`, replacing reference's top-level values.
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
undeclared variables and ambiguous escaped braces fail. Plain dotted context
expressions now become server-bound object-key paths; executable expressions,
audit metadata, connection settings and all unknown fields fail rather than
being discarded. Supply only the three template settings above, not an entire
tool descriptor. No original provider defaults, credentials, account ID or
broker configuration are inferred. Importing complete reference Python tool
descriptors was a separate gap. The supported explicit binding profile below
now connects descriptors to the agent configuration bridge; unsupported settings
still reject the whole import.

### Explicit Python descriptor bindings

`nexus.core.studio_nuclia_descriptors.convert_tool_descriptor` reads descriptor
data with `tool_type: python`, a module ending in `.nuclia_rag_tool`,
`class_name: NucliaRagTool` and `tool_config`. It never imports or executes the
named module. Call it with `policy="nexus-strict-v1"`; inspect its `adaptations`
and disabled native `connector` data before saving that connection through the
ordinary configuration APIs. This works even when the source Python package is
not installed.

Supported settings include a concrete base URL ending in `/api/v1`, knowledge
box ID, `${TOKEN_ENV}` credential reference, bounded `top_k`, rephrasing,
Markdown preference, user prompt and supported typed template settings. The
legacy `context_parameters` name is accepted when `template_parameters` is absent.
Only plain server context paths are supported. No environment value is expanded.
Unresolved endpoint/box variables need manual replacement with reviewed,
credential-free identifiers. Inline credentials are rejected.

The existing external configuration importer accepts an explicit binding:

```json
{
  "nuclia": {
    "CompanyKnowledge": {
      "connector_id": "knowledge",
      "policy": "nexus-strict-v1"
    }
  }
}
```

Place this under `bindings` in an API/CLI request or exported bundle, or paste it
into **Resource mappings** in Studio's Import external dialog. The key is the
descriptor's `tool_name` (default `NucliaRagTool`). The selected connection must
match the endpoint, box, credential reference, template and `ask_settings`, and
expose only `ask`. Conversion binds the agent's connection ID; it does not create
or enable a connection. The review displays the selected policy and all declared
adaptations before the normal plan/apply flow. Source-backed export preserves the
descriptor and binding and checks them again against current native settings.

Fixed `ask_settings` use the same invocation validator. Callers cannot override
different configured values; matching values are accepted. Existing native
connections without these settings retain their ten-result default. The source
descriptor's omitted `top_k` becomes five, matching its frozen default. Editing
ordinary connection fields in Studio preserves fixed settings and templates.

The explicit policy uses private Nexus answer/source revisions, native filenames
and structured citations. Existing bindings retain native retrieval defaults.
To preserve the source tool's neighbouring-paragraph and document-title/summary
context, explicitly add `"preserve_retrieval": true` to the tool's binding:

```json
{
  "nuclia": {
    "CompanyKnowledge": {
      "connector_id": "knowledge",
      "policy": "nexus-strict-v1",
      "preserve_retrieval": true
    }
  }
}
```

Create matching disabled connection data with
`convert_tool_descriptor(source, policy="nexus-strict-v1", preserve_retrieval=True)`.
This fixes `rag_strategies` to two preceding/two following paragraphs plus
hierarchy context with zero extra paragraph characters. Review reports the
expanded provider context and possible usage increase. A connection with
different strategies fails binding before any resource is saved. Conversion
never enables the connection or invokes a provider. The flag and strategies
survive source/native export and file/SQLite restart. Existing bindings without
the flag retain their old shape and behavior.

Native ask settings may separately configure `rag_strategies` using the same
validator. Supported names are `neighbouring_paragraphs` and `hierarchy`, each
once, with at most two strategies. Neighbour counts are integers 0–10; hierarchy
count is an integer 0–1024. Omitted counts normalize to 2/2/0. An empty list uses
the provider's default context. Unknown fields, duplicates, invalid counts and
conflicting caller replacements fail before HTTP. Search (`query`) rejects this
ask-only argument. Cited expanded text retains private revision-pinned artifacts
and augmentation provenance through the existing citation path.

Ephemeral provider links, provider evaluation topics and unknown fields still
require manual adaptation and fail before saving. Non-default presentation and
citation modes require explicit preservation as described below. Audit metadata
has the explicit template support described above. This bounded profile does not
close the whole Nuclia catalog entry. See
[retrieval context evidence](STUDIO-NUCLIA-RETRIEVAL-CHECKPOINT.json). Live retrieval
quality and billing remain separate from local request/citation acceptance.

### Answer files, inline responses and citations

Open a saved Nuclia connection and expand **Answer presentation**. Select
**Customize answer presentation** to choose answer-file output, filename,
description query-prefix length, citation mode, inline links and separate
citation details. Settings save through the existing catalog validator. Turning
customization off removes the formatting options; previously fixed provider
settings, including citation mode, remain unchanged. New connections do not
gain presentation settings automatically. Private source snapshots remain
available even when the Markdown answer file is disabled.

Configuration-as-code uses optional connector data:

```json
{
  "answer_options": {
    "output_response_as_artifact": false,
    "output_filename_base": "policy-notes",
    "artifact_description_query_max_length": 150,
    "inline_citation_links": true,
    "include_citations_in_tool_response": true
  },
  "ask_settings": {"citations": "llm_footnotes"}
}
```

`output` always retains the provider's original text, including original model
footnote markers. Inline mode adds Markdown `response_content`. File mode adds
`response_artifact` and saves the formatted answer with its sources in the
chosen Markdown filename. Only the configured effective-query prefix enters its
private description. The optional `citations` result lists source artifact IDs,
exact revisions, provider pages and authenticated local URLs. Native structured
`sources` remain available independently of this compact-field flag.

An agent or explicit connector call can set `arguments.output_filename_base` for
one answer, for example `{"query":"What is the return policy?",
"output_filename_base":"meeting-notes"}`. Studio saves `meeting-notes.md` locally.
Omit the argument or use null to retain the saved default. This works with legacy
answer files and configured presentation; it does not change the output mode,
saved settings, cited sources or evaluation evidence. Inline-only output rejects
a non-null name. Search rejects the field. The argument is never sent to Nuclia.

The existing filename rules apply: a bounded basename with optional `.md` suffix,
no paths, controls, traversal or reserved device names. Unsafe names fail before
HTTP rather than silently choosing another name. Tool descriptions advertise the
argument only when the connection enables `ask` with file output. The end-to-end
example accepts `--answer-name meeting-notes`; the presentation consumer supports
the same flag. No new frontend, state or credential format is required.

The three fixed citation modes are **Source markers** (`default`), **No citations**
(`none`) and **Model footnotes** (`llm_footnotes`). None mode does not turn uncited
retrieval into evidence. Footnote mode validates the documented provider mapping
and retains only referenced sources. Mappings must resolve to returned
paragraph/field IDs; positional-only context indices are rejected, never guessed.
The selected provider/model must support
this mode; no fallback or repeat request occurs when it fails. Unknown references,
malformed offsets/mappings and capacity exhaustion fail before publication.
Generated source links point only to private, pinned Nexus artifact revisions.

Filenames use safe basenames up to 100 characters, with an optional `.md` suffix;
paths, control characters and reserved platform names reject. Query-prefix
length is an integer from 1 to 1000. Formats and limits are checked during save,
configuration preflight and execution. Source/native export and file/SQLite
restart preserve the settings. Ordinary connector edits retain unrelated fixed
request settings and templates.

For descriptor conversion, add `"preserve_presentation": true` alongside the
connection ID and strict policy in its `bindings.nuclia` entry. Create matching
connection data with `convert_tool_descriptor(source, policy="nexus-strict-v1",
preserve_presentation=True)`. Review declares the native artifact/link adaptations.
Without the flag, the previous strict profile and non-default-setting rejections
remain unchanged. This option can be combined with `preserve_retrieval`.

Run `examples/nexus-bot/src/studio_nuclia_presentation.py --mode llm_footnotes
--inline --base http://127.0.0.1:<port>/nuclia` against the local simulation.
See [presentation evidence](STUDIO-NUCLIA-PRESENTATION-CHECKPOINT.json) for scoped
HTTP, ownership, restart and responsive checks. Live model quality and billing
remain unverified.

```sh
postman simulation run postman/simulations/studio-nuclia.sim.yaml --port 50950 --output ndjson
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia_descriptors.py
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia_descriptors.py --preserve-retrieval
PYTHONPATH=. python -m pytest tests/test_studio_nuclia_descriptors.py -q -o addopts=''
```

No persisted record rewrite or credential migration is required. Old connections,
imports and exports remain readable. New configuration may include optional fixed
ask settings, explicit descriptor bindings and conversion-report adaptations.

The example exercises conversion, native plan/apply, explicit reactivation,
agent invocation and private revision-pinned evidence against the local HTTP
simulation:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia.py --portable-template
PYTHONPATH=. python -m pytest tests/test_studio_nuclia_interop.py tests/studio_nuclia_acceptance.py -q -o addopts=''
```

Existing connector records need no migration. Generated configurations use the
same optional native `template` field and the existing validators. No endpoint,
credential or activation state changes during pure conversion. Live Nuclia,
independent reference runtime comparison, original-document broker access, context
expression mapping remain unverified or unimplemented as noted above. Native
cited-only evaluation evidence is described below.

## Native evaluation evidence and feedback

### Choose the saved context

**Save private evaluation evidence** remains off by default. When enabling it,
choose **Evaluation context** in the connection editor:

- **Cited sources only** keeps the existing version-1 format and default behavior.
- **All retrieved paragraphs** explicitly saves original retrieval paragraphs,
  including uncited text, in private version-2 evidence. Augmented-only blocks
  remain outside this original-retrieval scope; their ordinary citation snapshots
  are unchanged.

Configuration uses `capture_evaluation: true` with
`evaluation_context: "retrieved"`. Choosing the scope without enabling capture
records nothing. Turning capture off retains the selected scope for later use.
The provider's existing response supplies the paragraphs; no extra request occurs.

Version 2 uses `format: "nexus-retrieval-evidence/v2"` and
`context_scope: "retrieved-paragraphs-only"`. Each source includes a `cited`
boolean, exact text digest, identity and available provider page. Uncited text
stays in the private evidence artifact; it does not enter normal tool output,
chat source navigation or execution events. Citation mode `none` can still
capture explicitly requested retrieval context, with every source marked uncited.
It never manufactures citations from those paragraphs.

Both versions support owner-scoped, revision-pinned feedback promotion. The new
reader also verifies version-2 scope, source/context alignment, cited flags and
digests. Context is copied into an editable evaluation case only after promotion
checks. Only an explicitly configured and executed judge receives that copy as
untrusted reference material. Capturing or promoting never starts judging.

Existing bounds apply to both versions: 20 passages, 16,000 UTF-8 bytes per
passage, 64,000 combined text bytes, 96,000 encoded context bytes and 160,000
report bytes. Oversized or unavailable retrieval fails before any artifact is
saved; it never truncates or silently substitutes cited-only context.

The strict descriptor binding optionally accepts `preserve_evaluation: true`.
With a configured evaluation topic, conversion maps the original payload to
native private full-retrieval capture and reports the transport/privacy change
before review. The topic is never published or used as a destination. A matching
native connection is required. Only static topic text and plain
`interaction_id`, `tool_name`, `learning_id` placeholders are portable. Empty or
absent topics enable no extra capture; old bindings retain their rejection of
topic settings unless explicitly adapted. Original broker transport remains
excluded from this independent Nexus implementation.

The frozen Nuclia tool constructs and publishes an evaluation payload; it does
not compute evaluation scores itself. Native private capture, feedback promotion
and explicitly selected judging provide the corresponding Nexus flow. This is
not a claim about independent judge quality or downstream scoring equivalence.

No existing files or records are rewritten. Version-1 captures remain readable.
File and SQLite backup/restore tests retain both formats and their private
revision provenance. Older releases can continue using cited-only capture;
version-2 promotion requires the new reader. Rollback preserves version-2 bytes
and should disable new capture or select cited-only mode until upgrading again.

Run `examples/nexus-bot/src/studio_nuclia.py --retrieved --base
http://127.0.0.1:<port>/nuclia` against the local simulation to exercise capture,
feedback promotion and a synthetic explicitly selected judge. See
[full-context evidence](STUDIO-NUCLIA-CONTEXT-SCOPE-CHECKPOINT.json).

### Default cited-only capture

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
question/answer/context event, with an explicit cited-only scope. external platform topic
publishing remains excluded. Independent REMi scoring parity and live Nuclia or
judge quality are not claimed. Descriptor settings outside the explicit binding
profile, executable context expressions and original remote-document broker
access remain open.

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

This supports Nuclia file fields (`f/<field-id>`). Conversation fields
(`c/<field-id>`) offer the explicit attachment browser described above.
Text/link fields and old citations without the saved connection reference do not
advertise an original-file action. Ask again after enabling the action or changing the saved
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
The bounded native profile's local review is complete. Broader expression
compatibility remains explicitly unsupported, and independent runtime comparison
remains unverified. The [source reconciliation](STUDIO-NUCLIA-COVERAGE.md)
separates intentional private-access adaptations from additional capabilities:
general non-file/split originals and historical remote originals are not promises
of the inspected frozen file-only citation implementation. Live Nuclia/VM
acceptance remains separate. Milestones 7 and 8 are not complete.

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
runtime parameter declarations and reusable filter/rephrasing templates still
need dedicated configuration/import acceptance. Original remote-document viewing
through a broker and ephemeral tokens remains separate work; this slice provides
Nexus-authenticated local evidence snapshots instead. The plugin's Solace REMi
publishing is excluded; native evaluation/trace mapping still needs acceptance.
Live Nuclia generation quality, predicate behavior, account permissions,
original-document access and billing remain unverified. Milestones 7 and 8 remain
open.

Sources:

- [Official Ask contract](https://docs.rag.progress.cloud/docs/rag/advanced/ask/)
- [Official filter contract](https://docs.rag.progress.cloud/docs/rag/advanced/search-filters/)
- [Official synchronous response model](https://github.com/nuclia/nucliadb/blob/main/nucliadb_models/src/nucliadb_models/search.py)
- [Frozen plugin guide](https://github.com/SolaceLabs/solace-agent-mesh-core-plugins/blob/7599710a38318a336c47cf605e4774d4add24d8e/sam-nuclia-tool/docs/nuclia_rag_tool_user_guide.md)

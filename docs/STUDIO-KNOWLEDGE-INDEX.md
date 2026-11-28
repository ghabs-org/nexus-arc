# Knowledge retrieval, cached vectors and queued sources

This additive change preserves project/package formats, plain output, immutable
citations and existing lexical/semantic/hybrid search routes. No existing document
record is rewritten. The local derived index gains a bounded vector-cache table;
legacy indexes create it lazily and retain their FTS5 content.

Semantic/hybrid requests use only the explicitly configured embedding adapter.
Indexing uploaded bytes never calls an embedding endpoint. Document vectors are
cached locally after an explicit semantic/hybrid search; query text/vectors are
not persisted. Cache identity covers workspace index, project, requesting account,
configured endpoint/model identity, document revision and content hash. A new
optional server-side embedding `cache_revision` invalidates vectors when a model
is replaced under the same name. Credentials remain environment references.

Vectors must have 1–8192 finite numeric dimensions; booleans and mixed dimensions
are rejected. Requests are batched within existing adapter bounds. Cosine ranking
uses normalized vectors; hybrid search uses reciprocal-rank fusion. Derived cache
is capped at 4096 vectors and 64 MiB of vector JSON, evicting least-recently-used
rows. Retrieval supports up to 4096 current document chunks for vector ranking;
larger projects can use lexical search. A second bound limits one retrieval to
4,194,304 numeric vector values, including its query. Corrupt cache rows are recomputed through
the same selected provider. Deleting/restoring a derived index never authorizes
or uploads a document automatically.

An unavailable configured local endpoint falls back visibly to local lexical
search. Authentication, malformed responses, TLS validation and invalid vectors
remain explicit errors. API-provider failures never select another service or
silently change search method. Search responses add optional versioned retrieval
settings/cache counts; notices also appear in chat. These are additive fields.
`GET /api/studio/projects` also returns an optional versioned `retrieval_options`
descriptor. It exposes provider kind and processing notice, never endpoint,
credentials or model configuration. Chat and project search disclose API text
processing and possible usage charges before submission. Saved conversation
responses retain their actual fallback notice when reopened.

Queued retrieval pins project instructions, search settings and original source
revisions. Execution revalidates project and source authorization without
rerunning retrieval: later replacement does not substitute new content, while
archive/revocation fails before sources reach a model. Index publication checks
job identity, cancellation and existing SQL lease fencing; a late extraction
thread cannot publish over a newer retry. Interrupted jobs remain explicitly
retryable through the existing indexing service.

Text, Markdown, CSV, PDF, DOCX, PPTX and XLSX extraction have bounded background
fixtures. Empty documents and extraction limits fail visibly rather than publish
partial searchable content. PDF indexing accepts at most 50 pages and below the
128000-character extraction limit; larger files must be split. Source preview
navigation remains separately bounded and can inspect later pages. No OCR is
added by this change.

Postman simulations in `postman/simulations/` exercise the real HTTP adapter using
a synthetic local compatibility service derived from the checked-in contract.
No owning service/workspace was identified for this operator-selected endpoint;
owner-mock search was skipped. Nothing was published. These checks establish client behavior,
not real model quality or external-account readiness.

Reproduce with two separate local terminals (install the existing documents extra
for Office/PDF fixture coverage):

```sh
postman simulation run postman/simulations/studio-knowledge.sim.yaml --port 49641 --no-history
PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge_vectors.py --url http://127.0.0.1:49641/embeddings/v1
```

For the real ten-second client timeout, run the delayed simulation separately:

```sh
postman simulation run postman/simulations/studio-knowledge-timeout.sim.yaml --port 49642 --no-history
PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge_vectors.py --unavailable-url http://127.0.0.1:49642/embeddings/v1 --expect-timeout
```

These examples accept loopback endpoints only. Fixtures also exercise queued SQL
controller restart, old/new source revisions, archive/ownership/account revocation,
expired leases and cancellation followed by retry while the old thread remains
alive. Abrupt subprocess exits additionally cover uncommitted FTS, committed FTS before
ready metadata, and ready metadata before job completion. The package suite checks
size preflight and injected storage failure. These checks do not certify multi-host
failover, hosted identity, real model quality or personal-VM restore.

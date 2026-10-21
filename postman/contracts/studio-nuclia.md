# Nuclia opt-in generation contract

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

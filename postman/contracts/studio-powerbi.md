# Power BI catalog semantics

Authority: Microsoft Execute Queries v1.0 and the official plugin executable tool
at frozen commit `7599710a38318a336c47cf605e4774d4add24d8e`.

Existing Nexus `powerbi` connector contract remains:

- `dataset_id`, optional `workspace_id`, `token_env`, `enabled`, selected `read/query` actions.
- GET `/v1.0/myorg/[groups/{workspaceId}/]datasets/{datasetId}` for `read`.
- POST the same path plus `/executeQueries` for `query`.
- One `queries: [{query: DAX}]` with `serializerSettings.includeNulls: true`.
- Bearer credential resolved through current Nexus account/environment references.
- No caller-supplied impersonation or endpoint; no automatic retry/account fallback.

HTTP 200 is insufficient for query success. Response, query-result or table-level
`error` objects must fail, even with returned rows. One result and one table are
required; rows must be objects. Valid empty row arrays, nulls, qualified names and
information-protection labels retain their original shape. Dataset metadata must
match the selected identity. The existing 256 KiB result bound still applies.

Run `postman/simulations/studio-powerbi.sim.yaml` and explicit
`tests/studio_powerbi_acceptance.py`. The application, agent loop and HTTP decoder
run for real against the local dependency stand-in. Fixture markers select
synthetic failure responses; `/fixture/attempts` is instrumentation, not a Power
BI endpoint. Unsupported operations return 501. No live credentials, dataset or
external action is used.

The fixture was authored from the official contract because the CLI is signed
out and owner-mock search is unavailable. Orbit returned HTTP 403. Live identity,
RLS, tenant settings, tokens, source freshness and model correctness remain
unverified. See `docs/STUDIO-POWERBI.md` for setup and compatibility.

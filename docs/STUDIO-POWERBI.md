# Power BI dataset queries

The official Power BI catalog equivalent uses Nexus's existing connection tools,
credential references, agent tool loop and execution permissions. Select one
dataset and optional workspace; the connector reads dataset metadata and sends
one DAX query per call. It does not install MSAL, change Studio login, initiate a
new provider login flow, or silently refresh an expired token.

## Configuration

1. Open **Power BI** in the integration catalog and inspect its disabled template.
2. Supply the dataset UUID, optional workspace UUID and a scoped bearer-token
   environment reference (`POWERBI_TOKEN` by default). Keep the credential in
   existing server-managed account settings or server environment references.
3. Enable the tenant's Execute Queries setting. The selected account needs
   dataset read/build permission and the appropriate Power BI dataset API scope.
4. Explicitly enable `read` and `query`, then enable the connection.
5. Add the generated connector tool to an agent's selected tools, or invoke it
   from a workflow. Agent instructions should identify the selected data model,
   explain its measures, and require errors to be reported instead of inferred.

`read` fetches only the selected dataset's metadata. `query` takes:

```json
{
  "action": "query",
  "arguments": {"query": "EVALUATE ROW(\"Revenue\", 42, \"Missing\", BLANK())"}
}
```

Only the configured dataset/workspace appears in the provider URL. Callers cannot
supply another dataset, impersonated account, credential header or service URL.
Queries are sent as one DAX expression with `includeNulls: true`. Dataset access
and RLS policy remain enforced by the configured provider identity. Expired or
revoked credentials fail; Nexus never selects another account automatically.

## Complete and incomplete results

Successful responses retain the provider's JSON shape: `results`, `tables`, rows,
qualified column names, explicit nulls and information-protection labels. Metadata
must identify the configured dataset. Empty row arrays are valid.

Power BI can return HTTP 200 with an error and limited data. Nexus now rejects
errors at response, query-result and table levels. Missing/malformed results,
multiple query results/tables and non-object rows also fail. Partial rows never
reach an agent's synthesis prompt. A failed tool makes the run fail rather than
letting the model describe incomplete data as a complete answer.

Nexus retains its 16,000-byte query and 256 KiB connector-result limits, which are
stricter than some provider limits. Oversized results fail; narrow the DAX query.
Redirects, invalid JSON and non-success HTTP responses fail with the existing
sanitized integration error. No automatic retry or alternate provider is used.
HTTP success with no provider error is not proof that a model's underlying source
data is current. Nexus preserves the selected provider's result and labels.

## Local acceptance

Start the repository simulation:

```sh
postman simulation run postman/simulations/studio-powerbi.sim.yaml --port 49872 --output ndjson
```

Then consume the actual connector and agent runtime:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_powerbi.py
PYTHONPATH=. python -m pytest tests/test_studio_powerbi.py tests/studio_powerbi_acceptance.py -q -o addopts=''
```

The example maps only the fixed Power BI hostname into loopback through the
existing injectable request-client seam. Requests still use Nexus's real HTTP
encoder, time/size bounds, redirect policy and JSON decoder. It uses a disposable
workspace, synthetic credentials and a deterministic provider. No Microsoft
account or external dataset is contacted.

The simulation checks dataset/workspace paths, bearer credentials, exactly one
query, null-preserving serialization and absent impersonation. Nine failure cases
cover three nested provider errors, multiple tables, malformed rows, rejection,
redirect, invalid JSON and oversized results. A successful natural-language agent
query uses one tool call and one synthesis. A partial table makes the next run
fail before synthesis. Request counts prove one attempt per explicit invocation.

The peer's query marker strings and `/fixture/attempts` route are test-only
instrumentation. The fixture follows Microsoft's documented API shapes. No
published owner mock was available because the Postman CLI is signed out. Orbit
returned HTTP 403, so official vendor documentation and frozen plugin source
provided contract evidence.

## Compatibility and remaining validation

No saved connector format, route, optional dependency or credential reference
changes. Valid response objects remain unchanged. Custom injected request clients
must return documented Power BI metadata/query shapes. Old synthetic responses
that claimed success without a table now fail deliberately. No storage migration
or change to Nuclia or other enterprise connector response handling is required.

The frozen plugin's executable tool is `execute_powerbi_query`. Its dedicated
MSAL device-code UI/cache is replaced here by configured Nexus credential
references. Token acquisition/renewal remains the operator's existing credential
workflow. No report-browser or automatic identity-provider login is claimed.
Actual tenant permissions, RLS/SSO restrictions, token expiry/rotation, model
quality and report/data freshness still need a configured live account. This
checkpoint does not finish milestone 7 or the complete reference plan.

Sources:

- [Official Execute Queries contract](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries)
- Frozen Power BI executable tool (archived audit evidence)
- Frozen catalog README (archived audit evidence)

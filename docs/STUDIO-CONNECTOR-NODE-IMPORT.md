# Reviewed connector/tool workflow imports

Portable workflow conversion now accepts workflow-level `toolsets`, connector/tool
nodes, and explicitly authored approval nodes through the existing owner-only
validate/plan/apply interface. This profile binds existing native resources; it
never imports accounts, endpoints, specifications, credentials or deployment flags.

```yaml
nodes:
  - id: approve
    type: approval
  - id: record
    type: tool
    connector: service
    tool: record
    depends_on: [approve]
    input: {reply: '{{workflow.input.reply}}'}
output_mapping: {result: '{{record.output}}'}
```

Bindings select an existing OpenAPI connector and operation:

```json
{
  "connectors": {"service": "existing-connector-id"},
  "operations": {
    "service/record": {"operation_id": "savedOperationId", "input": "body"}
  }
}
```

`input` must explicitly choose `body` or `parameters`. The saved connector method
chooses native `read` for GET/HEAD/OPTIONS and `invoke` otherwise. The operation and
required action must already be selected in that connector. Unknown or ambiguous
fields, missing bindings, unselected operations and unsupported connector types
reject the entire executable document. No network call occurs during conversion
or native plan/apply. Conversion never enables a connection.

Write nodes require a directly preceding `type: approval` node in the authored
source. No gate is silently inserted or approved. Native workflow validation
rejects missing gates and automatic mutation retries. Approved writes reuse the
existing effect receipts and uncertainty handling; a received 503 never proves
that a remote mutation did not happen. Review uncertain effects before retrying.

Workflow-level toolsets use `bindings.toolsets` to resolve existing native toolset
IDs. A builtin `tool_name` still needs `bindings.tools` and membership in declared
workflow tools or toolsets. Knowledge references continue to use `skillRefs` and
explicit `bindings.skills`. The included complete reviewed bundle demonstrates
local tooling, a connector read, an agent with a skill, approval and one write.
Export preserves source syntax and explicit resource bindings; reimport validates
current resources and rejects conflicting alias mappings.

## Local acceptance example

Start the repository-owned simulation; this does not represent a vendor account:

```sh
postman simulation run postman/simulations/studio-interop-connectors.sim.yaml --port auto --no-history --output ndjson
```

Use the printed loopback address and `/service` suffix:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_connectors.py --url http://127.0.0.1:PORT/service
PYTHONPATH=. python examples/nexus-bot/src/studio_interop_connectors.py --url http://127.0.0.1:PORT/service --fail-write
```

The example uses a synthetic model and disposable state. It asserts zero requests
from conversion/plan/apply, one lookup and zero writes while waiting, then exactly
one write and one total model call after approval. The failure mode verifies one
received write returning 503, with an unresolved effect receipt and no automatic
replay. The simulation's `/stats` counters and request log provide independent HTTP
evidence. Stop the simulation after testing.

This is a bounded OpenAPI profile. MCP and other connector-node mappings are not
added here. Combined required operation parameters and body need explicit manual
native adaptation; non-JSON bodies are rejected. External deployment fields still
reject. The original external bundle is not a drop-in import: existing native
bindings, deployment review and authored approval gates remain required. No live
account, real model, VM or desktop rebuild was used. No state migration is needed.

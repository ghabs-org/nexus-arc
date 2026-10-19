# Structured workflows — local checkpoint, 19 October 2026

This checkpoint covers Milestone 5 clauses 1–4. It does not complete Milestone 5:
External configuration conversion, complete Quick Build resource lifecycle and CLI
lifecycle acceptance remain open.

## Author and run

New canvas workflows use version 2. Add agents, registered tools, integration
actions, decisions, delays, approvals, collection maps, bounded loops, saved nested
workflows, deterministic switches and operator input. Select a node to edit its
settings. **Edit mapped steps**, **Edit repeated steps** and handler controls open
nested canvases; **Back to parent** retains the parent connections and settings.
Invalid JSON stays in its draft field and prevents leaving or saving that scope.
Saving from a nested scope saves the complete root workflow.

Input/output schemas and mappings use JSON fields. A reference is an object such
as `{"ref":"input"}` or `{"ref":"nodes.count"}`. Node output references require an
upstream connection or `depends_on`; graph output mappings may refer to any graph
node. Map bodies additionally receive `item` and `index`. Loop bodies receive
`iteration` and `previous`; their continuation condition receives `result` and
`iteration`. Named child workflows have their own input and node namespace.
Completion handlers receive the graph result; error handlers receive an object
with the original body exception type in `error`.

Switches use explicit `left`, `op`, `right` conditions: `eq`, `ne`, `gt`, `gte`,
`lt`, `lte`, `contains`, `in`. No expression evaluation or generated code is used.
The first matching switch case wins. A loop runs its body first, then continues
while its condition is true; a still-true condition at its limit fails visibly.
Map results retain input order even when concurrent items finish in another order.

**Run workflow** accepts optional structured JSON input separately from the task
text. Operator-input requests show **Provide input**; approval gates show approval
controls. Both can be pending in one workflow. Reading Activity or reconnecting
never submits the task again. Scroll the canvas on small screens; keyboard arrow
keys move focused nodes. Contracts/settings stack within the existing responsive UI.

Existing version-1 graphs and ARC YAML workflows retain their formats and task
interpolation. Opening a legacy canvas never upgrades it silently; advanced-node
creation is disabled there. Create a new workflow to use structured nodes.

## Administrator policy

Pass `workflow_limits` to `StudioService`, or include this section in the existing
private runtime configuration file (outside the workspace, mode 0600):

```json
{
  "workflow_limits": {
    "map_items": 100,
    "map_concurrency": 4,
    "loop_iterations": 100,
    "nesting_depth": 10,
    "node_instances": 10000
  }
}
```

| Setting | Default | Hard ceiling |
|---|---:|---:|
| Map items | 100 | 10,000 |
| Parallel leaf operations | 4 | 32 |
| Loop iterations | 100 | 10,000 |
| Graph nesting depth | 10 | 32 |
| Executed node instances per run | 10,000 | 100,000 |

Every value must be an integer from 1 through its ceiling. Unknown keys and
invalid values fail before Studio state is created. Omitted node limits default
to 100/4/100, clamped to lower administrator limits; explicit node settings may
use the configured administrator maximum. The root occupies nesting level one.
Maps use a bounded worker pool, not one async task per item. A shared run semaphore
bounds concurrent leaf operations across nested maps and branches. Other existing
bounds remain: 40 nodes/160 edges per graph, per-node timeouts 1–3600 seconds and
at most three permitted retries.

`GET /api/studio/workflow-policy` is a versioned, read-only view of effective
limits/defaults/ceilings. There is no HTTP policy mutation. Client-supplied private
limits are discarded. Queued runs pin their administrator limits, agent snapshots
and named workflow definitions. Changing configuration or deleting the original
child file cannot change that queued run. Subsequent new runs use current policy.

## Validation, effects and recovery

Graph validation checks names/coordinates, DAG dependencies, reference scope,
condition structure, schema syntax and all pinned named graphs before execution.
External schema references are rejected. Typed schemas require the optional
`studio-connectors` extra. Graph input is checked before its body; each resolved
node input/argument is checked before that node's invocation, and output schemas
are checked before publishing completion. Unknown model output types cannot be
inferred in advance; a later type failure does not undo earlier independent work.

Only delays and API agents without selected tools permit automatic retries.
Coding CLI agents, tool-capable API agents, tools and integration actions cannot
be retried automatically. Generation retries can still consume provider quota.
Mutating integrations retain direct approval gates and the existing durable
uncertain-effect review. Cancellation/timeout cannot undo an external effect.

Completed nodes are reused after restart. Root and nested graph outcomes are
checkpointed before their completion/error handlers run. A handler approval pause
therefore resumes its handler without repeating a failed or successful body.
The original failure remains a failed run after error-handler completion. The
same rules apply to named children, map bodies and handler graphs. A successful
handler does not replace the body result.

A tool started without durable completion fails with an uncertain-outcome notice;
it is not automatically replayed. The local MCP lost-acknowledgement fixture
records exactly one real subprocess effect, pauses in an error handler, restarts
and resolves the existing effect for review without a second invocation.
These guards prevent automatic repeats; they cannot guarantee exactly-once
execution in third-party systems.

Existing graph checkpoints remain readable. The private payload policy and
`limits`, root `outcome`, and optional per-scope `scope_outcomes` fields are
additive. No existing record is rewritten at upgrade. Legacy started-tool guards
still protect checkpoints created before outcome fields existed. Older queued
runs without a pinned policy use the administrator policy when first executed.
The root body has its existing 1800-second timeout; handlers and their work retain
per-node bounds. No whole-run deadline for all handler chains is claimed.

## Reproduce

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_structured_workflows.py
PYTHONPATH=. python examples/nexus-bot/src/studio_structured_workflows.py --serve
python -m pytest tests/test_studio_workflow_authoring.py tests/test_studio_workflow_v2.py tests/test_studio_typed_nodes.py -q -o addopts=''
node --test tests/studio_graph_browser.test.cjs tests/studio_browser.test.cjs tests/studio_execution_browser.test.cjs
```

The consumer uses synthetic generation, real framework word counting and disposable
state. It maps two entries, loops through a pinned named workflow, restarts at an
approval and retains its original policy despite lower controller settings.
The server option provides the same consumer for browser authoring without an
external account. The checked-in read-only HTTP contract is
[studio-workflow-policy.yaml](../postman/contracts/studio-workflow-policy.yaml).

Local fixtures include all eleven node kinds in one graph, real stdio MCP discovery,
ordered map concurrency, exact default limits, API-generation retry, actual tool
timeout, root/nested handler restarts, malformed mappings/schemas and lost effect
acknowledgement. Real subscriptions/API accounts, hosted identities, physical
phones and personal-VM failover remain separate validation work.

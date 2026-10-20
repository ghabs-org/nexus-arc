# Imported collection-body routing

External configuration import compiles bounded collection bodies into the existing
version-2 workflow runtime. A map or loop body may be a switch or decision with
local routed branches and dependency joins. Each invocation keeps its own node
checkpoints, item/index or iteration/previous values, and captured outer outputs.
A routed target must exist inside the body and explicitly depend on its router.
External dependencies belong on the collection controller.

The collection returns its root body node's output. A switch root therefore returns
selection metadata; downstream branch effects and joins remain visible in the
execution history. Mapping preserves item order. Intentional pruning returns nil
in that item position. Switch and decision routing retain the existing approval,
retry, instance-budget and uncertain-effect policies; imports do not create a
second execution engine.

Original source can be exported and reimported while its compiled graph still
matches. Unsupported constructs fail before configuration plans or effects.
A collection template can also serve one or more exit phases when its inputs are
valid in each role. Each phase keeps separate durable receipts and approvals.
Restart and cancellation never repeat the completed collection body; named child
revisions remain pinned. Map/iteration-only variables are rejected in an exit
context before saving.

Concurrent shared loop templates, mixed map/loop ownership and broader expression-
language compatibility remain explicit limitations. Named nested workflows remain
available for those designs.

No persisted definition rewrite is required. New skip-reason metadata and image
revision pins are additive. Existing graph/YAML definitions remain readable;
existing server processes need restart to load new compiler behavior.

The framework consumer is
`examples/nexus-bot/src/studio_extended_integrations.py`. It imports and runs an
ordered two-item routed collection using only a local built-in tool. Required
workflow tests also cover per-item decision routing, joins, nil pruning,
original-source round trips, malformed target preflight, bounded collections and
checkpoint recovery. This is local Nexus acceptance, not an independent live comparison
with an external runtime.

## Routed exit handlers

An `on_exit` root may now own a bounded switch/decision route, its branch nodes
and dependent joins. The compiler keeps these nodes in the exit graph so they
never execute as ordinary main-graph work. Each routed target must depend on its
router. Cross-scope dependencies reject before saving; acknowledged main results
can be captured explicitly in handler inputs.

Always runs before the matching success/failure/cancel phase. Each intentional
phase keeps separate node receipts, approvals and named-child snapshots. Completed
branch outputs are exposed by their source node IDs to final output mappings. A
pruned or unsuccessful branch cannot retain an earlier phase's value. The handler
root's output keeps its existing meaning; distinct one-node handler shapes and
checkpoint paths remain unchanged.

Cleanup failure remains nonfatal to the original workflow outcome. A restart
resumes the saved phase and child definitions, preserves completed effects, and
never automatically repeats an unacknowledged tool result. Approvals still use
the existing inbox and current execution identity.

This extends the version-2 graph's existing scoped handler bodies; no persisted
record rewrite or frontend dependency is needed. New multi-node exit graphs need
the updated backend. The example consumer and required local HTTP, routing,
file/SQLite restart, cancellation and uncertain-effect tests cover this profile.
They do not establish independent equivalence with a running SAM installation.

## Image evidence across scopes

Decision images can capture completed main results and results from an earlier
exit phase. Local body nodes and captured outer nodes have separate acknowledgement
scopes, so reusing a node name in a nested graph cannot change which image is
trusted. Map/loop invocations carry only their captured upstream receipts.

Restart reconstructs image acknowledgement from existing scoped checkpoints;
it does not repeat completed work or require a state migration. An intentionally
pruned source omits its entire image entry. Failed, unfinished, unresolved or stale
sources stop that decision before provider invocation, even if an earlier phase
left an output value. Cleanup failure remains visible and nonfatal to the main
outcome. Existing artifact ownership, revision pins and image limits still apply.

`examples/nexus-bot/src/studio_cleanup_images.py` prepares this workflow through
the shared importer using local built-in tools. It starts no run. Review the
selected API model and its billing before explicitly running the installed
workflow in Studio. The extended integration example also exercises this path
against the existing local Postman decision peer.

This locally verifies the supported owned-artifact profile. Broader SAM artifact
URI namespaces, source settings and independent SAM/provider acceptance remain
separate limitations. Existing Studio servers need restart to load the change.

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
Concurrent shared loop templates, mixed map/loop ownership, cross-role exit
handler templates and broader expression-language compatibility remain explicit
limitations. Named nested workflows remain available for those designs.

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

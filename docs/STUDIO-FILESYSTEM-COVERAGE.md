# Filesystem MCP source reconciliation

This source audit reconciles native behavior with the pinned `@modelcontextprotocol/server-filesystem@2.0.0` MCP server contract.

The [filesystem checkpoint](STUDIO-FILESYSTEM-CHECKPOINT.json) and [setup documentation](STUDIO-CATALOG-SETUP.md) record local acceptance, security controls, and parameter mappings.

## Inspected source contracts and native implementations

| Component | Frozen / Specification contract | Native equivalent | Evidence |
| --- | --- | --- | --- |
| Server identity | `@modelcontextprotocol/server-filesystem` pinned at version `2.0.0` | Configured via `nexus/core/studio_catalog_setup.py` and `nexus/core/studio_recipes.py` | `tests/test_studio_catalog_setup.py` |
| Runtime execution | Minimal system PATH execution, Node 24+ requirement | Absolute Node runtime and optional script path configured as `[script, workspace_root]` args | `tests/studio_filesystem_acceptance.py` |
| Tool discovery | Exposes filesystem tools (`read_text_file`, `write_file`, `list_directory`, etc.) | Discovered via `connections.execute(action="discover")`, tools stored in connector record | `examples/nexus-bot/src/studio_filesystem.py` |
| Access control | Directory boundary isolation | Authorized only for configured workspace root; sibling folders and relative directory traversal (`../`) denied | `examples/nexus-bot/src/studio_filesystem.py` |
| Symlink protection | Prevents symlink target escapes outside allowed directory | Explicit verification denying symlinks resolving outside workspace | `tests/studio_filesystem_acceptance.py` |
| Write guard | Mutable file updates require human-in-the-loop confirmation | Requires explicit `confirm=True` via reviewed integration workflow; model self-approval denied | `nexus/core/studio_connections.py` |
| Session resilience | Per-call and persistent session lifecycles | Clean recovery after rejected or invalid requests in both session modes | `examples/nexus-bot/src/studio_filesystem.py` |

## Policy guarantees

1. Studio never invokes `npx` dynamically; software dependencies are pinned and verified.
2. Connector defaults to disabled and discovery-only without selected tools until human review.
3. Path arguments are strictly scoped to the workspace root.

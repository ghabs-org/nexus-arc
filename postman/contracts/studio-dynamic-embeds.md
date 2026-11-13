# Studio dynamic embed contract

Existing run, tool and conversation routes remain compatible. Completed chat output can contain `«type:expression»` directives. A local
`render_embeds` tool exposes the same interpreter through the existing tool
registry. Tool arguments are `text` and optional explicit finite-JSON `state`.
Workflows invoke the tool explicitly, preserving their typed JSON output.
No browser-side evaluation or new endpoint is required.

Supported types: `state`, `math`, `datetime`, `uuid`, `status_update`,
`artifact_meta`, `artifact_return`, and `artifact_content`. State uses dotted
object keys and nonnegative array indices. Automatic completion supplies only
`task`, `run_id`, `session_id`, `agent_id`, `provider`, and `model`. It never
exposes internal session records, credentials, environment variables or files.

Artifact selectors accept an owned artifact ID or unambiguous owned filename,
with optional `:revision`. Both current record and selected revision must pass
existing authorization. Returned attachments pin their exact revision. Metadata
contains only public artifact fields. Existing authenticated artifact download
routes recheck access. Resolution does not grant public access.

Content chains require a final `format:text|json|json_pretty|csv|datauri`.
Supported modifiers: bounded JSONPath (`$`, dotted keys, integer indices and
wildcards), CSV `select_cols`, `filter_rows_eq`, `slice_rows`, text `slice_lines`,
`head`, `tail`, literal-only `grep`, JSON `select_fields`, and an escaped
Mustache subset (`apply_to_template`, variables, sections, inverted sections,
comments). Templates are authorized, revision-pinned artifacts. Triple/raw tags,
partials, recursive directives and executable expressions are unsupported.
`datauri` returns a pinned file attachment rather than inserting a data URL into
Markdown. Binary formats are never decoded implicitly as text.

Limits per render: 256 KiB input/output, 32 directives, 32 KiB per artifact,
128 KiB total artifact reads, 12 modifiers, 1,000 data rows, 16 path/template
levels and 128 arithmetic nodes. Numeric magnitude is at most 1e15. Powers have
integer exponents between -12 and 12; formatting width/precision is bounded.
No `eval`, imports, calls, attribute access, filesystem traversal or arbitrary network requests. Existing
selected artifact storage backends retain their configured retrieval behavior.

Each invalid directive becomes a bounded `[Embed unavailable: ...]` marker.
Permission/missing-artifact errors use the same generic message. A bad directive
does not discard other valid response text. Retrieved state/artifact/template
text is inserted once and is never interpreted as another embed. Final resolved
text and attachment parts are persisted; reopening never recomputes dates,
UUIDs, state or artifact selection. Existing Markdown sanitization and pinned
artifact previews render the result. Temporary `status_update` messages become
execution events; replay does not reissue them.

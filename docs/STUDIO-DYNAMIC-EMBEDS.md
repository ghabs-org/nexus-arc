# Dynamic response values

Agents can include small calculations, dates, explicit state values and private
file references in their replies. Studio resolves these values once, before it
saves the completed response. Reopening a conversation displays the saved value.
It never changes an old date, generates another UUID or selects a newer file.

For example:

```text
Prepared «datetime:%Y-%m-%d».
Total: «math:40 * 1.2 | .2f».
Download: «artifact_return:report.csv:2»
```

The last directive adds a normal Studio attachment pinned to revision 2.
Opening or downloading it uses existing account permissions. It does not create
a public link or share a file with another user.

## Explicit state

The registered **render_embeds** tool accepts `text` and an optional JSON `state`
object. Agents and workflows can call it through the existing allowed-tool list:

```json
{
  "text": "Hello «state:user.name». Count: «state:counts[0] | .1f»",
  "state": {"user": {"name": "Ada"}, "counts": [2]}
}
```

The automatic final-response pass supplies only the current task, run ID,
session ID, agent ID, provider and model. Environment variables, credentials,
other sessions and internal records are never state variables. Retrieved values
are inserted once: text containing another `«directive»` stays literal.
Direct tool outputs are not automatically interpreted again.

## File data

Use an artifact ID or an unambiguous filename, optionally followed by a revision.
If two visible files have the same filename, select one by ID. Both current
artifact and historical revision must be authorized. Every read records its
exact revision. File metadata exposes only ID, revision, filename, size, type
and digest; storage paths and credential references remain private.

```text
«artifact_content:metrics.json:1 >>> jsonpath:$.items[*] >>> select_fields:name,total >>> format:csv»
«artifact_content:users.csv >>> filter_rows_eq:status:active >>> select_cols:name >>> format:json»
«artifact_content:notes.txt >>> tail:10 >>> format:text»
«artifact_meta:report.csv:2»
```

Available transforms: dotted/index/wildcard JSONPath, field/column selection,
equality row filters, row/line slices, head/tail and literal-text grep. A content
chain must end with `format:text`, `json`, `json_pretty`, `csv` or `datauri`.
Binary `datauri` requests become normal pinned attachments; they never insert
active URLs into Markdown. Regular-expression grep is deliberately unsupported.

`apply_to_template:filename:revision` reads an authorized text template. The
supported escaped Mustache subset includes variables, sections, inverted sections
and comments. It escapes inserted HTML and rejects raw tags, partials, executable
expressions and delimiter changes. Existing sanitized Markdown renders replies;
HTML artifacts retain Studio's restricted preview.

Other supported types are `uuid:` and `status_update:message`. Status updates add
bounded execution events. They do not trigger another action during replay.

## Limits and compatibility

Each render accepts at most 256 KiB input/output and 32 directives. Each artifact
is limited to 32 KiB, with 128 KiB total reads. Chains allow 12 modifiers; arrays
and CSV operations allow 1,000 rows. Paths/templates allow 16 levels. Arithmetic
allows 128 syntax nodes and magnitude up to 1e15; power exponents are integer
values between -12 and 12. Numeric formats have bounded width and precision.
A response already at the output limit can retain an unresolved directive when
there is no room for an error marker.

Invalid, unauthorized and over-limit directives produce a generic inline marker
while preserving surrounding text. Missing and unauthorized files use the same
message. There are no arbitrary network requests, package downloads, code evaluation,
imports or shell commands in this interpreter. Reading a selected cloud artifact
backend still uses its existing configured storage client. Workflows invoke the
tool explicitly so their typed JSON output remains intact.

This is a bounded Nexus equivalent of the frozen reference dynamic-embed feature.
Unsupported reference modifiers and expression syntax fail visibly. Existing generic
`nexus.tools.embeds.resolve_embeds` callable substitution remains unchanged.
Existing conversations and persisted records require no migration. Rich content
remains optional and plain-text `output` remains available to existing clients.

Run the account-free consumer:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_dynamic_embeds.py
```

Contract: [studio-dynamic-embeds.md](../postman/contracts/studio-dynamic-embeds.md).

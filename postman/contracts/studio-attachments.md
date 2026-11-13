# Studio attachment input contract

Existing `/api/runs`, `/api/builds` and `/api/catalog/artifacts` routes and request
formats remain unchanged. Select `artifact_ids` (at most five); the server pins
immutable `artifact_refs` before queuing. Each upload remains bounded to 16 MiB;
combined provider input is bounded to 32 MiB. Current and revision access are
checked again before original bytes are read.

For Codex, Claude Code, Antigravity, Copilot and OpenCode CLI invocation, the
server creates a private temporary directory and writes only the selected pinned
originals. The prompt contains quoted filenames, revisions, local paths and
`file:` links, with source content explicitly labelled untrusted. File contents
are not pasted into the prompt. Temporary files live through streaming and tool
rounds, and are removed on completion, failure or cancellation. A later invocation
rematerializes pinned originals; it does not rely on a prior temporary path.

Shared workers mount only that temporary directory read-only at
`/workspace/attachments`; no additional project or state directory is exposed.
Claude workers add the Read tool with a scoped `Read(./attachments/**)` allow rule
when files are present; existing shell/tool selection remains unchanged. See
[Claude permission rules](https://code.claude.com/docs/en/permissions).

API providers cannot dereference server-local `file:` links. Existing UTF-8 text
and PDF extraction remain available, with OpenAI native image/PDF parts retained.
Unsupported binary API inputs fail explicitly; there is no provider fallback.
Original-file access does not imply that the selected model can understand every
format. File materialization itself never runs a CLI, executes the document, or
initiates a provider request.

The UI uploads through the existing authenticated artifact route and selects the
resulting files for the next explicit submission. Selection/upload alone never
starts inference. Existing queued records with inline context remain readable.

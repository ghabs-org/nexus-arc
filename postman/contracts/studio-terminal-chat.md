# Terminal chat consumer contract

The terminal client uses existing Studio bootstrap/session authorization and
`POST /api/runs`, `GET /api/runs/{id}`. No new endpoint is added.

A chat submission may include up to five saved `artifact_ids`. The existing
catalog expansion validates access and pins each revision before queueing. Run
records now optionally include `artifact_refs: [{id, revision}]` copied from
those validated pins. They contain no filesystem paths, contents or credentials.
Older run records without this field remain readable; clients treat it as empty.
Input attachment references are distinct from generated output artifacts.

The response remains the existing run record, with ordinary per-user access
controls. Updating a saved file after submission must not alter its run reference.
The CLI's `--json` format selects public result fields and never serializes
internal run configuration wholesale. Plain output and read-only inspection
share citation rendering. Client polling, timeout and failed responses never
replay POST. An inspection with attachment flags fails before network access;
legacy message arguments accompanying `--run-id` remain ignored.

Local actual-HTTP CLI checks live in `tests/test_studio_terminal_chat.py`;
existing authorization/file-lifetime checks live in
`tests/test_studio_attachment_files.py` and `tests/test_studio_team.py`.
The example consumer is `examples/nexus-bot/src/studio_terminal.py`.

Interactive session controls reuse `GET /api/sessions`, `GET /api/sessions/{id}`
and `POST /api/workbench/memory/{id}/edit` with `{title}`. Existing visibility,
ownership and active-conversation edit rules apply. The terminal does not create
a parallel session store or bypass access checks when resolving titles.
Duplicate titles are ambiguous; explicit IDs take precedence. Failed selection
must retain the prior conversation. Listing keeps the existing 100-record cap;
an explicit owned ID can still be selected beyond that cap.

History and export read the authorized conversation, never submit a run. JSON
export wraps the existing conversation in `nexus-studio-memory/v1`, matching the
existing memory export shape. Local export refuses overwrites/symlinks and uses
0600 permissions. Slash command handling applies only to interactive input;
one-shot tasks and other saved-prompt shortcuts retain their existing behavior.
Session controls add no routes or stored-record changes.

Local `/upload` sends one `POST /api/catalog/artifacts` with a unique ID, basename,
and bounded base64 original. The returned ID joins the next explicit chat
submission's `artifact_ids`. The terminal never retries upload or chat POSTs.
Regular-file validation and the five-attachment limit run before upload.

Artifact listing reads the selected session's recorded runs via authorized
`GET /api/runs/{id}` and verifies each returned `session_id`. It selects output
references from `artifact_id`/`artifact_revision`, artifact structured parts and
existing speech references. It excludes input attachment references. Artifact
metadata/history come from existing authorized catalog GETs; pinned revisions
resolve against history after replacement. Legacy unpinned references resolve
the current revision. Binary downloads use `GET /api/catalog/artifacts/{id}/versions/{revision}`
with redirects disabled, the existing session headers and a 16 MiB response cap.
Stored SHA-256 metadata is checked before exclusive private-file creation.
No new server endpoints or stored fields are introduced.

Enhanced terminal completion reuses the same session/run/artifact GETs. It runs
only after explicit completion, offers only current authorized results, and does
not submit tasks or retain a cross-prompt name cache. Markdown rendering changes
terminal presentation only. `--json`, redirected output and `--plain` preserve
plain/structured payloads. A multiline submission is one existing chat POST
whose `task` retains its newlines. Mode switches and completion create no runs.
The optional PTY acceptance verifies this through an actual local Studio.

Agent discovery/completion uses `GET /api/agents` and selects only ID, metadata
name, configured provider and definition validity for display. Entrypoint
discovery uses `GET /api/catalog/entrypoints`, displaying ID/name, HTTP/A2A type
and enabled/disabled/archived configuration state. It does not print credential
references or claim connection health/last-seen measurements. Neither discovery
command submits or enables anything.

`/agent` resolves an exact ID or unique exact name and submits one existing
`POST /api/runs` with `kind: chat`, literal `task`, `agent_id`, current session
and any explicitly selected files/provider. Server roles and lifecycle checks
remain authoritative. The per-turn override is cleared after accepted submission;
subsequent messages use the CLI's original default. Bad/missing/ambiguous agent
selection never submits. No endpoint or persisted-field changes are required.
The last selected agent already appears on the saved conversation, which the
saved `studio-terminal-agents.assertions.js` checks against actual Studio HTTP.

Terminal abbreviations and alias invocation check active prompt IDs/shortcuts
through existing `GET /api/catalog/prompts` before interpreting a conflicting
token. Exact saved prompt tokens win over abbreviations/aliases. Exact built-ins
remain reserved. Ambiguous prefixes, failed prompt-access checks and alias cycles
never become submissions. An alias expansion is performed once and retains
literal appended arguments; the resulting command/message uses the same existing
server operations. One-shot arguments bypass interactive command resolution.

Alias CRUD and clear add no server mutations. Aliases use private per-key atomic
files on the CLI host, scoped by existing bootstrap origin/workspace/user/mode
metadata; no session or CSRF tokens are stored. The HTTP client retains only this
noncredential metadata for preference scoping. Clear changes terminal display
only. Saved directed-agent assertions also verify the conversation created through
alias expansion against a real disposable Studio.

Explicit `/retry` reads the previous owned run and existing workbench effects.
Only a completed chat run without pending gates or recorded integration effects
may prepare another submission. Preparation performs GETs only and copies an
allowlist of message/agent/file IDs plus the completed run's resolved provider.
Approval flags, credentials, environment/configuration and hidden run state are
never copied. The existing POST/auth/lifecycle path remains authoritative.

`GET /api/bootstrap` adds `capabilities.expected_artifact_refs: true`. The terminal
refuses file retries if the capability is absent. `POST /api/runs` accepts optional
`expected_artifact_refs: [{id, revision}]` (up to five; positive integer revisions).
After pinning current attachment IDs, the catalog requires exact reference-list
equality before queueing. Wrong shape/revisions/selection are validation failures;
requests without the field preserve existing behavior. References never bypass
ownership or authorize access to older files. There is no persisted-record rewrite.

Retry creates a new chat run and normal conversation turn. It does not resume
workflow gates or recover effects, replay uploads, or retry transport errors.
Saved `studio-terminal-retry.assertions.js` verifies two literal messages with
distinct run IDs through actual Studio after one explicit retry.

Named new chat uses optional `session_title` on `POST /api/runs`: nonempty text,
at most 200 characters, accepted only for a new `kind: chat` without `session_id`.
It populates the initial session title through the existing successful/failed
turn writers. Existing-session renaming keeps its existing memory-edit endpoint.
Bootstrap advertises `capabilities.session_title: true`; older servers fail
closed for named creation. Untitled/legacy requests keep existing defaults.

Session record summaries add computed `message_count` (user and assistant saved
messages), excluding message bodies. File and SQL state use the same summary;
no stored record rewrite occurs. Existing per-user visibility and the list's
100-record limit remain unchanged.

Interactive restore reads an account-scoped local selection, then authorized run
and session GETs. A known nonterminal/uncertain run receipt blocks restore. Explicit
`--session-id` overrides selection; `--new-session` resets it. One-shot tasks and
run inspection do not read/update this preference. Selection records are written
only after explicit selection/new commands or accepted interactive submissions.
If persistence fails after POST, the error identifies the accepted run and forbids
resubmission; no automatic retry occurs.

`/delete` writes only a scoped local hiding marker after authorized session lookup.
It never calls server archive/delete/cancel. Listing/completion filter hidden
IDs; explicit ID switching unhides them. Server history/artifacts remain intact,
verified by `studio-terminal-index.assertions.js` against actual Studio.

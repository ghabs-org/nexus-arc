# Prompt versions and calendar lifecycle

This additive change uses the existing catalog history, Nexus roles, workspace
lock and durable job queue. Existing prompt/catalog/schedule routes and records
remain readable. No extra scheduler or authentication service is introduced.

Prompt History compares a selected saved revision against the latest version:
name, description, content, variable defaults, tags and shortcut. Changed fields
are labelled in both columns; phones stack the columns. Restore creates a new
revision through existing validators. A stale reviewed revision, reclaimed
shortcut, invalid content or archived prompt is rejected before saving. Original
ownership and earlier history remain intact. AI-created and restored prompts
refresh slash suggestions immediately. Both chat and Quick Build support tag/name/
ID/shortcut search, keyboard selection and variable entry; `/prompt-id` remains
valid. Templates expand once without executing or recursively expanding values.

Schedules expose explicit Pause/Enable and Dates & history controls. Dates are
shown in the stored IANA zone. Manual once, interval and calendar creation remain
available; new drafts default disabled, including AI drafts even if generated
JSON asks to enable them. For once/interval dates the editor explicitly uses the
browser's local time; the stored instant is UTC. Cron uses the selected IANA zone.
Spring gaps are skipped; a fall fold uses its first occurrence only. Pausing does
not cancel work already dispatched. A queued/running/approval-paused prior run
prevents another occurrence from overlapping.

A version-1 schedule-state record adds a cadence fingerprint. Name, task, target,
archive, pause and resume edits preserve progress when timing stays unchanged.
Completed one-time schedules do not repeat on those edits. Change the first run
instant or recurring timing to create a new cadence. Legacy state derives its
fingerprint from the corresponding immutable catalog revision; no existing
records are rewritten during startup. If that legacy history is missing, existing progress is preserved and
`legacy_timing_unverified` is surfaced for review; an old date is never replayed
merely because history is missing. Explicit pause/enable persists the normalized
baseline so later deliberate timing edits can be compared.
The scheduler rereads each catalog item inside the workspace lock, so another
worker's pause/edit cannot be overwritten from a stale list.

Missed recurring occurrences coalesce into one dispatch after downtime or resume;
an overdue one-time occurrence dispatches once. Intervals restart from that
coalesced dispatch time. New `schedule-occurrences` version-1 records retain due
instant, claim time, catalog revision and acknowledged run ID; the history view
resolves current run status. It shows the latest 100 records and a count of older
retained records. Earlier legacy executions remain in Activity, not invented in
this new history.

In SQL mode, state, occurrence and run enqueue share the existing workspace
transaction. A failure before commit rolls them back together. File state claims
before dispatch; an abrupt crash can leave an unacknowledged claimed occurrence.
Inspect Activity and external effects before manual resubmission; it is never
replayed automatically. This preserves uncertain-effect policy and does not claim
exactly-once execution in external systems. File mode remains one Studio process;
multiple workers use SQL mode and shared definition/artifact storage.

Additive HTTP contracts are checked in at
`postman/contracts/studio-calendar.yaml`:

- `GET /api/studio/prompts/{id}/compare?revision=N`
- existing `POST /api/studio/prompts/{id}/restore`
- `GET /api/studio/schedules/{id}`
- `POST /api/studio/schedules/{id}/enabled`

Shared-mode mutations retain builder authorization and CSRF checking. Definitions
follow existing workspace catalog visibility; run details retain their private
authorization. Read-only inspection never dispatches. Existing catalog APIs remain
available for full manual editing and revision history.

Reproduce the framework consumer or disposable browser fixture:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_scheduling.py
PYTHONPATH=. python examples/nexus-bot/src/studio_scheduling.py --serve
python -m pytest tests/test_studio_scheduling.py tests/test_studio_design.py -q -o addopts=''
```

The consumer uses synthetic providers only. Its optional loopback HTTP server
runs the actual scheduler against disposable state. No provider credentials,
external effects, accounts or real schedules are used. See the scoped local
acceptance record for tested restart, DST, concurrency and browser behavior.

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

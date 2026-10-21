# Studio terminal chat

The `cli-entrypoint` community equivalent uses `nexus chat` against a running
Studio. It reuses existing sessions, saved file revisions, providers and Nexus
authorization. No separate broker, entrypoint server or upload service is needed.
This is a supported Nexus equivalent, not an import of the upstream Python plugin.

```sh
nexus chat --url http://127.0.0.1:5004 'Summarize this task'
nexus chat --url http://127.0.0.1:5004 --artifact-id SAVED_FILE_ID 'Review this file'
nexus chat --url http://127.0.0.1:5004 --session-id SESSION_ID 'Continue'
nexus chat --url http://127.0.0.1:5004 --run-id RUN_ID --json
nexus chat --url https://studio.example --session-env NEXUS_SESSION
```

Omit the message to enter interactive chat. `/quit`, `/exit` or end-of-input
stops the terminal consumer without submitting another message. Select an agent
with `--agent` and an installed/configured provider with `--provider`.

`--artifact-id` accepts an existing saved Studio file ID, not a host path or URL.
Repeat it for up to five distinct files. Existing authorization checks apply.
The server pins revisions at submission and supplies private temporary original
files to supported CLI providers. API providers retain their existing attachment
capabilities; the client does not silently change providers or billing. In
interactive mode these attachments apply only to the next submitted message.
Subsequent turns do not implicitly attach the current version again.

`--json` returns one `nexus-studio-chat-result/v1` object containing `id`,
`session_id`, `status`, `output`, `error`, `citations` and `artifact_refs`.
It requires a one-shot message or `--run-id`. It excludes the run's internal
configuration/environment fields. A noncompleted run is a result, not success:
script consumers must inspect `status`. Transport/validation errors fail the
command. Plain output prints the answer/error and available citation references.

`--run-id` is read-only. It rejects attachment flags and preserves existing
behavior of ignoring message arguments during inspection. It returns the same
result representation as submission, including citation and pinned file references.
Polling, timeout, lost connections and inspection never resubmit a task.
`--timeout` bounds polling (1–3600 seconds); an in-flight HTTP request has its own
client timeout. If the result is uncertain, inspect the printed run ID or Studio
Activity instead of sending the task again. JSON-mode polling timeout errors also
identify the run. Closing the client does not cancel the server job or approve a
workflow gate; use the existing Studio controls.

Remote Studio requires HTTPS and an existing Nexus session supplied through the
named environment reference. Do not put tokens in arguments or URLs. Coding
subscription and API billing stay governed by the selected server-side provider.

The framework example creates an isolated temporary Studio with a deterministic
provider, sends a saved file through the actual CLI, inspects the same run and
verifies that inspection creates no duplicate execution:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_terminal.py
```

Acceptance remains partial until the frozen upstream entry's full semantics and
selected real provider/platform checks are reconciled. Local CLI behavior is
covered by `tests/test_studio_terminal_chat.py`, existing terminal/configuration
checks, and file-lifetime/authorization regressions. No live model account is
needed for these local checks.

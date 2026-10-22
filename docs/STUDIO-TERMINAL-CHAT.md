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

Install the optional terminal interface with `pip install 'nexus-arc[studio-terminal]'`.
An interactive terminal then renders response/history Markdown, including headings,
code, lists and tables. Base installs retain ordinary input/output. Redirected
output and `--json` preserve their existing plain/structured representation;
`--plain` explicitly disables rich display and enhanced input. Error and citation
references remain ordinary text. Rich renders Markdown locally and does not load
linked images or execute code. Rich console-markup tags inside model text are literal.

Press **Tab** to complete command names, authorized session IDs/titles after
`/switch`, generated artifact IDs/revisions/filenames after `/download`, or export
formats after `/export`. Names containing spaces are quoted for the existing
command parser. Session listing keeps the existing 100-record limit; explicit
IDs remain usable beyond that limit. Completion performs authorized GETs only,
only on explicit completion, and never submits an agent task. Results are not
persisted or retained between prompts; failed access offers no fallback names.
Prompt editing history stays in memory for the terminal process. Shell execution
and external-editor shortcuts are disabled.

Agent commands reuse workspace definitions and the existing chat runner:

- `/agents` lists native agent IDs, names, configured providers and definition
  availability. It does not run agents, read credentials or prove provider readiness.
  Invalid definitions remain visible without printing their source/error details.
- `/agent <id or quoted exact name> <message>` directs one turn to that agent.
  Exact IDs take precedence over matching names; duplicate names require an ID.
  Only the selector is parsed: message JSON, quotes and newlines remain literal.
  Pending file selections apply to that turn. Later turns return to `--agent`,
  or Studio's normal default if no CLI default was selected. The current
  conversation is retained. Explicit `--provider` remains an override; otherwise
  provider/model selection follows the agent and existing server rules.
- `/entrypoints` lists configured HTTP/A2A entrypoints and their enabled/disabled/
  archived configuration state. It does not print authentication references or
  invoke targets. Connection health and last-seen timestamps are not tracked by
  this command; enabled configuration is not live readiness.

Tab completes known native agent IDs and names after `/agent`, using only the
existing authorized agent GET. The server still applies current workspace roles,
resource lifecycle, approvals, credential and execution checks at submission.
Discovery is not authorization to run an unavailable resource. One-shot message
arguments stay literal even when their text starts with `/agent`.

Terminal aliases and abbreviations:

- `/alias` lists aliases; `/alias name text` creates or updates one;
  `/alias name` removes it. Use `/name` to expand it once, appending any extra
  arguments literally. Text may be an ordinary message, saved prompt shortcut,
  or terminal command. Quote the whole expansion when desired. Alias chains are
  rejected. At most 100 aliases and 16 KiB of UTF-8 text per alias are supported.
- Aliases cannot shadow built-in names or existing saved prompt IDs/shortcuts.
  If a saved prompt later acquires an alias name, that exact prompt token wins.
  Existing exact built-in commands remain reserved.
- Unambiguous built-in prefixes work, such as `/he`, `/se`, `/sw`, `/m`, `/c`
  and `/q`. Ambiguous `/h`, `/ag` or `/e` reports matching commands and submits
  nothing. Exact saved prompt tokens also win over these abbreviations. A
  failure to check prompt access blocks abbreviation execution. Aliases use
  exact names; Tab completes those names locally.
- `/clear` or `/c` clears an actual terminal without deleting messages, files,
  aliases or the selected conversation. Redirected output prints an explanatory
  line instead of terminal controls. Clear never executes a shell command.

Aliases persist on the CLI machine under
`$XDG_STATE_HOME/nexus-arc/terminal/<scope hash>/aliases/`, defaulting to
`~/.local/state/nexus-arc/terminal/`. Set `NEXUS_TERMINAL_STATE_DIR` to choose a
different root. The scope includes the authenticated Studio origin, workspace
path, user ID and deployment mode; credentials/tokens are excluded. Files use
`nexus-studio-terminal-alias/v1`, private 0600 permissions and atomic replacement
per alias. Concurrent edits to different aliases do not overwrite each other;
the last completed edit to the same alias wins. Corrupt or unsupported records
fail closed and remain on disk for recovery. Framework consumers without a
bootstrap scope retain in-memory aliases only. Server conversation and agent
files remain authoritative; no conversation contents are copied into this store.

`/retry` explicitly sends the last completed chat message from this terminal
again in the selected conversation. It creates one new run, retaining the literal
message, selected agent, resolved provider and original attachment selection.
It does not replay `/upload`, re-expand terminal aliases or change the CLI default
for following turns. Current server configuration, authorization and conversation
context apply; this is a new message, not a replay of a historical model context.

Retry memory is per conversation and per terminal process. A restored conversation
without a completed turn in this process has nothing to retry. Pending, failed,
cancelled, uncertain and approval/input-waiting runs cannot be resubmitted through
this command. Any recorded integration action, including a completed or reviewed
action, directs the user to existing Studio review/recovery controls instead of
creating a new effect identity. If access to those checks fails, no run is submitted.
Actions inside external provider tools remain governed by that provider; this
guard inspects Nexus's recorded integration actions rather than claiming to detect
all external effects.

The optional `expected_artifact_refs` submission field compares the previous
validated IDs/revisions with newly pinned attachment references before queueing.
Changed, replaced, missing or archived files require explicit new selection.
The server advertises this guard through bootstrap capabilities; file retry is
blocked against older servers without the guard. No files are silently replaced.
Polling, reconnects, inspection and transport failures never trigger retry. Existing
one-shot/noncompleted command behavior is preserved: use Activity to inspect an
uncertain result. Manual retry can repeat provider work and its normal billing.

`/retry` is newly reserved. A previously saved alias with this name is preserved
but inactive; `/alias` can list it and `/alias retry` can remove it. Other aliases
continue to work. This avoids deleting local preferences during upgrades.

`/multiline` toggles multiline editing in the enhanced terminal. Enter adds a
line; **Alt+Enter** (or Escape then Enter) submits the whole message once.
To turn multiline off, type `/multiline` and press Alt+Enter. It preserves message
newlines in Studio. Plain/redirected input refuses this command rather than
changing how piped messages are split. The mode is not persisted across processes.

Interactive session controls use the same server records as the browser:

- `/sessions` lists up to 100 visible, nonarchived server conversations, filters
  locally hidden IDs, and marks the current selection. It shows saved message
  counts and the server's last update/creation timestamp. A pending named
  conversation appears separately until its first message is sent.
- `/switch <id or exact title>` selects an owned conversation. Quote titles containing
  spaces, or enter the full title without quotes. Duplicate titles require an ID.
  Direct IDs also support conversations outside the first 100 listed records.
- `/new [title]` clears the current selection and optionally names the next
  conversation (1–200 characters). The pending title survives terminal restart;
  Studio creates the conversation on its first message. Named creation requires
  server title support. `/rename <title>` renames an existing conversation.
- `/delete <id or exact title>` removes a conversation from this terminal's local
  list. Server history, artifacts and archive state remain unchanged. Deleting
  the selected conversation clears the selection. Explicit `/switch ID` restores
  it to the local list; hidden names are not offered by listing or completion.
- `/history [count]` prints the latest 20 messages by default; the allowed count is 1–1000.
- `/export [md|json|txt] [new file]` prints the current conversation or writes a
  new local file. Markdown is the default. Files use owner-only permissions;
  existing files and symlinks are refused. JSON uses `nexus-studio-memory/v1`.
- `/help` lists these commands. Other slash messages retain Studio's existing
  saved-prompt shortcut behavior. One-shot message arguments remain literal tasks.

Session commands never submit agent tasks. Invalid commands retain the current
selection. Interactive startup automatically restores the last local selection
after authorized run/session GETs. Known pending or uncertain runs stop for
inspection; missing/revoked/archived conversations fail closed without selecting
another conversation. No task is automatically submitted or approved. Use
`--session-id` to select an accessible conversation explicitly, or `--new-session`
to start fresh and clear the saved selection. The latter cannot accompany
`--session-id` or `--run-id`. One-shot messages keep creating fresh conversations
unless an ID is specified; one-shot messages and read-only `--run-id` inspection
neither restore nor update the interactive selection. `/new` retains the selected provider and any explicit pending
attachment flags; those files still apply only to the next submitted message.
Export includes conversation messages and their metadata, including citations;
it does not fetch run configuration or copy attachment files. Treat exported
conversations as private data. Markdown and text exports include message text;
JSON preserves message metadata.

Selection and per-ID hiding markers share the alias store's origin/workspace/user
scope and private atomic-file writer. Selection uses
`nexus-studio-terminal-selection/v1` in `active.json`; markers use
`nexus-studio-terminal-hidden/v1` under `hidden-sessions/`. They contain only IDs,
an accepted-run receipt and an optional pending title. Server labels, timestamps
and history remain authoritative. Separate hiding markers prevent another
terminal's completion from resurrecting a locally deleted conversation. Concurrent
selection changes use the last completed local write. Unsupported/corrupt state
is preserved and blocks restoration; `--new-session` explicitly resets selection.
These preferences live on the CLI host, which may differ from the Studio VM.

File commands operate on local terminal files and authorized server artifacts:

- `/upload "local path" [message]` saves the original file to Studio and sends it
  with the optional message. Without a message it asks the agent to review the file.
  Uploads accept regular files up to 16 MiB, use unique IDs, and never replace a
  same-named saved file. The attachment applies only to this submitted turn.
- `/artifacts` lists generated outputs from runs recorded in the selected
  conversation. Unrelated saved files and input attachments are excluded.
- `/download <id:revision or exact filename> [new path]` downloads one listed
  output. IDs without a revision work only when unambiguous. Without arguments,
  choose comma-separated list numbers; Enter cancels. Duplicate selections are
  deduplicated. Default destinations use artifact filenames in the current local
  directory. Duplicate filenames require explicit individual destination paths.

Downloads use recorded artifact revisions, including old revisions after a file
is replaced or archived. Legacy outputs without a recorded revision, such as
existing speech references, resolve the currently authorized revision at listing
time. Each download uses that resolved revision. SHA-256 is checked where stored;
the client bounds binary responses at 16 MiB. Files use owner-only permissions
and refuse overwrites or symlink destinations. All selected destination names
are checked before a multi-download; network or storage failure can still leave
earlier selected files downloaded. Transfers never retry automatically.
An uncertain upload may already be saved: inspect Studio's files before retrying.
Uploads/downloads retain the selected server-side provider and its billing.

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

The frozen community README at commit
`f3a733c1470fc79d7dbc7fc21ddeab077f4645f4` also describes connected-entrypoint
type/last-seen reporting. Runtime equivalence beyond configuration-only listing
and selected real provider/platform checks remain open. Local CLI behavior is
covered by `tests/test_studio_terminal_chat.py`, existing terminal/configuration
checks, and file-lifetime/authorization regressions. No live model account is
needed for these local checks.

Optional real-library checks run separately from base-install regressions,
without skips or model accounts:

```sh
pip install 'nexus-arc[studio-terminal]' pytest pexpect
PYTHONPATH=. python -m pytest tests/studio_terminal_ui_acceptance.py -q -o addopts=''
```

The macOS PTY check drives actual Tab and Alt+Enter keys against a disposable
Studio. This does not establish physical Linux/Windows terminal behavior. Library
interfaces follow the [Rich Markdown documentation](https://rich.readthedocs.io/en/stable/markdown.html)
and [prompt-toolkit input documentation](https://python-prompt-toolkit.readthedocs.io/en/stable/pages/asking_for_input.html).

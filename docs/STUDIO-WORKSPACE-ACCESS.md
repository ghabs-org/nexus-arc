# Studio assistant and server workspace files

Implemented in commit `325e4a6`; follow-up verification: 22 October 2026.

Open **Help → Studio assistant** to ask where definitions are saved, how knowledge
projects work, or how to access VM files. **Find local answer** uses built-in help
and current agent/workflow names without a model call. **Ask AI** explicitly starts
a normal Studio conversation with the selected provider and a read-only help tool.
The provider's existing account, permissions and billing apply. The assistant
cannot run shell commands or modify/activate resources. It does not automatically
read or send folder contents.

Open **Workspace files** to browse these server-side roots:

- `files`: `<workspace>/files`, writable through create-only upload/new-folder actions.
- `agents`: `<workspace>/agents`, read-only YAML; use the existing Agents editor to change definitions.
- `workflows`: `<workspace>/workflows`, read-only YAML; use the existing Workflows editor/canvas to change definitions.

The file picker uploads from the browser computer. The listing shows files on the
Studio server, including a headless VM. Preview is inert, bounded text; binary files
can be saved as private artifacts for existing viewers. Uploads are limited to
16 MiB and cannot overwrite an existing file.

Select **Save private copy** to capture the previewed revision in Library artifacts.
A changed source is rejected until preview is refreshed. The saved artifact can be
downloaded, attached to the next chat, or selected in a knowledge project's document
form. Attaching does not submit a message. Knowledge projects remain document/chat
collections, rather than VM directories.

In shared mode, file browsing and writes require builder/owner project access.
Exposed folders are shared among these authorized builders. Use private Library
artifacts for personal documents. Snapshot artifacts use the existing owner-scoped
storage and download controls. Hidden/traversal paths, symbolic links, hard-linked
and special files, private Studio state and common credential filenames are blocked.
An administrator should expose only folders intended for workspace sharing; these
filters cannot identify every possible secret inside otherwise ordinary files.

## Additional VM folders

Add roots to the existing private administrator runtime configuration, outside the
shared workspace and readable only by the service account:

```json
{
  "workspace_files": {
    "documents": {
      "name": "Server documents",
      "path": "/srv/nexus/documents",
      "writable": false
    }
  }
}
```

The directory must already exist. An omitted `writable` flag means read-only.
Restart Studio after changing runtime configuration. Browser/model requests cannot
choose arbitrary host roots. Private state and workspace-root aliases are rejected;
additional writable aliases of agents/workflows are also rejected. No migration,
PostgreSQL conversion or desktop packaging is needed for these features.

## Follow-up verification

Checks found and fixed a missing browser run-monitor reference in **Ask AI**.
The browser now uses the shared `pollRun` recovery mechanism, retains an Activity
inspection link when monitoring fails, and never resubmits a task automatically.
A configuration alias could also make definition directories writable; that bypass
is now rejected before a file service is created.

Recorded checks:

- 54 backend checks: workspace operations, actual HTTP routes, runtime configuration,
  shared authorization, private download ownership, revoked roles, traversal,
  symbolic/hard links, special files and a completed synthetic AI tool conversation.
- 19 browser-logic checks: existing reconnect/no-replay behavior plus actual Ask AI
  handler completion and monitoring failure. Zero skipped checks in both sets.
- The framework example `examples/nexus-bot/src/studio_workspace_access.py` passed.
- Two read-only Postman assertions passed against an isolated actual Studio server.
- Actual browser walkthrough: local question, completed synthetic AI answer, VM file
  preview, private snapshot and explicit attachment selection without submission.
- Changed-file lint and whitespace checks passed.

These checks used temporary files and synthetic providers. They do not certify
real provider accounts, a deployed VM, physical mobile devices or Windows file
access. The descriptor-based file service currently targets macOS/Linux; Windows
acceptance remains part of the final desktop milestone. The user's running Studio
was not restarted during verification. No complete reference milestone is promoted by
this additional product check.

Protocol details: [workspace access contract](../postman/contracts/studio-workspace-access.md).

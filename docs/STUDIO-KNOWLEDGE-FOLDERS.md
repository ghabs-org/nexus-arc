# Knowledge projects linked to server folders

A knowledge project can follow whole folders from **Workspace files**, such as a
documentation folder or a source tree, instead of single saved files. Sync copies
new and changed files into private Library artifacts and indexes them with the
existing local knowledge index. Nothing is sent to a model by linking or syncing.

## Linking a folder

1. An administrator exposes the folder as a Workspace files root (see
   [additional VM folders](STUDIO-WORKSPACE-ACCESS.md#additional-vm-folders)).
   Read-only roots work; sync never writes to the folder.
2. Open **Workspace files**, choose the root, open the folder (or stay at the top
   level for the whole root) and select **Link folder to knowledge project**.
3. Choose the project, optional include/exclude patterns, and whether to sync now.

The project view lists linked folders with their file counts and last sync result,
and offers **Sync now** and **Unlink**.

Patterns use shell-style matching against the path inside the linked folder and the
file name (`*.md`, `docs/*`, `tests/*`). Without include patterns, text, code, PDF
and Office files are synced. Hidden files and folders, symbolic and hard links,
credential file names, lockfiles, minified bundles and dependency/build folders
(`node_modules`, `venv`, `__pycache__`, `site-packages`, `dist`, `build`, `target`,
`coverage`, `htmlcov`, `vendor`, `*.egg-info`) are always skipped.

## What sync does

Sync runs as one `knowledge-sync` task in Activity and can be cancelled there.

- **New file**: saved as a private artifact (`kfolder-…`) owned by the project owner,
  then indexed in the same task.
- **Changed file** (different SHA-256): saved as the next revision of the same
  artifact and indexed. Search uses the newest ready revision; older revisions stay
  pinned for earlier answers and their citations.
- **Unchanged file**: skipped.
- **Deleted file**: its documents are marked `source_removed` and leave search.
  If the same content returns, the existing copy is restored without a new revision.
- **Unlinked folder**: its documents leave search immediately; records and artifacts
  are kept for existing citations.

Files that cannot be indexed (for example a PDF over 50 pages) are reported as
failed and keep the usual **Retry indexing** action. Up to 50 skipped files are
listed in the task output with their reason.

## Limits

| Limit | Value |
| --- | --- |
| Linked folders per project | 4 (they cannot repeat or contain each other) |
| Files read per sync | 1000 |
| Bytes read per sync | 64 MiB, including unchanged files, which are hashed |
| Patterns per folder | 20 include and 20 exclude |
| Per-file limits | Existing knowledge limits: 16 MiB, 256000 characters, 256 chunks |

When a limit is reached the sync reports `truncated`, keeps what it processed and
never marks unseen files as removed. Link a narrower folder or add exclude patterns.
Semantic and hybrid search rank at most 4096 chunks per project; large source trees
may need lexical search or narrower folders.

Code is indexed as plain text in fixed-size chunks, which suits documentation and
"where is X mentioned" questions better than reasoning across a codebase.

## Permissions

Linking, unlinking and syncing require the builder role and Workspace files access,
as browsing does. Reading folder status needs only access to the project. Sync
runs as the requesting user and re-checks access when it starts.

## API

| Method and route | Purpose |
| --- | --- |
| `GET /api/studio/projects/<id>/folders` | Linked folders, file counts, running sync, last result and limits |
| `POST /api/studio/projects/<id>/folders` | Link `{root, path, include?, exclude?}`; returns the saved project |
| `POST /api/studio/projects/<id>/folders/<folder_id>/unlink` | Unlink; returns the saved project |
| `POST /api/studio/projects/<id>/sync` | Queue a `knowledge-sync` run (one at a time per project) |

Linked folders are stored on the project record as `folders`; per-file sync state
is kept in the private `knowledge-folder-sync` collection. Search results from
synced files include `source_path`.

Example consumer: `PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge_folders.py`.
Tests: `tests/test_studio_knowledge_folders.py`.

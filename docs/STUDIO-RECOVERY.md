# Studio upgrade and recovery foundation

This operator interface backs up Studio definitions and private state. It does
not start providers, workers or schedules. Stop every Studio/worker and other
workspace writer first; `--stopped` is an explicit operator acknowledgement, not
an automatic process-stop mechanism.

```sh
python -m nexus.core.studio_backup create /srv/nexus/project /srv/backups/studio-2026-10-18 \
  --state-dir /var/lib/nexus/studio-private --stopped
python -m nexus.core.studio_backup verify /srv/backups/studio-2026-10-18
python -m nexus.core.studio_backup restore /srv/backups/studio-2026-10-18 /srv/nexus/restore-review
```

For local default state, omit `--state-dir`. Both backup and restore destinations
must be new directories. Keep backups outside the project and private state.
The Python APIs are `create_snapshot`, `verify_snapshot` and `restore_snapshot`
in `nexus.core.studio_backup`; the example consumer is
`examples/nexus-bot/src/studio_recovery.py`.

Snapshots include `agents/`, `workflows/`, Studio records and histories, document
chunks, local artifact revisions, indexes and checkpoints. SHA-256 manifests
verify every listed file and reject missing, unlisted, corrupt or symbolic-link
entries before publishing a restore. SQLite state snapshots include committed
WAL pages; immutable uploaded files remain byte-for-byte unchanged, even when
their content is itself a SQLite database. Interrupted copies leave no published
destination. Snapshot files use mode 0600 under a private directory.

Authentication stores, prepared subscription accounts, environment files, project
source outside the two definition directories, MCP account/cache homes and
temporary speech inputs are excluded. Back those up using the existing Nexus
and project procedures. Configuration records retain credential references;
the backup never resolves those references. Conversation and document content
remains private data in the snapshot. The checksums detect corruption; they do
not authenticate a snapshot supplied by another party or encrypt it.

SQL workspaces require the correct namespace and server connection reference:

```sh
python -m nexus.core.studio_backup create /srv/nexus/project /srv/backups/studio-sql \
  --state-dir /var/lib/nexus/studio-private --stopped \
  --state-url-env NEXUS_STUDIO_STATE_URL --namespace personal
```

The exporter uses the existing transactional state service and reads only that
namespace. It retains job rows for inspection. A physical SQLite SQL-state file
is excluded so another namespace cannot leak through it. Backups of legacy SQL
workspaces created before the backend marker still require these SQL arguments;
the operator must supply their actual configured backend. New workspaces record
backend/namespace without connection strings and reject accidental mode changes.

Restore always creates a **file-state review copy**, including SQL-origin
records. It never reconnects to or overwrites a database. Queue rows are not
enqueued; queued/running runs become interrupted, uncertain external tasks remain
uncertain, schedules/connections/entrypoints are disabled, sharing is revoked,
and active artifact storage becomes local. Credential references and inactive
profiles are retained for review. Paused workflows keep their approval gates;
resuming an approved copy leaves the original run unchanged. Embedding/runtime
configuration and the original Nexus team policy must be supplied separately
before hosted use. Knowledge search reconstructs its destination index from
persisted ready chunks when its original root/namespace index name differs.
The knowledge SQLite snapshot includes bounded derived document vectors. Queries
are never stored in that cache. Rebuilding/restoring indexes does not call an
embedding endpoint; an explicit semantic/hybrid search uses only the configured
provider. Changing model identity or its `cache_revision` invalidates old vectors.

Remote artifact references fail preflight rather than produce an incomplete
snapshot. Non-default artifact storage profile IDs also require a separate
storage backup in this initial API. Use the matching storage backup procedure until provider-aware
restore is implemented. Unrecovered configuration releases must be recovered
before backup. Physical PostgreSQL restore remains a separate, existing local
infrastructure check; logical file-state restore does not certify SQL-to-SQL,
cloud-bucket, actual personal-VM or account-store disaster recovery.

Version 1 upgrades add metadata only. An immutable upgrade intent precedes
history and marker writes; restart finishes the same intent after interruption.
Malformed, future or conflicting metadata fails before upgrade writes. The
frozen v0 fixture is synthetic historical-format data with a checked hash
manifest, not a copy of a production workspace. Any future user-record rewrite
must require a verified backup and complete migration preflight first.

Acceptance evidence is in `tests/test_studio_schema.py` and
`tests/test_studio_backup.py`: interruptions before/after each upgrade write,
SQL transaction rollback, legacy conversation continuation and graph/YAML runs,
artifact revisions/citations, paused-workflow restore, namespace isolation,
WAL inclusion, byte-exact database attachments and inactive remote profiles.
These checks establish the supported recovery foundation, not complete external parity.

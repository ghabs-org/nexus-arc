# Studio upgrade and recovery foundation

This operator interface backs up Studio definitions and private state. It does
not start Studio, workers or schedules. Artifact providers are read only when
remote inclusion is explicitly selected. Stop every Studio/worker and other
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
`examples/nexus-bot/src/studio_recovery.py` (add `--sql` for logical SQL restore).

Snapshots include `agents/`, `workflows/`, Studio records and histories, document
chunks, local artifact revisions, indexes and checkpoints. SHA-256 manifests
verify every listed file and reject missing, unlisted, corrupt or symbolic-link
entries before publishing a restore. Copied bytes are checked again before inactive
record changes. SQLite state snapshots include committed
WAL pages; immutable uploaded files remain byte-for-byte unchanged, even when
their content is itself a SQLite database. Interrupted copies leave no published
destination. Snapshot files use mode 0600 under a private directory.

Authentication stores, prepared subscription accounts, environment files, project
source outside the two definition directories, MCP account/cache homes and
temporary speech inputs are excluded. Back those up using the existing Nexus
and project procedures. Configuration records retain credential references;
default backup never resolves artifact credential references. Explicit remote
capture resolves only the configured artifact provider references in memory;
resolved secrets are not written to the snapshot. Conversation and document content
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

Restore defaults to a **file-state review copy**, including SQL-origin
records. This default never reconnects to or overwrites a database. Queue rows are not
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

## Logical SQL restore

Fresh SQL snapshots retain an explicit, checksummed inventory of authoritative
namespace records. Binary artifacts and other physical JSON files are not guessed
into that inventory. Restore can import those records into an **empty** PostgreSQL or SQLite
namespace using the existing SQL state service. Both source and destination
writers must be stopped; existing records or job rows reject the operation.
Other namespaces are untouched. The destination workspace must be a new directory.

```sh
python -m nexus.core.studio_backup restore /srv/backups/studio-sql /srv/nexus/restored \
  --state-url-env NEXUS_RESTORE_STATE_URL --namespace personal-restored --stopped
```

Connection strings stay in the selected environment reference. Restore never
prints or writes them into reports. The Python API accepts an existing
`StudioSQLState` through `restore_snapshot(..., sql_state=target, quiesced=True)`.
The snapshot's queue rows are retained in the backup for inspection and are
never enqueued in the target. The existing inactive-restore rules above still
apply. Review restored agents, approvals, permissions and credentials before use.
Launch Studio with the returned project/state directories and target namespace.

Files and SQL cannot share one database transaction. Restore prepares and checks
all local bytes, publishes a blocked workspace, imports records and a receipt in
one locked SQL transaction, then marks the workspace ready. A pending marker
blocks Studio startup and further backup. If interrupted after publication,
retain the directory and recover explicitly while target writers remain stopped:

```sh
python -m nexus.core.studio_backup recover-sql /srv/nexus/restored \
  --state-url-env NEXUS_RESTORE_STATE_URL --namespace personal-restored --stopped
```

Recovery checks the destination, namespace, file checksums and private import
payload. An uncommitted attempt retries only when the target is still empty;
a committed receipt verifies the imported records without importing them again.
The Python recovery API is `nexus.core.studio_sql_restore.recover_sql_restore`.
Changed files, changed records, new jobs or another namespace fail closed.
The source snapshot is not needed for recovery after preparation. Temporary
import data is removed only after commit; the final report and SQL receipt remain.

Older SQL snapshots without the record inventory remain supported by default
file-state restore. They cannot be guessed into SQL: create a fresh snapshot
with stopped writers or use the file-state review path. File-origin snapshots
also retain their existing restore path. This adds optional backup metadata and
restore receipts; it does not rewrite existing workspace records or change the
state schema version. Source snapshots remain the rollback copy.

## Object-backed artifact revisions

Default backup still refuses remote references. Opt in to read referenced
objects from the existing S3, GCS, Azure or additional filesystem profiles:

```sh
python -m nexus.core.studio_backup create /srv/nexus/project /srv/backups/studio-objects \
  --state-dir /var/lib/nexus/studio-private --stopped --include-remote-artifacts
```

For SQL state, add the same `--state-url-env` and `--namespace` arguments as above.
The SDK dependencies for the selected profile must be installed. Use the existing
server environment references or provider credential chain. This is a read-only
operation: it never lists buckets, tests profiles with writes, uploads or deletes
provider objects. Only objects named by current or historical artifact records
are captured, including archived revisions. Shared references are fetched once.

The snapshot keeps original records plus a versioned object inventory and private
local byte copies. Reference/profile preflight and size budgets precede provider
reads. Each object must match its recorded SHA-256 and size, up to 16 MiB. Missing,
changed or oversized historical objects, missing credentials and provider failures
prevent publication of the backup. SDK errors do not expose signed URLs or keys.
An explicit later attempt repeats read-only capture, with no remote mutations.

Verification and restore require no provider SDK, credentials or network access.
Restore rewrites only staged references to the captured local bytes. Artifact IDs,
revisions, owners, archived status and citation revision links remain unchanged.
The active store becomes local; retained remote profiles require a fresh storage
test before activation. File and logical SQL restore use the same localization.
A restored workspace can produce a normal local-only backup with no remote reads.
The original workspace records, snapshot metadata and provider objects stay intact.

Install updated recovery tooling before using these snapshots; older tooling
rejects their remote references. Prior local-only snapshots remain readable.
`examples/nexus-bot/src/studio_remote_recovery.py` demonstrates the path using
disposable local object fixtures. The [operator and SDK contract](../postman/contracts/studio-object-backup.md)
records bounds, compatibility and the independent S3 HTTP simulation.

Unrecovered configuration releases must be recovered before backup.
Physical PostgreSQL backup remains a separate infrastructure responsibility.
Logical restore now has real local PostgreSQL 17.11 source/target acceptance;
this does not certify a cloud bucket, personal VM or account-store disaster recovery. GCS/Azure
SDK runtimes and real cloud-account permission/retention behavior remain separate
validation prerequisites; their adapter call contracts are checked locally.

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
`tests/test_studio_sql_restore.py` additionally covers empty-namespace logical
imports, real process exits before/after commit, receipt recovery without replay,
retained document search, paused workflow approval without repeating completed
steps, stale-record removal, changed-state rejection and credential-free CLI errors.
The logical SQL path has local SQLite and real local PostgreSQL acceptance.
The PostgreSQL gate uses separate disposable clusters and verifies inactive
restore, source/other-namespace preservation, occupied-target rejection, actual
process exits before/after commit, a redacted driver connection failure and
target database restart. Recovery never imports executable job rows or repeats a
committed import. Both clusters, the private network and all test volumes are
removed afterward. See [native SQL evidence](STUDIO-POSTGRES-RESTORE-CHECKPOINT.json)
and [the reproducible gate](../deploy/studio/recovery-checks/README.md#native-postgresql-logical-restore).
Personal-VM/account-store recovery, physical database recovery, cluster failover
and host power-loss remain separate validation tasks; no HTTP API is added here.
These checks establish the supported recovery foundation, not complete external parity.

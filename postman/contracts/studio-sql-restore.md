# Logical SQL recovery operator contract

This is an offline framework/CLI operation, not an HTTP endpoint. Reuse
`create_snapshot`, `restore_snapshot` and `recover_sql_restore`; stop every
workspace writer before passing `quiesced=True` or CLI `--stopped`.

Snapshots with a verified logical record inventory can restore into an empty
SQL namespace. Target emptiness includes job rows. The existing SQL workspace
lock and one transaction protect import; unrelated namespaces remain unchanged.
Verified local files, a private pending guard and a receipt bind the destination
to its snapshot, namespace and imported records. An occupied target is rejected,
not merged or overwritten. Original snapshots remain the rollback copy.

Restore publishes an inactive workspace: queued runs become interrupted,
schedules/connectors are disabled, shares revoked and external attempts become
uncertain. No source job rows become executable work. An interrupted import can
be recovered explicitly. A committed import is verified rather than replayed.
Retain the pending directory on failure; do not remove its guard to force startup.

CLI connections use `--state-url-env` and an explicit namespace. Connection
failure exits 1 with `SQL state connection failed; check the configured reference
and driver`. Reports never include the connection URL or driver exception.
Existing file/SQLite defaults and stored record formats remain unchanged.

## Real local PostgreSQL acceptance

`tests/studio_postgres_restore_acceptance.py` runs current framework source and
`examples/nexus-bot/src/studio_recovery.py` against two real disposable PostgreSQL
clusters. `tests/studio_postgres_restore_consumer.py` supplies synthetic state
and actual before/after-commit process crashes. The test additionally verifies
search/revision/artifact retention, no job replay, occupied-target rejection,
source/other-namespace preservation, redacted connection refusal and persistence
after restarting the target database. Every test resource is removed.

This is local database infrastructure proof, not an HTTP service simulation or
personal-VM/account-store drill. Physical backup/PITR, cross-host failover and
host power-loss are outside this gate. See the
[recipe](../../deploy/studio/recovery-checks/README.md#native-postgresql-logical-restore)
and [checkpoint](../../docs/STUDIO-POSTGRES-RESTORE-CHECKPOINT.json).

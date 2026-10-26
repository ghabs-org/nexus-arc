# Studio server termination and recovery

This operational contract preserves existing HTTP request/response formats.
It applies to a main-thread `nexus studio` launch, including SQL worker-only
mode. A service launched in an embedded thread retains its host signal policy.

- SIGTERM requests cooperative shutdown rather than interrupting a state write.
  The controller stops its listener from another thread, then invokes existing
  service cleanup and releases its workspace lock. A worker stops its wait loop
  and invokes the same service cleanup. Normal completion exits zero.
- A stop received during initialization closes the initialized service before
  opening the listener or starting operational dispatch. Repeated SIGTERM during
  cleanup does not interrupt that cleanup. The previous handler is restored on
  return or error. SIGINT retains its existing behavior.
- SIGKILL cannot run cleanup. Restart retains the existing recovery rules;
  it never treats an interrupted external effect as safe to repeat automatically.
- Stopped-workspace backup/restore reuses `nexus.core.studio_backup`. Restores
  remain review copies with automatic effects disabled. SQL-origin snapshots can
  restore to file state without reconnecting to the source database.

The explicit local process drill reads the existing `GET /api/bootstrap`,
`GET /api/agents` and `GET /api/runs/{id}` endpoints after each launch. It checks
that the same paused run and agent remain visible, with no additional runs.
It does not submit HTTP mutations, use provider accounts or change authentication.
Artifact revision bytes and pinned retrieval citations are verified through the
existing framework APIs after restart and restore.

Evidence comes from actual CLI/controller/worker processes plus framework
regressions, not a substitute HTTP provider. See
`tests/studio_server_recovery_acceptance.py` and
`docs/STUDIO-SERVER-RECOVERY-CHECKPOINT.json`. This does not certify public HTTPS,
Nexus login, systemd configuration, remote Docker, Windows signals or native apps.

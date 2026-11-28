# GCS/Azure artifact backup simulation

Created 27 October 2026 from vendor primary contracts linked in
`postman/contracts/studio-object-backup.md`. Postman was signed out, so owner-mock
discovery was unavailable; no hosted resources were fetched or published.

## Purpose

Run the real framework backup and installed Google/Azure SDKs against separate
read-only HTTP dependencies. The application under test is not a simulation member.
Handlers live in `postman/mocks/studio-gcs-backup/` and `studio-azure-backup/`.

## Execution

Use an environment with the repository, pytest and its `gcs,azure` extras installed.
From repository root, start:

```sh
postman simulation run postman/simulations/studio-cloud-backup.sim.yaml \
  --port auto --no-history --output ndjson
```

Take the port from the `listening` event, then run in another terminal:

```sh
NEXUS_ACCEPTANCE_CLOUD_BACKUP_URL=http://127.0.0.1:PORT PYTHONPATH=. \
python -m pytest -o addopts='' tests/studio_cloud_backup_acceptance.py -q \
  --junitxml=/tmp/nexus-cloud-backup-sdk.xml
```

Read request events for both members, then stop the simulation with Ctrl-C.
The recorded run used CLI 1.75.0, google-cloud-storage 3.17.1 and
azure-storage-blob 12.31.0. No application process remains listening afterward.

## Assumptions

- Endpoint validation permits only HTTP `127.0.0.1` with an explicit port and
  no credentials/path/query. Test clients bypass outbound proxies. GCS emulator
  credentials are anonymous; Azure credentials are fixed synthetic data.
  These do not certify real cloud authentication or permissions. (observed 2026-10-27)
- GCS 3.17.1 asynchronously fetches bucket metadata for observability. The fixture
  serves documented `Buckets.get` alongside object reads. This is not bucket or
  object listing. Azure transient retry timing is not tested. (observed 2026-10-27)
- Deliberately overlong responses exercise final byte guards; they are malformed
  upstream behavior, not a claimed normal cloud response. SDK/model/package
  success is separate from cloud retention/history and account restore. (observed 2026-10-27)

## Observations

<!-- begin:observations -->
2026-10-27: both members served actual SDK traffic. Two SDK checks passed;
all calls were reads, no unsupported route returned 501, and offline restore/reopen
made no provider calls. The task-owned simulation stopped cleanly. Exact results,
log checksum and the 42 existing regressions are recorded in
`docs/STUDIO-CLOUD-BACKUP-SDK-CHECKPOINT.json`.
<!-- end:observations -->

## What didn't work

- 2026-10-27: the initial GCS fixture returned 501 for the SDK's asynchronous
  `/storage/v1/b/evidence` metadata read. Object reads passed via SDK fallback,
  but that run was insufficient contract coverage. The final fixture models
  `Buckets.get`; final request logs contain no 501 responses.

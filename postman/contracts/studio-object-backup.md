# Offline artifact backup contract

`create_snapshot(..., include_remote_artifacts=True)` and the operator CLI's
`create --include-remote-artifacts --stopped` opt into provider reads. Default
backup behavior still rejects remote references. No HTTP/UI endpoint starts this
offline operator action. All workspace writers must be stopped.

The existing artifact profile resolver and S3/GCS/Azure/filesystem adapters supply
credentials and byte access. Profile credentials remain environment references;
backup never starts Studio, probes storage with writes, lists buckets, or deletes
objects. It reads current and historical catalog artifact records, including
archived revisions. Duplicate references to the same object are fetched once.
Conflicting checksums/sizes, invalid profiles, missing files and incomplete
configuration releases fail before provider reads. Stored size is at most 16 MiB
per object; the existing snapshot file/byte limits also apply before capture.

`ArtifactStore.get_bounded(bucket, key, max_bytes)` is an additive optional read
capability. Existing stores implementing only `get` remain compatible, with
post-read size/checksum validation. Built-in stores bound their reads: S3 checks
ContentLength and bounds the streaming body; GCS checks metadata, then requests
raw bytes with a range and generation precondition; Azure checks metadata and
requests an explicit range. Empty objects use their verified zero-length metadata.
Provider errors expose no credential-bearing SDK messages.

New v1 snapshots optionally include `remote_artifacts`, with format
`nexus-studio-backup-artifacts/v1` and an exact `objects` inventory. Each entry has
`store_id`, `kind`, `bucket`, `blob_key`, `sha256`, `size`, and a deterministic
`local_key`. Bytes live under `state/artifact-objects/studio/{local_key}` and are
also included in the ordinary snapshot file inventory. Original artifact metadata
stays unchanged in the backup. No cloud versions are guessed: an overwritten or
missing historical object fails checksum/availability checks.

Verification is offline. It checks complete reference coverage, source profile
agreement, deterministic paths, sizes and checksums. Restore copies and checks
all files before rewriting staged artifact references to the local copies. IDs,
revisions, owners, archive flags and citation references stay unchanged. Remote
storage profiles remain configured but require a new probe before activation.
The active store becomes local. Source workspace and provider are untouched.
Logical SQL restore imports the localized records through its existing empty-
namespace transaction/receipt flow. No source queue rows become executable jobs.

Interrupted/failed capture publishes no destination. Retrying is an explicit new
read-only backup attempt. No account credentials are required to verify or restore
a completed snapshot. Older file-only snapshots retain their previous format and
restore path. Older readers reject remote snapshots rather than claiming a
complete restore; upgrade the recovery tooling first.

## Independent S3 SDK simulation

`postman/simulations/studio-object-backup.sim.yaml` exercises the real boto3 client
against local HTTP fixtures using synthetic credentials. Only known GET object
paths are supported. Other operations return 501. Fixtures include 404, 403,
503, changed bytes and oversized content; success captures two revisions and
restores without a provider factory. GCS/Azure bounded-client contract checks are
separate from real SDK/account acceptance.

Primary reference contracts:

- [AWS GetObject](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/get_object.html)
- [GCS Blob download](https://docs.cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.blob.Blob)
- [Azure ContainerClient download_blob](https://learn.microsoft.com/en-us/python/api/azure-storage-blob/azure.storage.blob.containerclient?view=azure-python)

## Independent GCS and Azure SDK simulation

`postman/simulations/studio-cloud-backup.sim.yaml` serves separate read-only
GCS and Azure HTTP contracts. `tests/studio_cloud_backup_acceptance.py` constructs
the actual `GCSStore`/`AzureStore` and their installed vendor SDKs; no HTTP client
is replaced. The GCS SDK uses its documented emulator endpoint and anonymous
credentials. Azure signs requests using a synthetic fixture-only account key and
an explicit loopback BlobEndpoint. The mocks do not certify real authentication.

Both paths capture two current/historical revisions, restore their private bytes
and pinned document citation offline, preserve source metadata and publish no
snapshot on missing, denied, corrupt or oversized objects. Empty objects require
only metadata, oversized objects are rejected before media reads, and deliberately
overlong media responses exercise the final byte guard. Requests contain bounded
ranges; GCS media also requires the observed generation precondition. GCS 412
generation change and 503 failure are exercised without retry. Azure transient
retry timing, cloud permissions, retention and version history remain separate.
No object writes, deletes or bucket listings are supported; unknown calls return
501. Fixture observation routes expose only synthetic request metadata.
The current GCS SDK additionally reads bucket metadata for its observability
cache. This read follows [Buckets.get](https://docs.cloud.google.com/storage/docs/json_api/v1/buckets/get);
it is distinct from bucket/object listing. Actual cloud IAM and optional SDK
observability behavior remain live-account concerns.

The service shapes follow [GCS Objects.get](https://docs.cloud.google.com/storage/docs/json_api/v1/objects/get),
[Azure Get Blob](https://learn.microsoft.com/en-us/rest/api/storageservices/get-blob)
and [Get Blob Properties](https://learn.microsoft.com/en-us/rest/api/storageservices/get-blob-properties).
Owner-mock discovery was unavailable because the Postman CLI was signed out;
these repository-owned fixtures derive from the primary contracts above.
See [execution context](../simulations/studio-cloud-backup.context.md) and
[SDK evidence](../../docs/STUDIO-CLOUD-BACKUP-SDK-CHECKPOINT.json).

Orbit discovery found GCS object download resource
`urn:orbit:endpoint:v1:aNKmVs6jdw8vzAaWATGSHoj2gYnDZFn5:google:retrieves-an-object-or-i`.
Its integration brief confirms revision/precondition support but omits range
details. The primary SDK documentation above supplies those details. No vendor
account, shared Postman workspace or published dependency is changed by this work.

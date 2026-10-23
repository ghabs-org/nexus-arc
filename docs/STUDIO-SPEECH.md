# Voice jobs and pinned settings

Studio supports browser recording, local Whisper and API transcription, local
Piper and API speech, and browser playback. A speech request selects an explicit
configured profile. No unavailable local model silently selects a paid API.

## Queue behavior

Accepted voice jobs retain a versioned copy of their profile settings. Editing
or removing that profile later does not change the accepted job's provider kind,
action, model, local model path, available voices or credential reference. The
chosen voice and language remain in the existing job payload. Relative model
paths resolve to absolute paths before dispatch.

The public run record includes the selected profile name, action and a revision
digest. Private profile settings stay in the existing worker payload. API clients
cannot supply their own trusted snapshot, including through the generic runs API.

Credentials are resolved from the pinned environment reference at execution using
the current account context. Rotating a credential value works without changing
the queued settings. Revoking it causes a visible failure; another profile or
account is never substituted. Current project access remains authoritative.

The snapshot pins configuration, not local model file bytes, installed software
or service availability. Replacing a model at the same file path changes that
external file; manage model versions explicitly when reproducibility requires it.

## Upgrade behavior

Completed voice runs remain readable. Pending jobs created before profile
snapshots existed cannot prove their original settings. They fail with a request
to review and submit again, rather than resolving potentially changed settings.
Corrupt snapshots also fail before provider invocation. The existing owned-input
cleanup runs for those speech-execution failures.

No durable-state rewrite or new database table is required. Existing JSON job
payloads preserve the versioned snapshot through SQL queue restarts. Private
snapshots contain credential references only, not resolved API keys.

## Temporary audio lifecycle

Uploaded recordings and local synthesis output use the private `speech-inputs`
directory. Owner records now include the accepted run ID. Registration, binding
and dispatch share the existing workspace lock. A recording cannot be rebound to
another run through the generic runs API. Existing owner records without a run ID
remain supported: SQL queue payloads protect their accepted recordings.

Cleanup runs at startup, shutdown, speech completion/cancellation and expired
worker-lease recovery. It removes abandoned managed audio and stale owner records.
Queued durable recordings survive HTTP/worker restarts. Active worker leases
protect recordings and local output, even if a cancellation receipt already
exists. Non-durable pending jobs become interrupted on restart; their audio is
removed rather than replayed. Access denial before provider execution also
releases temporary recordings.

Only generated UUID audio filenames are collected. Directories and unrelated
filenames are left alone; symbolic links are removed without following them.
Saved speech artifacts are separate resources and remain available normally.
Local synthesis output starts with private file permissions. Cancellation waits
for the existing bounded child process before unlinking its files, preventing a
late write from recreating abandoned audio. Local process limits remain 90 seconds
for synthesis and 130 seconds for transcription; cancellation may wait for the
child to exit or reach that limit. It does not publish cancelled output.

The cleanup example needs SQLite support but no account, HTTP service or model:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_speech_cleanup.py
```

## Local verification

Required tests cover configuration edits/removal, model-path changes, credential
rotation/revocation, caller snapshot rejection, SQL restart, API metadata,
malformed snapshot cleanup and existing team permissions.

The real optional OpenAI SDK can be checked against a repository-owned Postman
fixture, using only synthetic credentials and audio:

```sh
postman simulation run postman/simulations/studio-speech.sim.yaml --port 49832 --output ndjson
```

In another terminal:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_speech_pins.py
PYTHONPATH=. python -m pytest -o addopts='' tests/studio_speech_sdk_acceptance.py
```

The fixture follows the existing client request contract and the installed SDK;
no shared vendor mock is used. It checks a queued synthesis after profile removal,
queued transcription, private artifact storage, input cleanup and a single-attempt
provider error. Synthetic audio proves transport and storage, not audible quality.

Required lifecycle checks include real subprocess exits in file/SQL modes,
queued cancellation without workers, late local-output writes, expired worker
leases, concurrent registration/collection, ownership persistence, private file
permissions and filename collisions. These close queued-profile pinning and local
interrupted/startup audio-cleanup acceptance within milestone 8. Local model/API
live checks and physical microphones/playback remain separate work. Full
milestone 8 acceptance remains open.

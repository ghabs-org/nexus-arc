# Queued speech settings contract

`POST /api/studio/speech` retains its existing profile, text/audio, voice and
language request fields and returns a queued run. `GET /api/studio/speech`
retains passive profile readiness and billing metadata.

At dispatch, the server copies the selected configured profile into a private,
versioned job snapshot. Settings include kind/action, model selection or absolute
local model path, available voices and the credential environment reference.
The snapshot contains no resolved credential values. Caller-supplied snapshots
are discarded, including requests through `POST /api/runs`.

Queued run responses additionally identify `speech_profile`, `speech_action` and
`speech_profile_revision` (a SHA-256 of the private versioned settings snapshot).
Private settings, model paths and credential references are not added to public
run records. Changes or removal of a configured profile after queuing do not
change that job's settings. Current user permissions and current credential
values remain authoritative at execution. A removed/revoked credential never
selects another provider automatically.

Durable jobs retain snapshots through the existing job store; no new scheduling,
authentication or credential subsystem is introduced. Old pending speech jobs
without a snapshot fail explicitly and require a reviewed new submission. They
never silently resolve a changed profile. Existing completed runs stay readable.
A corrupt snapshot fails before provider invocation. Owned temporary input audio
is removed on these failures through the existing cleanup path.

Temporary input is bound to one accepted run before dispatch. Rebinding it through
the generic runs endpoint is rejected. Cancellation before execution, access
denial, completion, expired worker leases and startup/shutdown release abandoned
audio. Durable queued jobs retain their recordings across restart. Active leases
protect audio still in use by another worker. Local synthesis cancellation waits
for its bounded process before removing output; cancelled audio is not published.

The pin covers profile settings, not local model file contents, installed
executables or model/API availability. Interrupted/startup cleanup is locally
verified; real model/API and physical-device checks remain separate acceptance work.


Local-worker portability follow-up (26 October): Whisper applies POSIX resource
limits only where the `resource` module exists. POSIX CPU/Linux address-space
limits and the parent 130-second wall timeout are unchanged. No request/response,
profile snapshot, credential scope or cleanup contract changes. Controlled platform
branches do not certify native Windows inference.

Actual local Piper/Whisper inference now has a separate opt-in acceptance consumer
(`studio_local_speech_acceptance.py`). It uses explicitly installed models and
normal Studio jobs; it never substitutes an API provider. Model preparation was
operator-approved, separate from runtime execution. Physical devices, API-account
acceptance, native Windows/Linux and VM operation remain separate.

Linux model-runtime follow-up (27 October): `studio_linux_speech_acceptance.py`
now executes the existing local speech consumer with real Piper/Whisper models
inside a network-disabled Linux/arm64 container. Normal Studio jobs generate and
transcribe private audio and remove temporary files. Three model hashes and the
current worker source hash are checked. No HTTP speech provider is simulated or
substituted, and no API payload/profile/authentication contract changes.
The controlled tiny-English result recognizes core words but mishears the initial
name; runtime success is not general transcription quality. Physical device,
Windows/installed-platform, process-tree cancellation and personal-VM checks
remain separate. See `docs/STUDIO-LINUX-SPEECH-CHECKPOINT.json`.

Linux cancellation follow-up (27 October): local speech commands now use the
existing isolated subreaper via a private bounded input frame plus owner-lifetime
pipe. Cancellation, shutdown, wall timeout and owner death stop owned detached
descendants before temporary audio cleanup. No global process scan, automatic
retry or provider fallback is introduced. Unavailable ownership primitives fail
before launching a local command. Finite input/output and command errors retain
their existing public behavior; profiles, HTTP routes and queue formats do not
change. macOS/Windows retain their prior local-process path. Real Linux child
cases and Piper/Whisper model jobs pass separately; no installed/VM/DMG claim is
made. See `docs/STUDIO-SPEECH-LIFECYCLE-CHECKPOINT.json`.

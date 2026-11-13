# Passive speech profile readiness

The existing `GET /api/studio/speech` response retains `items`, profile IDs, actions,
billing, voices and `model_ready`. The latter now reflects all known local
prerequisites, rather than model-file presence alone.

Each item adds `missing_prerequisites` (display names only) and
`verification_scope`. Local Whisper checks model-file presence, the Python
Whisper module spec, and FFmpeg availability. Piper checks model-file presence,
the Piper executable and adjacent `.onnx.json` voice configuration. No model
imports, subprocesses, downloads, installation hooks or API requests run.

A true local readiness flag means passive prerequisites were found; it does not
prove model loading or physical-device recording/playback. API profiles retain
existing availability behavior and explicitly report `account not checked`.
No account credentials, model paths or document/audio contents are returned.

The voice picker lists missing local prerequisites and rejects that selection
before microphone capture or speech submission. It retains the explicit selected
profile and never auto-selects a separately billed API. Existing queue payloads,
pinned settings, worker cleanup and provider formats are unchanged.

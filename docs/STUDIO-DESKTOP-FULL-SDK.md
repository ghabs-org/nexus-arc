# Full desktop SDK profile

The `full` desktop dependency profile extends the existing `connected` profile to
44 explicitly checked SDK imports. It includes local Whisper/Torch software,
finance, GCS/Azure storage, gRPC messages, Telegram/Discord, Redis, MQTT/Kafka,
terminal UI, hot reload, and existing bot-runtime dependencies. Core and connected
profiles remain available. No provider or connector activates during packaging.

This name describes the selected Python SDK set, not completed product or live
acceptance. Models, FFmpeg, Piper, host subscription CLIs, Docker, Node, credentials,
and platform permissions remain explicit prerequisites where applicable.

## Build from the recorded native dependency snapshot

The macOS arm64 / Python 3.13 build has a checked-in version snapshot. It contains
146 pinned build dependencies, excluding the editable Nexus source. It is not a
cross-platform lock or a signed/hash-verified wheel inventory.

```sh
uv venv /tmp/nexus-full-build --managed-python --python 3.13.16
uv pip install --python /tmp/nexus-full-build/bin/python -r apps/studio-desktop/requirements/full-macos-arm64.txt
uv pip install --python /tmp/nexus-full-build/bin/python --no-deps -e .
/tmp/nexus-full-build/bin/python apps/studio-desktop/build_desktop.py --sidecar-only --profile full --output /tmp/nexus-full-sidecar
```

Use a non-framework interpreter for macOS packaging. The builder rejects directory links
that Tauri would drop. The relocated example dereferences file aliases, matching
native resource copying, before testing SDK and worker behavior.

For a newly resolved native build instead, explicitly install
`nexus-arc[studio-desktop-full]` from the chosen source. Each OS/architecture needs
its own verified dependency and artifact record. Native imports or resource checks
that fail stop packaging; no SDK installation or reduced-profile fallback occurs.
`--development-wrapper` cannot be combined with connected/full profiles.

The builder collects explicit SDK imports, recursive distribution metadata,
SQLAlchemy dialects, Bedrock/S3 model descriptions, Whisper assets and tokenizer
plugins. Three additional offline checks verify speech mel-filter/tokenizer data,
cloud service descriptions and gRPC message serialization. Kafka protocol
schemas, Eventlet hub/DNS modules and channel async drivers receive explicit
collection rules; their runtime discovery is not visible to static analysis. They never instantiate
provider clients, load a speech model, transcribe user audio or request a URL.
Import and resource failures report only safe exception classes.

The resulting one-folder backend must pass these same checks with an empty PATH
and isolated HOME before staging. Preserve the whole directory when relocating.
The manifest records dependency versions, profile outcomes, source/frontend
hashes and the bundle tree digest.

## Native release consumer and runtime proof

With the chosen build environment active, `npm run build:full` packages the full
profile before Tauri and uses `src-tauri/binaries/tauri.bundle.conf.json`. The
connected build command now also uses this generated resource configuration.
Without it, a release shell cannot find the owned sidecar resource.

For an isolated relocated-backend drill:

```sh
NEXUS_ACCEPTANCE_SIDECAR=/tmp/nexus-full-sidecar/nexus-backend-aarch64-apple-darwin/nexus-sidecar NEXUS_ACCEPTANCE_PROFILE=full PYTHONPATH=. python -m pytest tests/studio_desktop_bundle_acceptance.py -q -o addopts=''
```

The example selects the framework profile, copies the complete bundle into a path
with spaces, renames its executable, clears host Python environment, verifies
profile data, authenticates Studio, saves an agent, serves assets, checks valid and
unreadable PDFs, then closes the owned backend. It uses no live accounts or user
workspaces. Exact evidence belongs in `STUDIO-DESKTOP-FULL-SDK-CHECKPOINT.json`.

SDK imports and local package data do not establish every adapter's API/version
compatibility, permissions, account billing, service invocation or quality. Actual
local speech models and physical microphone/playback remain unverified until
explicitly configured and exercised. Native installation with this full profile,
active descendant cleanup, signatures, updates, Linux/Windows, and personal VM
operation remain separate release gates. Earlier intermittent PDF stability
history remains visible; a passing package drill does not prove its cause fixed.

No persisted records or source-mode worker command formats change.

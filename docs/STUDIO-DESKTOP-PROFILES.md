# Desktop dependency profiles

The portable backend builder accepts a fixed dependency profile. A profile is a
packaging gate, not activation of a provider, permission to use an account, or a
claim that the entire application has live acceptance.

- `core` preserves the small development packaging path. Optional SDKs may be
  absent; the manifest reports what is present.
- `connected` requires 17 distributions: OpenAI, Anthropic, MCP, Slack SDK,
  SQLAlchemy, PostgreSQL/MySQL/SQL Server/Oracle drivers, JSON Schema and reference
  handling, Bedrock/S3 SDK, MongoDB, PDF, Pillow, HTTPX, and cryptography.

The framework owns the fixed list in `nexus/core/studio_desktop_profiles.py`.
The build consumer and example only select it. Arbitrary module names cannot be
passed to the executable's check operation.

## Build and verify

```sh
uv venv /tmp/nexus-connected-build --managed-python --python 3.13
uv pip install --python /tmp/nexus-connected-build/bin/python -e '.[studio-desktop-connected]'
/tmp/nexus-connected-build/bin/python apps/studio-desktop/build_desktop.py --sidecar-only --profile connected --output /tmp/nexus-connected-sidecar
```

Missing distribution metadata or failed imports stop the build before compilation
or output mutation. The compiler includes the selected import targets and their
recursive dependency metadata. SQL dialects and Bedrock model data receive
explicit collection rules. Before staging, the produced executable must import
the same fixed profile from an empty working folder with an empty PATH and HOME.
Compiler or frozen-import failures preserve the previous bundle.

The manifest includes the profile name, imported module targets, package versions,
source/assets and runtime evidence. The check exposes exception classes only;
exception values may contain environment-specific information. The standalone
operation is `--nexus-bundle-check connected`; it exits nonzero on failure.

For the current one-folder layout, relocate the whole bundle directory, including
`_internal`. The example does this automatically:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_desktop_bundle.py --executable /tmp/nexus-connected-sidecar/nexus-backend-aarch64-apple-darwin/nexus-sidecar --profile connected
NEXUS_ACCEPTANCE_SIDECAR=/tmp/nexus-connected-sidecar/nexus-backend-aarch64-apple-darwin/nexus-sidecar NEXUS_ACCEPTANCE_PROFILE=connected PYTHONPATH=. python -m pytest tests/studio_desktop_bundle_acceptance.py -q -o addopts=''
```

With the selected build environment active, `npm run build:connected` packages this
profile before Tauri. No dependency installation occurs inside the build helper
or while using Studio. Native OS, installation and signing gates remain separate.

The additional `full` profile and pinned macOS dependency snapshot are described
in [Full desktop SDK profile](STUDIO-DESKTOP-FULL-SDK.md). That profile adds
offline package-data checks; it does not replace live adapter acceptance.

## Scope still open

Passing imports verifies that SDK code and native libraries can load. It does not
verify API compatibility of every adapter, network protocols, credential scopes,
account billing, database connections, or live external effects. Existing protocol
and behavioral acceptance remains required.

This connected profile is not the SDK-complete release profile: finance, local
Whisper/model tooling, gRPC, GCS/Azure stores, other channel SDKs and any remaining
optional capabilities need their own reviewed packaging and invocation evidence.
Models and credentials are never copied into the bundle. Host CLI subscriptions,
Docker, Node and model binaries remain explicit external prerequisites. Missing
software never causes an automatic switch to an API provider.

No persisted schema changes are required. Source-mode PDF, speech and finance
worker commands retain their existing compatibility behavior.

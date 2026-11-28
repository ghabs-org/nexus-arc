# Native desktop handshake simulation

25 October 2026. This repository owns the Nexus handshake contract; its local
stand-in exercises impostor services. No hosted mock, account or public endpoint
is used. Success runs against a real Studio sidecar process instead.

Start from repository root on an explicit free port:

```sh
postman simulation run postman/simulations/studio-desktop-readiness.sim.yaml --port 49741 --no-history --output ndjson
```

Confirm the `listening` event's port. Build the native probe using
`cargo build --manifest-path apps/studio-desktop/src-tauri/Cargo.toml --example backend_probe --offline`,
then run `NEXUS_NATIVE_SIMULATION_PORT=49741 PYTHONPATH=. python -m pytest tests/studio_desktop_native_acceptance.py -q -o addopts=''`.
Missing probe, CLI or port fails explicitly; no tests skip prerequisites.

The member is named `api` so the native fixed `/api/desktop/handshake` request
reaches its `/desktop/handshake` handler. Fixture-only mode/call routes select
HTML, redirect, oversized, forged-proof or legacy bootstrap responses and record
header presence, never header values. Run these cases sequentially because
mode and request history are shared.

Observed: six native acceptance tests passed, zero skips. The sole `api` member
served 70 requests with 0 failures: 60 handshake GETs (48 HTTP 200, 12 HTTP 302),
five fixture POSTs and five call reads. Every handshake carried a distinct 64-hex
challenge and no cookie or bearer. No redirect target was requested. Counts
reflect bounded polling, not retries of mutations.

These checks prove the handshake client rejects impostors without disclosing a
secret, and that a real sidecar verifies and gates its API. They do not prove
workspace ownership, crash cleanup, portable runtime, installed OS lifecycle or
signing. See `docs/STUDIO-DESKTOP-SESSION-CHECKPOINT.json`.

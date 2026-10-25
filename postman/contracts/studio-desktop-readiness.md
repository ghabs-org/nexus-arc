# Native desktop handshake contract

The native launcher starts `nexus studio --desktop-launch-stdin` and writes a
per-launch 32-byte secret as one lowercase-hex line to its stdin. Readiness uses
`GET /api/desktop/handshake`, which exists only when that flag is set.

Request: header `X-Nexus-Desktop-Challenge` with 64 lowercase hex characters,
fresh and random per attempt. No cookie, bearer or secret is sent. Any other
challenge shape gets HTTP 400.

Response: `200 {"proof": "<64 hex>"}` where the proof is
`HMAC-SHA256(secret, "nexus-desktop-launch/v1\0" + challenge + "\0" + workspace + "\0" + port)`.
`workspace` is the canonical workspace path and `port` the listening port.

The launcher accepts the backend only when:

- HTTP status is exactly 200; redirects are never followed and proxies are bypassed.
- The body is valid JSON of at most 4 KiB.
- `proof` verifies in constant time against its own challenge, canonical
  selected workspace and port.
- The owned child has not terminated and app shutdown has not started.

Native startup has a 20-second deadline and one-second per-request timeouts.
After verification the launcher sets the HttpOnly `nexus_desktop_session` cookie,
`HMAC-SHA256(secret, "nexus-desktop-session/v1")` in hex, for `127.0.0.1`. With a
launch credential, `/api/` routes without their own authentication return
`401 {"error": "Open this workspace from Nexus Studio Desktop"}` without it.
Readiness or session failure stops the owned child; Studio never navigates to an
unverified service.

Success runs against a real Studio sidecar process. Impostor shapes use the local
Postman stand-in for the owning Nexus service: HTML, redirect, oversized body,
a forged well-formed proof, and the unauthenticated bootstrap shape the previous
launcher accepted. Its `api` member prefix preserves the fixed `/api/desktop/handshake`
route; the member handler sees `/desktop/handshake`. Fixture control routes are
not production API additions.

Portable Python bundling and clean installed OS builds remain separate desktop
release gates.

## Parent lifetime

The native shell retains the stdin writer after sending the credential. For
`--desktop-launch-stdin`, the backend reads that credential without prefetching
and monitors the remaining private channel. EOF, a broken stream or unexpected
additional bytes request normal HTTP shutdown. No PID polling or process-name
termination is used in production. The server then runs existing service/job/
artifact/audio cleanup. The monitor uses unbuffered OS reads so interpreter exit
cannot block on a daemon holding stdin's BufferedReader lock.

Callers using this native-only flag must keep the writer open for the launch's
lifetime. Scripts that send the credential and immediately close stdin now end
the launch deliberately. Browser/CLI Studio without that flag, team mode and
programmatic `launch=` handlers without `parent_input` retain their behavior.
Parent monitoring is rejected for worker-only or non-native launches. No HTTP
route, authentication format, durable record or schema changes.

Actual local process checks cover graceful EOF, a terminated/killed synthetic
shell, EOF before readiness, invalid trailing bytes and process exit while the
parent pipe remains open. These prove backend/listener shutdown on the tested
macOS host. They do not certify live CLI/container descendant cleanup or native
installed window lifecycle on every OS.

## Workspace ownership and stop

- The sidecar takes the workspace's OS file lock before touching it. If another
  Studio holds it (waiting up to 5 s for a closing native instance), the process
  exits with status **75** and a message naming the holder's PID and port. The
  shell maps 75 to its "workspace in use" screen, **64** (native launch on a home,
  system or app-bundle folder; refused before anything is written) to its
  "unsafe workspace" screen, and **65** (workspace saved by
  a newer release, left unchanged) to its "newer workspace" screen; other early
  exits stay "readiness" failures. Startup errors exit with their status and
  message; the sidecar never kills its own group before a normal shutdown.
- To stop, the shell writes `stop\n` to the private stdin (any byte ends the
  launch), waits up to 12 s for exit, then kills the sidecar's process group.
  After its normal close the sidecar, as group leader, kills leftover
  descendants itself, so a vanished shell leaves none behind. A 10 s watchdog
  bounds a hung close.
- The bundled executable also accepts `--nexus-desktop <args>`, routed to the
  existing `nexus desktop` CLI. The shell uses `--nexus-desktop import --from SRC
  --dir DST --json` and reads `{"success": …, "error": …}` from stdout.

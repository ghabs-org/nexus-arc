# Nexus Studio Standalone Desktop Distribution

Current macOS release **1.0.2** is a full-SDK Apple Silicon build, Developer ID
signed with accepted app/DMG notarization and stapled tickets. The signed
DMG-installed runtime and Gatekeeper checks pass. The native updater passes
signature/version checks and an isolated install/rollback fixture. Publication
of its release feed and another physical Mac remain separate checks.
See [signed release evidence](STUDIO-DESKTOP-SIGNED-RELEASE-CHECKPOINT.json).

Milestone 9 remains open for installed Windows/Linux, Windows signing, other
physical devices and remaining release-distribution acceptance. No VM work was
performed. Rebuild another DMG only after an explicit user request.

## Historical prototype background

The prototype notes below retain their original scoped results. Current release
evidence above supersedes earlier missing-signing/full-SDK statements.


Milestone 9 remains an incomplete standalone distribution and stays last in the
delivery order. The native shell now owns a process-group sidecar, drains its
output, verifies the backend it launched (see *Authenticated native startup*
below) and keeps a local error screen on failure. Closing/quitting stops the
owned child; late spawns are rejected and stopped. The default native workspace
is the Tauri application data directory's `workspace` child.

Rust and Python tests, a real sidecar checked by the compiled readiness probe,
Postman impostor simulations and a macOS development-build window drill provide
scoped proof. Checked-in development wrappers (debug builds only) still use
build-machine Python; the default packager creates a portable one-folder core/PDF
backend. Ad-hoc signed (not Developer ID) macOS arm64 DMGs of the core and connected
profiles installed and ran from an empty environment outside the repo (see
[macOS release evidence](STUDIO-DESKTOP-MACOS-RELEASE-CHECKPOINT.json) and
[packaging](STUDIO-DESKTOP-BUNDLE.md)). The native
backend monitors its private parent input pipe: parent EOF requests normal
HTTP/service shutdown, including after abnormal shell exit. Native and terminal
launches share a kernel-owned workspace lock, and macOS development-build drills
cover window close, a killed or terminated shell, and launches on a workspace
already in use (see *Workspace ownership and shutdown*). Active CLI/container
descendants, Windows/Linux packages, signing and full SDK coverage of the
installed app remain open. See [bundle evidence](STUDIO-DESKTOP-BUNDLE-CHECKPOINT.json),
[ownership evidence](STUDIO-DESKTOP-OWNERSHIP-CHECKPOINT.json),
[parent lifetime evidence](STUDIO-DESKTOP-PARENT-CHECKPOINT.json),
[session evidence](STUDIO-DESKTOP-SESSION-CHECKPOINT.json),
[startup evidence](STUDIO-DESKTOP-STARTUP-CHECKPOINT.json) and
[the next-agent handoff](STUDIO-NEXT-AGENT-PLAN.md).

## Authenticated native startup

1. The shell generates a 32-byte secret per launch and writes it as one hex line
   to the owned sidecar's stdin (`nexus studio --desktop-launch-stdin`). It never
   appears in argv, environment, URLs, frontend assets or logs.
2. The shell polls `GET /api/desktop/handshake` with a fresh random
   `X-Nexus-Desktop-Challenge`. The backend answers
   `HMAC-SHA256(secret, "nexus-desktop-launch/v1" | challenge | workspace | port)`.
   The shell verifies in constant time. Another loopback service, or another
   Studio, cannot answer, and learns nothing reusable from the request.
3. The shell sets a host-only `HttpOnly; SameSite=Strict` `nexus_desktop_session`
   cookie (an HMAC derived from the secret) for `127.0.0.1`, confirms it is in the
   webview store, then navigates. Any failure stops the child and shows the
   local error screen.
4. With a launch credential, every `/api/` route without its own authentication
   requires that session on GET, POST and DELETE. `/api/bootstrap` releases the
   existing CSRF token only to the session holder. A2A, MCP, public shares,
   OpenClaw, webhooks, channel events and callbacks keep their own credentials.

`nexus studio` without the flag, the browser-based `nexus desktop launch` and
team mode are unchanged. Cookies are host-scoped, not port-scoped; the shell
navigates only to the verified origin.

## Choosing a workspace

The desktop app opens the workspace it served last (remembered in
`desktop.json` in the app's configuration folder, only after that workspace was
verified), or `workspace` in the app-data folder the first time. The **Workspace**
menu offers:

- **Open Workspace…** (⌘O): pick any folder; Studio stops the current backend
  cleanly and serves the chosen one in place.
- **Import Workspace…**: copies an existing workspace (for example one used with
  `nexus studio`) into the app's own `workspaces/` folder through the framework's
  `nexus desktop import` (refused while another Studio has the source open, or
  when source and destination nest), then opens the copy. The original is never
  modified.
- **Show Workspace in Finder** (Open Workspace Folder elsewhere): shows where its
  `agents/`, `workflows/`, `files/` and private `.nexus/` state live.

Native launches refuse your home folder or any folder containing it, system
folders and anything inside an app bundle (status 64, "choose a dedicated folder").
A remembered folder that has disappeared is reported, never silently replaced by
another. Terminal `nexus studio` keeps accepting its existing folders.

## Workspace ownership and shutdown

- Every local desktop server (native sidecar, `nexus studio`, the browser
  launcher's child) takes the workspace lock before touching the workspace. A
  second launch exits with status 75 naming the holder's PID and port; the
  native app shows "This workspace is already open". Native launches wait up to
  5 s for a previous instance that is still closing. Team and worker-only modes
  do not lock.
- Window close or app exit hides the window and sends one line on the sidecar's
  private stdin. The sidecar closes Studio normally (runs marked interrupted,
  temporary audio removed, MCP sessions closed, lock released). The shell waits
  up to 12 s, longer than the sidecar's own 10 s bound, then kills whatever
  remains of the sidecar's process group.
- If the shell dies, the pipe closes and the sidecar runs the same close. A 10 s
  watchdog bounds a hung close. Because the shell starts the sidecar as its own
  process-group leader, the sidecar then kills leftover descendants on POSIX
  (agent CLIs and their children were drilled). Startup errors are reported with
  their message and status, never by killing the group.
- Outside the group: stdio MCP servers (the MCP SDK gives each its own session)
  are terminated by Studio's normal close; if the backend dies without closing,
  servers that exit on stdin EOF still stop, servers that ignore EOF remain.
  Docker sandbox containers outlive a dead backend by at most their own timeout
  (1–120 s), then exit and are removed.
- A workspace saved by a newer Studio release is refused with status 65 and left
  unchanged; the native app says so and suggests that release or a compatible
  backup. Upgrade, rollback and uninstall (data kept) were drilled on macOS.

The target distribution will bundle the UI and Python runtime so users do not
need a source checkout or a separately installed Python interpreter. The
subsystems below describe the target architecture; completed runtime and
platform evidence must be recorded before this milestone can be accepted.

## Architecture

The standalone desktop architecture decouples the native presentation shell from the
reusable Studio framework runtime:

```
+-------------------------------------------------------+
|  Tauri 2 Desktop Shell (Rust + Platform Webview)      |
|  - Window management (1280x820, min 800x600)          |
|  - Single-instance activation and focus               |
|  - Restricted capability permissions (shell, process) |
+---------------------------+---------------------------+
                            |
           Loopback IPC / Authenticated HTTP (127.0.0.1)
                            |
+---------------------------v---------------------------+
|  Nexus Studio Python Backend (Owned Child Process)     |
|  - Workspace single-instance lock (.nexus-workspace)  |
|  - Dynamic loopback port allocation (0 / 5003+)       |
|  - Per-launch HMAC handshake + HttpOnly session      |
|  - Full agent, workflow, catalog, and worker runtime  |
+-------------------------------------------------------+
```

## Core Subsystems

### 1. Workspace Instance Locking

Every workspace directory enforces a single-writer lock via `.nexus-workspace.lock`:
- An OS file lock (`flock` on macOS/Linux, `msvcrt` on Windows) held by the serving process; the kernel releases it when that process exits or crashes. No PID checks decide staleness, so a reused PID is never mistaken for the owner.
- The file's JSON (PID, requested port, timestamp) is status metadata, trusted only while the lock is held. The file is kept after release.
- Rejects concurrent launches against the same workspace; a second native app launch focuses the existing window through the single-instance plugin.

### 2. Loopback Security & Native Webview Isolation

A loopback address alone is not sufficient authorization. Nexus Studio enforces:
- **Per-launch credential and session**: The native shell authenticates its backend and holds an HttpOnly session; nothing is placed in URLs or browser storage (see *Authenticated native startup*).
- **Strict Host Header Enforcement**: Requests must target `127.0.0.1:{port}` or `localhost:{port}`; external domains and internal network addresses are rejected.
- **Origin Validation**: CORS/Origin headers are restricted to `tauri://localhost`, `http://tauri.localhost`, and `http://127.0.0.1:{port}`.
- **External URL Allowlisting**: External links (e.g. documentation, OAuth provider sign-in) open in the user's default browser only when matching `https://` or `mailto:` schemes. Internal network ranges and `file://` / `javascript:` URLs are strictly blocked.

### 3. Platform User Directories

User data, configurations, caches, and backups are stored in standard platform directories outside the application binary and workspace:
- **macOS**: `~/Library/Application Support/NexusStudio/`
- **Linux**: `~/.local/share/nexus-studio/` (respecting `XDG_DATA_HOME`)
- **Windows**: `%LOCALAPPDATA%/NexusStudio/data/`

### 4. Workspace Import and Automated Backup

Existing Studio workspaces can be imported into the desktop data directory via `import_workspace`:
- Automatically generates a timestamped `.zip` backup archive in the platform backups directory before modifying existing target directories.
- Verifies writer exclusivity; aborts if the destination is actively locked by a running instance.

### 5. System Prerequisites Audit

The preflight audit inspects local dependencies:
- **Python**: 3.10+ execution runtime (required).
- **Docker**: Container daemon for Python analysis workers, ImageMagick, and YOLO object detection (optional; required only for isolated container workers).
- **Node.js**: Runtime for local MCP server adapters (optional).
- **Git**: Version control CLI (optional).

## CLI Operations

The `nexus desktop` subcommand exposes desktop lifecycle commands:

```bash
# Check system prerequisites
nexus desktop preflight [--json]

# Inspect workspace instance lock status
nexus desktop status [--dir WORKSPACE] [--json]

# Launch desktop backend and local webview/browser
nexus desktop launch [--dir WORKSPACE] [--port PORT] [--no-browser]

# Stop running backend for a workspace
nexus desktop stop [--dir WORKSPACE]

# Import an existing workspace with automated backup
nexus desktop import --from SOURCE [--dir TARGET] [--no-backup]
```

## Tauri 2 Shell Package

The desktop shell is located in `apps/studio-desktop/`:
- `src-tauri/tauri.conf.json`: Tauri 2 bundle configuration, window sizing, CSP, and sidecar binary definitions.
- `src-tauri/Cargo.toml`: Rust dependencies (`tauri`, `tauri-plugin-single-instance`, `tauri-plugin-shell`, `tauri-plugin-process`).
- `src-tauri/src/main.rs`: Rust entrypoint implementing single-instance focus, backend sidecar spawning, and clean child termination on quit.
- `build_desktop.py`: Python packaging script that verifies static frontend assets and stages the executable sidecar runner in `src-tauri/binaries/nexus-sidecar`.

## Parent pipe lifetime and abnormal shell exit

The native shell must retain its stdin writer after sending the launch secret.
The backend monitors the same channel without holding Python buffered-input
locks. Parent EOF or unexpected extra bytes trigger the existing server/service
shutdown path; credentials are never echoed. Browser/team launches without the
native flag are unchanged. A script using `--desktop-launch-stdin` must keep its
writer open until it wants the backend to stop.

The owned-process example uses only a disposable workspace and no live jobs:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_desktop_parent.py
```

See [parent lifetime acceptance](STUDIO-DESKTOP-PARENT-CHECKPOINT.json). This
closes the demonstrated idle-backend orphan case; it does not certify every
active external process or all installed webview platforms.

## Portable backend prototype

A real frozen core/PDF backend is now available through the native packaging
command; development wrappers are explicit opt-ins. See [bundle instructions and
limits](STUDIO-DESKTOP-BUNDLE.md). The relocated macOS executable is not yet an
SDK-complete or installed multi-OS release. Native application installation,
active descendant cleanup, signatures and release updates remain open.

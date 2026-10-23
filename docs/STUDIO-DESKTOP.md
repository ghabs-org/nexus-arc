# Nexus Studio Standalone Desktop Distribution

Milestone 9 remains an incomplete prototype and stays last in the delivery order.
The files describe a proposed Tauri 2 shell and Python backend. The current shell
does not start its backend, and its staged launcher uses build-machine Python.
It is not a standalone distribution. Helper tests do not establish native startup,
session security or clean installation on macOS, Linux or Windows.

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
|  - Ephemeral bootstrap token handshake                |
|  - Full agent, workflow, catalog, and worker runtime  |
+-------------------------------------------------------+
```

## Core Subsystems

### 1. Workspace Instance Locking

Every workspace directory enforces a single-writer lock via `.nexus-workspace.lock`:
- Records the owning process ID (`pid`), listening loopback port (`port`), SHA-256 hash of the bootstrap token, and acquisition timestamp.
- Detects stale locks: if the recorded PID is dead or nonexistent, the lock is safely broken and reacquired.
- Rejects concurrent launches against the same workspace; secondary desktop invocations focus the existing running window rather than spawning duplicate workers.

### 2. Loopback Security & Native Webview Isolation

A loopback address alone is not sufficient authorization. Nexus Studio enforces:
- **Ephemeral Bootstrap Tokens**: Generated per launch (`secrets.token_urlsafe(32)`) and never stored in URLs or browser storage.
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

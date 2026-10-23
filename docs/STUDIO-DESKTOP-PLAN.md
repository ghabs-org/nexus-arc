# Nexus Studio standalone desktop delivery plan

Status: planned; no desktop package is implemented or accepted by this document.
Requested: 21 October 2026.
Placement: reusable framework distribution and desktop launcher.

This is milestone 9, a user-approved addition to the
[current implementation plan](STUDIO-IMPLEMENTATION-PLAN.md). It preserves
the frozen eight-milestone reference inventory and its acceptance counts. Desktop
completion is a separate delivery gate covering macOS, Linux and Windows.

## Product goal

Install and launch Nexus Studio like a normal desktop application. Include the
existing UI, Python server and core runtime, without requiring users to clone
the repository, install Python or start a terminal. Keep the browser/HTTPS VM
edition available from the same framework and frontend sources.

Expose the full supported feature set: agents, Quick Build, workflows, knowledge,
connectors, subscriptions, tools, voice, artifacts, sharing and operational views.
Desktop packaging must not create a reduced fork. Features requiring accounts,
external services, model files, provider CLIs or containers remain conditional on
explicit setup; list those requirements before enabling the feature. “Standalone”
means an independently installed application, not universal offline operation or
a package containing every external dependency.

## Proposed architecture and first validation gate

Start with a Tauri 2 desktop shell and a separately packaged Nexus Python process
(sidecar), provisionally built using PyInstaller. Reuse the existing static UI;
do not migrate it to a different frontend framework. Validate this choice with a
small packaged prototype before committing to a complete release pipeline.

Tauri supports bundled external binaries, and uses platform webviews. PyInstaller
packages Python applications but requires separate builds on each target OS.
References: [Tauri sidecars](https://v2.tauri.app/develop/sidecar/),
[webview engines](https://v2.tauri.app/reference/webview-versions/),
[distribution](https://v2.tauri.app/distribute/), and
[PyInstaller operating model](https://pyinstaller.org/en/stable/operating-mode.html).

Prove packaged imports/resources, process spawning, streaming, microphone access,
PDF/artifact previews, Mermaid and subscription CLI execution on all three OSes.
Record supported OS versions, architectures and Linux runtime dependencies before
publishing installers. Do not claim Linux portability from one distribution's
successful build. Start with macOS arm64/x86_64 and Windows/Linux x86_64 targets;
additional architectures require separate acceptance.

## UI and server lifecycle

1. Launch the desktop shell and acquire a per-workspace instance lock.
2. Start its bundled server as an owned child process using an available
   loopback-only port, not a hard-coded developer port.
3. Complete an authenticated startup/readiness handshake, then open that server's
   existing UI inside the desktop window. UI and API use the same local origin.
4. Keep all existing server-side permissions, jobs and provider adapters. The
   window displays results; the server performs agent work and stores state.
5. Present startup failures, logs and safe recovery actions in the app. A second
   launch focuses the existing instance; it must not start duplicate workers.
6. On quit, handle active work through existing cancellation/checkpoint semantics
   and stop only owned processes. Never terminate an independently running VM or
   development server. Restart recovery must not replay uncertain external effects.

Closing the window defaults to an explicit quit/keep-running choice while work is
active. Optional tray/background mode and login startup are opt-in. Clearly show
that local schedules and integrations cannot execute while the runtime is stopped
or the computer is asleep; use existing missed-run recovery on resume. Continuous
operation remains available through the personal VM deployment.

## Local security and remote mode

A loopback address alone is not authorization. Use an ephemeral per-launch
bootstrap credential, an authenticated desktop session, strict Host/Origin and
CSRF checks, and no permissive cross-origin access. Do not expose credentials in
URLs, logs or browser storage. Keep binding local unless the user explicitly
configures the existing server deployment mode.

Give the desktop bridge only narrowly required native permissions. Generated HTML,
artifact frames, untrusted links and remote content receive no native privileges.
Open external authentication/document links in the system browser through an
explicit allowlisted flow; validate callback state and destinations.

Offer optional connection to an existing HTTPS Nexus server using existing Nexus
authentication. Show which server owns data and executes jobs. Never silently
copy local data, credentials or subscriptions to it. Remote pages receive no
privileged desktop bridge; if a safe isolated embedded view is unavailable, open
the existing server UI in the system browser. Local/remote switching must not
resubmit active work or imply automatic synchronization.

## Data, dependencies and native integration

- Store workspaces, artifacts, indexes, configuration and logs in platform user
  data directories outside the installed executable. Reuse existing schema
  upgrades, backups and credential services. Use native secret storage where
  appropriate without replacing Nexus authorization or exporting secret values.
- Offer explicit import of an existing local Studio workspace with backup and
  exclusive-writer checks. Never silently attach to a live workspace or overwrite
  existing state. Uninstall preserves user data unless removal is explicitly chosen.
- Detect provider executables and credentials despite desktop applications having
  a different environment from a terminal. Provide file selection/setup guidance,
  existing login flows, readiness and billing labels. Validate CLI provider
  availability separately per OS, including any WSL-only requirements. Unsupported
  provider/platform combinations must say so rather than fall back to API billing.
- Detect Docker and require it for generated code, ImageMagick and object detection.
  Offer reviewed setup instructions; never substitute unrestricted host execution.
  Support paths, mounts and permissions on Windows, macOS and Linux.
- Keep large local speech/embedding models and optional integration dependencies
  explicit, versioned and checksum-verified. Display download sizes, licenses and
  runtime readiness. No automatic document upload or model download on first launch.
- Support native file open/save, drag/drop attachments, microphone permission,
  clipboard, download locations and notifications through restricted interfaces.
  Preserve attachment-as-file behavior and accessible responsive UI.
- Document which callbacks/webhooks/public snapshots need a reachable HTTPS VM;
  a laptop-only loopback server does not make inbound integrations publicly reachable.

## Packaging, upgrades and delivery order

1. After milestones 1–8 implementation and local acceptance work, prototype the
   shell, bundled Python startup, authenticated loopback session and real streaming
   on each OS. Milestone 9 stays last; do not start desktop implementation early.
2. Complete feature prerequisites, workspace import, native permissions,
   crash/shutdown/sleep recovery and local/remote separation. Wire an example
   consumer following repository policy.
3. Produce per-OS builds in CI: macOS app/DMG, Windows installer, Linux AppImage
   and an initial Debian package. Validate installed, nondeveloper environments;
   do not rely on the build machine's Python, toolchain or source checkout.
4. Add checksums, dependency/license notices and supply-chain records. Complete
   macOS signing/notarization and Windows signing when signing identities exist;
   otherwise label personal unsigned builds and their installation requirements.
   Never describe unsigned artifacts as signed releases.
5. Provide user-initiated, signature-verified updates with release notes. Back up
   before schema changes and retain a recoverable previous application version.
   Data rollback requires a compatible backup, not merely an older executable.
6. Complete platform acceptance after milestones 7 and 8 and the cross-cutting
   audit. Retain personal-VM/browser acceptance as a separate supported deployment.

## Acceptance and completion gate

Track desktop requirements separately as D09 records when implementation begins;
do not change the existing 135 frozen acceptance records to inflate coverage.
For every advertised OS/architecture, retain evidence for:

- Clean install, launch without Python/source checkout, second launch, port
  collision handling, readiness failure, clean quit and owned-process cleanup.
- Existing agents, Quick Build lifecycle, workflows, knowledge/citations,
  artifacts, schedules, feedback, sharing and connectors through the packaged UI.
- Streaming/reconnect, approvals, restart/crash/sleep recovery, no duplicate
  actions, backup/restore, upgrade and workspace locking.
- Configured subscription/API providers, provider readiness and billing disclosure;
  Docker workloads, speech and optional integrations with explicit prerequisites.
- File paths including spaces/non-ASCII, microphone permissions, downloads,
  clipboard, external login callbacks, accessibility and platform webview behavior.
- Local-session isolation, hostile origins, untrusted artifact/native-bridge
  separation, local/remote account boundaries and redacted diagnostics.
- Installer/signature/update checks and a tested recovery procedure on each OS.

Missing accounts, signing identities, devices or machines remain named pending
checks. A working macOS prototype does not complete Windows or Linux. All
supported feature paths must be packaged and locally accepted; account-dependent
live validation remains separately reported. Desktop delivery remains **planned**.

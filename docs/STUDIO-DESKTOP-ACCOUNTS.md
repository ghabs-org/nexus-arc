# Native desktop accounts

Documentation checked against source on **10 November 2026**. Native Nexus identity
is optional; local Studio remains usable without Nexus sign-in. Version 1.0.3
introduced the native account flow. The 7 November installation record verifies
provider sign-in actions and status display in the local **1.0.4 app**; it is not
a claim about the version installed today. No 1.0.4 DMG was created.
Version **1.0.5** has a signed/notarized DMG including the later Antigravity status
fix. Source is now **1.0.6**, with subsequent work tracked separately as Unreleased.
See [release history](STUDIO-RELEASE-NOTES.md),
[1.0.4 installation evidence](STUDIO-DESKTOP-1.0.4-LOCAL-INSTALL.json) and
[1.0.5 evidence](STUDIO-DESKTOP-1.0.5-CHECKPOINT.json).

Hosted native sign-in still requires compatible desktop endpoints and registered
provider callbacks. Historical endpoint-deployment notes below retain their
recorded scope; this documentation audit does not verify a current VM deployment
or live identity-provider login.

## User flow

The native **Account** menu offers **Sign in to Nexus**, **Nexus Account**,
**Sign out of Nexus**, **Cancel Sign-in**, and local subscription setup for Codex,
Claude Code and Antigravity. The account dialog shows the selected server, account,
existing project names and remaining session lifetime. This connection identifies
the user; it does not synchronize files or enable remote job execution.

The shell opens the system browser at the existing Nexus authentication service.
Configured GitHub/GitLab and linked social identity providers use their existing
OAuth flows, encrypted credentials and project authorization. The user confirms
the device explicitly in the browser. Provider callbacks remain registered HTTPS
Nexus addresses; GitHub never redirects directly to each Mac or an arbitrary port.

After approval, Nexus sends a one-minute single-use code and state to the shell's
temporary IPv4 loopback listener. The shell exchanges the code using its PKCE S256
verifier, validates the device identity, then saves the opaque Nexus credential in
macOS Keychain. Native credentials never enter the local webview, URL query strings,
frontend storage, scripts or workspace exports. Provider tokens remain server-side.

Nexus stores only the device credential hash, with the existing user identity,
device label, expiry and revocation flag. Device credentials permit identity and
current project-name inspection through the desktop session endpoint, not arbitrary
Studio/team API access. Project grants remain governed by the existing Nexus ACLs.
Sessions last 30 days and require sign-in again after expiry; there is no silent
refresh or automatic credential transfer between servers.

Signing out confirms revocation on the server before removing the Keychain entry.
If the server is unavailable, the entry remains available for a retry. Signing out
does not sign out provider CLIs. If a replacement sign-in cannot revoke the previous
device credential, the dialog reports that the old session remains until expiry.

## Configuration and deployment

The account server defaults to `https://arc.ghabs.me`. A launcher can set
`NEXUS_DESKTOP_ACCOUNT_SERVER` to another HTTPS origin without a path, credentials,
query or fragment. Keychain entries are separated by normalized server origin.
The native client validates TLS and never follows HTTP redirects for API requests.
No OAuth client secret is bundled in the desktop application.

The hosted service must install the new routes using
`nexus.core.auth.desktop_routes.install_desktop_login_routes`. The existing
`examples/nexus-bot/src/webhook_server.py` consumes this adapter with its current
Nexus cookie and page renderer. Requests under `/auth/desktop/*` must reach this
service. Each configured provider callback must be registered with that provider
and served on the account origin. The GitHub Studio callback remains
`https://arc.ghabs.me/auth/github/callback`. Live acceptance requires that exact
address to be registered in the GitHub OAuth app settings.

Two additive tables, `nexus_desktop_login_attempts` and `nexus_desktop_devices`, are
created idempotently in the existing auth database. No existing sessions,
credentials or ACLs are rewritten. Include these tables in normal auth database
backups. Older services ignore them. Do not drop them during rollback; disable the
new routes and preserve device expiry/revocation state.

## Local subscriptions

The installed 1.0.4 app exposes **Sign in** in **Providers & models** and its provider
configuration dialog for Codex, Claude Code and Antigravity on macOS. These fixes
were packaged in the 1.0.4 local app. The older 1.0.3 DMG continues to
offer subscription setup through the native **Account** menu only.

The new action asks for native confirmation, then reuses the Account menu's local
Terminal/browser flow. Only fixed provider IDs from the currently verified backend
origin are accepted. Browser-only Studio, shared team sessions and unsupported
desktop platforms do not show this local action. Returning to Studio does not
resubmit a failed message; the user refreshes installation status and retries.

Provider inventory, sign-in guidance and Codex/Claude Code/Antigravity execution
now share executable discovery. Finder's short PATH previously let Providers show
**CLI installed** for a Homebrew/local installation while chat reported that the
provider was not ready. Availability now tests the discovered executable and keeps
that exact path for execution. An explicit missing path never falls back to a
different installation. This check runs `--version`, not a model or login request.

Failed local CLI authentication now shows a sign-in action in the conversation.
It explains that `/login` belongs in the provider's terminal rather than Studio
chat. Identical output/error text is rendered once. Native account messages and
subscription confirmations are attached to the main window so they remain visible.

Providers now checks local Claude/Codex sign-in when opened, when returning to the
visible page after login, or when **Refresh status** is selected. **Signed in** is
separate from **CLI installed**. Connected providers offer **Reconnect**. Missing
authentication shows **Sign-in needed**; timeouts, unsupported output and checker
failures show **Couldn’t verify sign-in** rather than retaining a green badge.
Subscription login and API credentials have distinct labels, including API billing.

Added after the 1.0.4 installation and packaged in 1.0.5: Antigravity joins these checks using the
documented [`agy models`](https://antigravity.google/docs/cli/reference/) command,
also used by the existing Nexus account setup. Both consumers now share the pure
status checker; server-side checks retain their isolated account environment and
30-second limit, while the local check uses four seconds. A zero exit must include
a recognized tab-separated model catalog. Empty output, help, transport errors
and timeouts do not establish sign-in. Explicit authentication prompts report
Sign-in needed. Successful catalog access shows Signed in/Reconnect, with billing
managed by the CLI; it does not prove a paid subscription or remaining quota. The
1.0.4 artifact does not include this later change; the 1.0.5 DMG does.

The checks use the official [`claude auth status --json`](https://code.claude.com/docs/en/cli-reference)
and [`codex login status`](https://learn.chatgpt.com/docs/developer-commands) commands,
with a four-second timeout per CLI and two concurrent checks. Results are local
CLI reports, not proof of remaining quota or live model authorization. Their raw
output, email, organization, tokens and credential paths never enter the new
authentication metadata. The new
`POST /api/providers/status` operation requires the existing Studio token/session
and origin checks. Shared workspaces reject host CLI status probes and keep their
existing user-scoped Nexus setup. Cheap bootstrap inventory remains unchanged.

The Claude/Codex status display was verified in the local 1.0.4 app. The later
1.0.5 DMG includes those checks and the Antigravity addition; no new installer is
built by this documentation update.

**Connect Codex/Claude Code/Antigravity** reads framework-owned setup guidance from
the sidecar, checks the local CLI and opens its sign-in in Terminal. Missing CLIs
produce installation guidance; Studio does not install software, initiate a model
run or fall back to API billing. Antigravity uses its interactive sign-in menu.

The provider CLI manages its own credential storage. This is deliberately separate
from the hosted Nexus credential database and does not copy VM subscriptions onto
the Mac. A CLI being installed or a terminal opening does not establish successful
authentication. Verify a real account and invocation separately before release.

## Validation and remaining acceptance

The installed, signed/notarized 1.0.4 app reopened the existing workspace and
displayed **Signed in**, **Subscription login**, and **Reconnect** for the current
Claude and Codex accounts. Its frozen status endpoint also passed a synthetic
account check with private fields filtered out. The hardened runtime passed all
44 SDK checks and five runtime/cleanup checks. The prior app copies and verified
workspace snapshot remain in the private local update backup. No real login or
model request was started during installation.

The sign-in status extension passed 54 focused Python checks and 11 frontend
checks without skips. The source UI showed **Signed in** and **Subscription
login** for the installed Claude and Codex accounts, then refreshed both check
timestamps through **Refresh status**. The example consumer reported the same
sanitized results. No login, model request, VM change or DMG build was performed
for this status check.

The post-1.0.3 fix passed 36 focused Python checks, seven frontend checks and 24
native checks without skips. An isolated macOS development app showed the provider
links and native confirmation; Cancel returned to Providers without starting login.
The real installed Claude executable passed `--version` with PATH restricted to
`/usr/bin:/bin`, using the same discovered path as setup. No real account login or
paid model request was performed. A broader adapter run passed 103 checks and
separately skipped 15 optional HTTP checks because `aiohttp` is absent from the
repository test environment. No DMG was rebuilt or installed for these source fixes.

The implementation has real database/Flask route checks, native callback/transport
tests, a Postman simulation and an opt-in synthetic Keychain round trip. The native
client rejects wrong state, untrusted destinations, malformed responses and replay;
code exchange is never retried automatically. A macOS inherited-nonblocking socket
regression test covers callbacks whose request bytes arrive after accept.

Reproduce network acceptance:

```sh
cargo build --manifest-path apps/studio-desktop/src-tauri/Cargo.toml --example accounts_probe --offline
postman simulation run postman/simulations/nexus-desktop-login.sim.yaml --port 49742 --no-history --output ndjson
NEXUS_ACCOUNT_SIMULATION_PORT=49742 .venv/bin/python -m pytest -o addopts='' tests/studio_desktop_accounts_acceptance.py -q
```

The probe's HTTP loopback server is a test fixture. The application menu accepts
only HTTPS account origins. The Keychain probe uses a dedicated synthetic
`.invalid` origin and removes its new entry after the test.

Native startup, the guide, Account-menu presence and clean close were observed in
the signed app. Dialog actions were not confirmed through menu automation. Manual
click-through, browser approval with a deployed identity provider, signed-app
Keychain continuity across updates, real CLI subscription sign-in and another
physical Mac remain pending.
Windows/Linux OS credential stores and guided terminal launch are explicitly
unsupported by this first native account implementation; local Studio continues
to work there. The user explicitly requested the 1.0.3 DMG build. No VM changes or
public release were performed; future builds still require a new request.

# Native desktop accounts

The current source adds optional Nexus identity to the macOS native shell. Local
Studio remains usable without Nexus sign-in. The signed/notarized 1.0.3 DMG now
includes this implementation. The new hosted endpoints have not been deployed to
the personal VM. See [release evidence](STUDIO-DESKTOP-1.0.3-CHECKPOINT.json).

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

**Connect Codex/Claude Code/Antigravity** reads framework-owned setup guidance from
the sidecar, checks the local CLI and opens its sign-in in Terminal. Missing CLIs
produce installation guidance; Studio does not install software, initiate a model
run or fall back to API billing. Antigravity uses its interactive sign-in menu.

The provider CLI manages its own credential storage. This is deliberately separate
from the hosted Nexus credential database and does not copy VM subscriptions onto
the Mac. A CLI being installed or a terminal opening does not establish successful
authentication. Verify a real account and invocation separately before release.

## Validation and remaining acceptance

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

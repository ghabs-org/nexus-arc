# Direct browser sign-in

Implemented in source on 4 November 2026. Live deployment/provider acceptance is pending.

The Nexus account sign-in page now offers **GitHub, GitLab, LinkedIn, X and Meta** when
their existing server credentials are configured. The session-reference form remains
available in an expandable section. Browser users no longer need to start Git login
through Telegram or Discord.

## LLM subscription connections

Added on 5 November 2026: **Connect Codex**, **Connect Claude** and
**Connect Antigravity** on the sign-in page. These connect subscriptions to an
existing Nexus account; they do not replace Nexus identity or project permissions.

1. Select a subscription. If necessary, sign in to Nexus first. The selected
   provider and original workspace destination survive that handoff, including
   when the Nexus account has not configured its first AI provider yet.
2. Press **Connect** on the subscription page. This explicitly starts the existing
   provider account flow on the Studio server; viewing the page never starts it.
3. Codex shows a device sign-in URL and code. Claude and Antigravity expose their
   existing provider URL and authorization-code entry controls when requested by
   the CLI. Complete authentication on the provider's own site.
4. Once connected, **Continue to workspace** uses the normal Nexus readiness and
   permission checks. Account setup remains available for managing all providers.

The guarded `GET /auth/subscription?provider=codex|claude|agy` page uses only the
current Nexus cookie, never a supplied user/session query argument. Provider
connections reuse `/auth/provider-connect/start`, `/status` and `/submit-code`,
the existing credential store and isolated per-user runtime homes. Expired or
pre-OAuth sessions cannot poll login instructions or submit authorization codes.
Subscription login processes and Antigravity's account-status probe exclude inherited
server API keys and alternate cloud-auth overrides. They retain the existing private
per-user credential homes; this does not remove or modify the server's own settings.

The appropriate CLI must be installed on the **server running Studio**, using
the existing `CODEX_CLI_PATH`, `CLAUDE_CLI_PATH` or `AGY_CLI_PATH` overrides if
needed. `NEXUS_CLI_AUTH_MODE` must be `account` or `auto`. Discovery checks command
availability without executing commands or reading credentials. Missing software
or incompatible auth mode disables Connect with an explanation. No new provider
OAuth client registration, database migration or API key is required for these
subscription connections. Provider eligibility and plan limits still apply;
API-only features retain their existing separate configuration and billing.

The implementation reuses the installed Codex CLI's `login --device-auth` support
and existing Claude/Antigravity adapters. The inspected [official Codex authentication
documentation](https://developers.openai.com/codex/auth/) and
[Claude authentication documentation](https://code.claude.com/docs/en/authentication)
support the account-login approach. Antigravity's documentation was unavailable
during this check; its existing adapter was retained rather than inventing a new
OAuth protocol.

Local browser acceptance used synthetic accounts: three explicit connection starts,
two code submissions, connected-state persistence and zero real provider calls.
It covered desktop and mobile layouts, a Codex device code, both code-entry flows,
keyboard submission, cleared code fields and continuation. Real provider account
acceptance remains pending. See the [subscription checkpoint](STUDIO-SUBSCRIPTION-CONNECTIONS-CHECKPOINT.json).

## Identity and permissions

- GitHub/GitLab use the existing credential store, organization/group checks and
  project access synchronization. Browser login resolves existing accounts by the
  provider's immutable numeric ID, rather than a possibly reassigned username.
- Social sign-in only opens an existing Git-backed Nexus account. It cannot create
  project grants. First sign in with GitHub/GitLab, open Account setup, and connect
  the desired social account there.
- Existing LinkedIn/X/Meta connections need one reconnect after this upgrade. Earlier
  publishing credentials did not store a suitable app-scoped login identity. No
  automatic matching by email, display name, username or administered Meta Page occurs.
- Social login requests identity scopes and leaves existing publishing credentials
  unchanged. A new account connection still uses the existing connector permissions.
- An incomplete account stays on Account setup. Ready users return to their requested
  local destination; the saved-setup page also provides **Open workspace**.

## Server configuration

Continue using the existing Nexus authentication database and credential master key.
No new authentication service or signing secret is introduced.

| Provider | Existing server configuration |
| --- | --- |
| GitHub | `NEXUS_GITHUB_CLIENT_ID`, `NEXUS_GITHUB_CLIENT_SECRET` |
| GitLab | `NEXUS_GITLAB_CLIENT_ID`, `NEXUS_GITLAB_CLIENT_SECRET`; existing GitLab base URL |
| LinkedIn | `NEXUS_LINKEDIN_CLIENT_ID`, `NEXUS_LINKEDIN_CLIENT_SECRET` |
| X | `NEXUS_X_CLIENT_ID`, `NEXUS_X_CLIENT_SECRET` |
| Meta | `NEXUS_META_APP_ID`, `NEXUS_META_APP_SECRET` |

Meta discovery now uses the same `APP_ID`/`APP_SECRET` names as its existing OAuth
implementation. Instagram continues to use the existing Meta connection; it is not
advertised as a separate identity provider.

Meta OAuth URLs now use `NEXUS_META_API_VERSION`, defaulting to `v26.0`, matching
the [official Meta Python SDK configuration](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/apiconfig.py)
inspected on 4 November 2026. The previous hard-coded `v19.0` is no longer used by
these OAuth functions. Validate your app's approved Graph version before deployment;
this change does not upgrade unrelated publishing adapters.

`NEXUS_PUBLIC_BASE_URL` and any `NEXUS_<PROVIDER>_CALLBACK_URL` overrides must point to
the **same public origin as this sign-in page**. Register the existing
`/auth/<provider>/callback` URLs in each provider app. Browser context cookies cannot
cross different hostnames. Route `/login` and `/auth/*` to the existing Nexus auth
server; the new `POST /auth/browser/start` needs no additional proxy prefix.
Provider app approval and allowed scopes remain provider requirements.

## Callback and storage boundaries

Browser-created attempts use a short-lived encrypted HttpOnly, SameSite=Lax context
cookie scoped to `/auth`, marked Secure for HTTPS. Callback validation checks the
initiating browser, provider, one-time database state, session and expiry before
exchanging an authorization code. Context cookies clear on completion or error.
Origin/Fetch Metadata checks reject cross-origin initiation when those headers exist.
Return destinations accept only local paths, including after percent decoding.

Account connections from browser-created sessions use a fresh, browser-bound
`web-connect` attempt. They do not recycle or reactivate the original login state.
Bot-created onboarding callbacks retain their existing behavior. Browser login sends
no chat notifications.

The additive `nexus_login_identities` table stores `(provider, client_id, subject)`
with its canonical Nexus user ID. A composite primary key prevents duplicate bindings;
an existing binding cannot be reassigned to another account. Provider secrets and
tokens remain in existing encrypted credential storage.

Startup creates the table through the existing schema initialization. Existing
credential/session tables are unchanged. Repeated initialization is safe. An older
release can ignore this table on rollback; retain it and include it in account DB
backups. No destructive migration or bulk identity backfill is required.

## Validation

Automated checks cover browser/provider mismatch, state expiry and replay, encrypted
context tampering, cross-origin initiation, safe redirects, secure cookies, account
readiness, separate connection attempts, stable Git identity reuse and additive schema
preservation. Existing session-reference and bot onboarding checks remain included.
The focused suite passed **147 checks with zero failures or skips**. The
[acceptance checkpoint](STUDIO-BROWSER-LOGIN-CHECKPOINT.json) records source hashes
and the separate HTTP/browser evidence boundaries.

The [Postman contract](../postman/contracts/nexus-browser-login.yaml) describes the
routes. The [HTTP simulation](../postman/simulations/nexus-browser-identity.sim.yaml)
and [explicit acceptance command](../tests/studio_browser_identity_http_check.py)
exercise LinkedIn/X/Meta clients through real local HTTP requests: linked/unlinked
subjects, absent identity fields and provider denial. Synthetic checks do not establish
provider app approval or real account login.

Local browser inspection covered 1280px desktop and 390px phone widths, aligned
buttons with at least 44px touch height, no horizontal overflow, and keyboard expansion
of the session-reference fallback. No real account, provider billing, VM deployment
or desktop package build was used.

Postman CLI 1.75.0 validates the OpenAPI syntax without remote report events. Its
`status` command is unavailable; workspace publishing/discovery was not used. Public
Orbit lookup returned unrelated results, so those candidates were not integrated.
Provider behavior reuses existing Nexus clients; live acceptance remains pending.

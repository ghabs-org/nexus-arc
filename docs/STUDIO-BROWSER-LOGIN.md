# Direct browser sign-in

Implemented in source on 4 November 2026. Live deployment/provider acceptance is pending.

The Nexus account sign-in page now offers **GitHub, GitLab, LinkedIn, X and Meta** when
their existing server credentials are configured. The session-reference form remains
available in an expandable section. Browser users no longer need to start Git login
through Telegram or Discord.

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

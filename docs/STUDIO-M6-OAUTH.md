# Delegated MCP accounts

Studio supports explicit, per-user OAuth authorization-code consent for remote
MCP over Streamable HTTP and legacy SSE. Existing stdio/environment-reference,
Basic, Bearer and API-key connectors remain available. This adds connector
accounts; Nexus remains the identity and project-authorization authority.

## Configure and connect

1. Install the existing Nexus credential encryption dependencies (`nexus-arc[studio-oauth]`)
   and configure `NEXUS_CREDENTIALS_MASTER_KEY` through the server's secret manager.
   Use the same protected key on controllers sharing state. Do not paste it into
   Studio or save it in exported configuration. The existing key-version setting
   remains available; changing the key without re-encrypting grants requires consent
   again.
2. Register an OAuth client with the selected authorization server. Register the
   exact callback shown in the connector editor:
   `<trusted Studio origin>/api/studio/oauth/callback`.
3. Choose a remote MCP transport and **Sign in with provider (OAuth)**. Enter the
   exact issuer, registered client ID and explicitly requested scopes. Optionally
   select a resource URI, a server-managed client-secret environment reference,
   and additional allowed endpoint origins when the issuer hosts token or consent
   endpoints on another origin. Save the connector, then reopen it.
4. Select **Sign in**, follow **Continue provider sign-in**, finish the provider's
   consent, and select **Refresh status** in Studio. Connecting does not enable
   the connector, add tools, approve mutations or activate a build. Select tools,
   permissions and activation explicitly using the existing controls.
5. **Disconnect my account** removes that user's local tokens and invalidates
   their pending consent requests. It does not revoke another user's grant,
   contact the provider's revocation endpoint, cancel an in-flight external effect,
   or claim that an already completed tool action was undone. Revoke the upstream
   app grant at the provider if that is also required.

Example configuration (public metadata and secret references only):

```yaml
type: mcp
transport: streamable-http
url: https://mcp.example.net/mcp
auth:
  type: oauth
  issuer: https://accounts.example.net
  client_id: registered-public-client
  scopes: [documents.read]
  resource: https://mcp.example.net/mcp
  endpoint_origins: []
enabled: false
actions: [discover]
tools: []
```

## Security and recovery contract

Discovery verifies RFC 9728 protected-resource metadata and an explicitly selected
issuer using OAuth authorization-server or OIDC discovery. S256 PKCE is mandatory.
Protected-resource metadata is fetched from the selected MCP endpoint, and a
configured resource must share its origin. Resource audience is sent on
authorization, code exchange and refresh. Authorization
response `iss`, when present, is compared exactly; it is required when the selected
server advertises support. Provider error callbacks undergo the same issuer,
state, browser-cookie and Nexus-session checks before processing.

Consent uses a ten-minute, HttpOnly, SameSite=Lax browser cookie (Secure on HTTPS).
State and verifier are tied to the Nexus session, user, workspace, saved connector
and reviewed account configuration. ID tokens never establish Nexus identity and
are never used as access tokens. Token responses must be Bearer, bounded and use
the reviewed scope set. Network requests disable redirects and environment proxies,
use destination restrictions/timeouts and cap response bodies at 64 KiB. OAuth
URLs require HTTPS; explicit private allowance permits loopback HTTP for local
fixtures and local authorization servers. Non-loopback plain HTTP is rejected.

Private records use the existing AES-GCM credential crypto, with the user/workspace/
connector scope authenticated inside the encrypted payload, on existing Studio
file/SQL state. Account and pending-request payloads never appear in resource
exports, histories, status responses or workspace snapshots. Snapshot creation
excludes both private collections for file and SQL backends; restoration also
ignores them. Configurations retain secret references and require new consent after
restore. The master key remains independently server-managed.

Before a tool starts, expired tokens refresh through a durable claim. Existing SQL
workspace transactions commit the claim before token I/O, so separate controllers
cannot reuse a rotating refresh token concurrently. Concurrent callers wait at most
12 seconds for an active refresh. Public clients require refresh-token rotation;
confidential clients may keep their previous refresh token if omitted. A failed or
interrupted exchange/refresh requires consent again; execution never starts consent,
automatically replays a tool after a 401, switches providers, or retries a possibly
completed mutation. Changed tool/enable permissions during refresh reject the old
request; the refreshed account remains available for a later reviewed request.
File mode uses the existing single-controller lock; multiple controllers require
SQL state. Revoked Nexus project access blocks token use and refresh.

## Scope and migration

All changes are additive, with version-1 private records; no existing conversation,
workflow, schedule or connector format is rewritten. Auth package exports now load
the bot account manager lazily, so local Studio and credential crypto do not require
bot project configuration. Existing public auth imports retain their names.

This implementation uses pre-registered clients with `none` or
`client_secret_basic` token authentication. It does not install OAuth clients,
perform dynamic registration, add an identity provider, support signed client
assertions, or automatically elevate scopes from server challenges. Explicitly
configured origins are required for cross-origin authorization/token endpoints.
Endpoint URLs with embedded credentials, query strings or fragments are rejected.
These supported-client boundaries are visible configuration errors, not silent
fallbacks. Provider API charges remain distinct from coding subscriptions.

## Repeatable verification

- `tests/test_studio_mcp_oauth.py` uses independent local HTTP/JSON-RPC/SSE and
  stdio servers and the real installed MCP SDK. It covers consent, PKCE/audience,
  OAuth/OIDC discovery, strict issuer/browser/session checks, replay rejection,
  selected tools, all existing header auth modes on both HTTP transports, explicit
  denied-account recovery, encrypted scope separation, refresh concurrency,
  failures, restart claims, permission changes, disconnect fencing, private backup/
  restore, Nexus team isolation/revocation, and configuration-free local startup.
- `postman/contracts/studio-mcp-oauth.yaml` defines the Studio interface.
  `studio-oauth-token.yaml` defines the synthetic provider subset. The local
  `postman/simulations/studio-oauth.sim.yaml` exercises the real bounded token
  client through the example consumer, including rotation, JSON/HTML errors,
  forbidden redirects and response-size limits. Discovery uses the independent
  HTTP fixtures; this simulation does not certify an upstream provider.
- Run `postman simulation run postman/simulations/studio-oauth.sim.yaml --port 49647`
  locally, then run `PYTHONPATH=. python examples/nexus-bot/src/studio_mcp_oauth.py`
  and the same command with `--sql`. Each performs six real token requests using
  synthetic credentials and temporary state. Stop the owned simulation afterward.
- Browser checks use an isolated workspace at phone/tablet/desktop widths. The
  user's running workspace and real accounts are not modified.

The checkpoint records actual totals and limits. Live selected-provider consent,
refresh/expiry behavior, physical mobile devices and personal HTTPS VM operation
remain explicit live-validation work. Milestone 6 and the full plan remain open.

Protocol references: [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization),
[RFC 9728](https://www.rfc-editor.org/rfc/rfc9728.html),
[RFC 9207](https://www.rfc-editor.org/rfc/rfc9207.html).

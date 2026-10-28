# Recovered-host browser login acceptance

Status: prepared, not executed. Independent selected-service data/key recovery
already passed on the owner-approved Mac. A real browser/OAuth login needs a
separate HTTPS test hostname and a compatible registered provider callback.
The current live Studio address must continue serving its existing instance.

The owner supplied a candidate test hostname. Read-only preflight found no DNS
record. Existing Nginx/Tailscale ingress and certificate coverage are suitable; a
DNS-only CNAME to that ingress, distinct restored UI/auth routes and provider
callback review remain prerequisites. Exact addresses/configuration stay in the
private operator review. No DNS or ingress change has been applied.

## Required inputs

- The separate test HTTPS hostname and isolated target host. The Mac is the
  already approved recovery target; another host needs its own chosen scope.
- Valid TLS for that hostname, trusted normally by the browser. Do not bypass
  certificate warnings or copy a production TLS private key as a shortcut.
- A reviewed Nexus login configuration whose public origin and callback match
  the test hostname. Existing routes are `/login`, `/auth/start` and
  `/auth/github/callback` (or the selected supported provider callback).
- Provider callback registration compatible with the test origin. Review the
  existing provider setup before changing it; preserve the production client's
  settings. A separate test OAuth client may be required. Enter any credential
  only into its approved private configuration, never chat or Git.

## Isolate before starting applications

1. Verify and decrypt the private recovery ZIP locally using its separate
   recipient key. Both encrypted payloads are required. Verify file/database/image
   hashes against the recovered manifests. Keep the private material outside Git.
2. Restore into new databases, private directories and Docker resources. Resolve
   all recovered paths to those locations. No restored configuration may point
   to a live database, credential home, artifact backend or callback destination.
3. Convert the stopped recovered Studio namespace through the existing
   `create_snapshot`/`restore_snapshot` framework APIs with explicit quiescence.
   Use a new empty SQL namespace if retaining SQL. These APIs inactivate
   schedules/connections/public shares, interrupt unfinished runs, exclude
   executable queue restoration and retain uncertain external effects for review.
   Do not launch the raw restored SQL copy as an active service.
4. Recover only the credential key material needed by the test. Verify it first
   using the synthetic canary. No provider token or original login session needs
   to be displayed. Remove or revoke copied login sessions in the isolated auth
   database before allowing browser access; do not change production sessions.
5. Start only the reviewed Studio/auth/HTTPS test components. Keep the event
   consumers, schedulers, external callbacks, subscription workers and messaging
   services inactive. Begin without outbound access, then permit only the chosen
   identity-provider endpoints needed by the deliberate login test.

## Acceptance

1. Anonymous private routes return 401 without exposing records or redirecting
   private API calls. The public shell and normal login route remain reachable.
2. A person completes a fresh normal Nexus/provider login on the test origin.
   Browser credentials, consent and identity checks remain with that person.
3. Confirm the recovered owner/project permissions, agents and workflows are
   visible. Do not submit a provider task or activate a restored resource.
4. In Workspace tools → Deployment checks, select **Check private connection**.
   Workspace and Approval inbox must pass signed-in access, JSON response type
   and `Cache-Control: no-store`. The check reads no response bodies or cookies.
5. Verify sign-out removes access on the test origin and leaves the production
   Studio session and service unchanged. Record pass/fail/unavailable separately.
6. Stop the isolated application/auth components and remove only their scoped
   temporary resources. Retain the encrypted recovery package and redacted proof.

This closes recovered-host browser/login behavior only after actual execution.
It does not establish a full OS image, ingress automation, daemon/power-loss
recovery, physical-device acceptance or signed desktop installation. Those
remain separate gates. No DMG build is part of this procedure.

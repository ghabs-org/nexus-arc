# Operational overview

Open **Workspace & event mesh** to inspect **Quality & resource health**.
Refresh updates runtime events and operational summaries together.

The overview shows your helpful/needs-improvement totals, visible evaluation
reports and recent builds. Workspace owners can see workspace reports and builds;
feedback counts remain private to each account. The totals cover all visible
records. Report and build lists show the 20 most recent records.

Reports retain their execution status. A failed, interrupted or unknown run never
appears as a completed evaluation, even if its recorded cases passed. **View
report** opens the existing evaluation details. **Review build** opens the saved
Quick Build editor, including explicit connection checks and activation reviews.

Connection health comes from the existing saved, revision-aware checks. Old checks
remain stale when a build or resource changes. Opening the overview never contacts
a provider, checks a connection, executes an evaluation or activates a resource.
Unavailable definitions produce an explicit health-unavailable state.

Expand **Compare latest two releases** for added, removed and changed resource
identities. The comparison includes enabled-state changes. It excludes
configuration values, prompts, document contents and credentials. The active
release is identified separately; comparing releases does not roll them back.

No state migration or new configuration is needed. Existing records and API
consumers remain unchanged. The read-only API contract is
[studio-overview.md](../postman/contracts/studio-overview.md).

## Local verification

- Scoped HTTP tests cover anonymous/revoked access, account and owner visibility,
  private feedback, partial reports, visible release history and metadata-only
  comparison. Local lifecycle tests verify passive health inspection.
- Frontend tests cover escaping, existing detail controls, empty/unavailable
  states and late-response suppression.
- Disposable full-app fixture:
  `PYTHONPATH=. python tests/studio_overview_acceptance.py`.
  Sign in through `/acceptance` as the synthetic owner and open `/#workspace`.
  The fixture uses temporary files and no external accounts.
- Saved Postman assertions:
  `postman/contracts/studio-overview.assertions.js`, run against the fixture's
  `/api/studio/overview` with its synthetic owner cookie.
- Browser checks cover saved-build navigation and 390px/820px layouts.
  HTTPS deployment and physical-device acceptance remain separate.

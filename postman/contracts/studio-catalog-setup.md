# Passive integration setup contract

`GET /api/studio/integrations` retains its existing authenticated Nexus access,
`items`, `official_items`, `excluded` and existing row fields. Each of the 27
supported rows adds:

- `setup_metadata`: billing, requirements, display-only installation instructions,
  reference-only configuration template, frozen source/revision and optional
  `alias_of`. Four agreed vendor exclusions remain excluded.
- `readiness.state`: `dependencies-missing`, `configuration-present` or
  `setup-required`. Configuration presence is the existing row fact, not proof
  of an installed worker image, account credentials, authorization or availability.
- `readiness.checks`: installed Python distribution metadata and exact optional
  installation pins. `installed` never claims that an account/model works or that
  another installed version has passed acceptance.
- `readiness.live_verified`: always false for this passive inspection.

No credential resolution, environment-value reads, external requests, executable
launches, plugin factories, lifecycle hooks or installations run while listing.
Package checks read installed distribution metadata only. Templates retain secret
references and disabled connector defaults; selection placeholders require review
and normal validators before saving. SQL driver selection remains explicit.

Existing `POST /api/studio/integrations/{id}/configure` remains unchanged. Catalog
inspection grants no new execution permissions. Runtime authorization, connection
activation, selected-tool grants and approved mutation paths remain authoritative.
Missing optional software disables the catalog tool-run button. Setup instructions
remain readable without dependencies. Older servers without metadata still render.

Acceptance: passive inspection under forbidden process/network/credential hooks,
all supported identities and billing requirements, validator checks on filled
connector templates, missing/installed distribution scenarios, plugin-factory
isolation, authenticated HTTP access and escaped browser rendering.

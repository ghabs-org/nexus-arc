# Local activation prerequisite contract

Existing routes remain authenticated and owner-controlled:

- `GET /api/studio/builds/{id}/lifecycle` adds each component's
  `prerequisite_checks` and opaque `prerequisite_fingerprint`. This passive
  inspection never starts Docker workers or contacts a service.
- `POST /api/studio/builds/{id}/plan` with the current reviewed revision and
  `action: "activate"` checks local SDK metadata, explicit tool configuration and
  selected local worker software before returning a usable activation plan.
- `POST /api/studio/builds/{id}/transition` with `plan_id` and `digest` rechecks
  setup and existing credentials/authorization before any activation commit.

Missing SDKs, missing worker profiles/Docker/models, absent images, failed worker
software checks and insufficient chart process/file allowances add explicit
component errors. An errored plan cannot activate or enable any component.
Connector tool nodes include their actual connector dependency, so using a tool
ID does not bypass connector prerequisites.

Worker checks run fixed framework probe code through the existing pinned,
networkless, non-root, bounded sandbox. They inspect package/executable presence
and selected model readability. They do not execute workflow code, arguments,
registered tools, external account operations, installation hooks or model
inference. Probes produce no retained artifacts. One activation inspection caches
identical action/profile probes across components. Catalog browsing remains
passive. Live account entitlement, API quotas and model quality are not inferred.

Plans pin local package versions, worker configuration and selected model file
metadata. Setup drift rejects apply before probing changed workers; successful
preflight is repeated at apply to catch removed images/software. No raw worker
stderr, credential values or host paths enter the public prerequisite result.
Old pending activation plans without these fingerprints require fresh review.
Completed transition receipts replay without repeating probes or actions.

Existing request fields, release journals, rollback/deactivation and runtime
permission checks retain their behavior. This contract covers reviewed build
activation. Direct resource configuration and remaining account-specific readiness
continue under their existing validators/permissions and separate acceptance.

# Studio operational overview

`GET /api/studio/overview` uses the existing Studio session and project access.
It is read-only and requires no new permission. Anonymous and revoked sessions
remain rejected by the host. Opening the overview never probes connectors,
executes evaluations, changes releases or sends provider requests.

Response format: `nexus-studio-overview/v1`.

- `feedback`: current account's total, helpful and needs-improvement counts.
  Workspace owners still cannot see another account's feedback.
- `evaluations`: visible report count, completed report count, and up to 20 recent
  report summaries. `status` comes from the source run; partial results never
  count as completed acceptance. Summaries contain counts, not tasks or output.
- `builds`: visible build count and up to 20 recent build summaries. Each includes
  saved health counts from the lifecycle service, or an explicit unavailable
  status if definitions are missing. No connection check runs during this read.
- Each build's `comparison` compares its two newest visible releases. It lists
  added, removed and changed resource keys, never configuration values, prompts,
  documents, credentials or environment-variable values. Fewer than two releases
  returns `null`. `current_release_id` separately identifies the active release.
- `scope` identifies account visibility or workspace-owner visibility. Feedback
  always remains account-private. `recent_limit` is 20; totals cover all visible
  records, while detail lists explicitly contain only recent records.

The Workspace page links to existing feedback, report and Quick Build views.
Connection checks and lifecycle mutations retain their existing explicit flows.
Existing routes and response formats remain unchanged.

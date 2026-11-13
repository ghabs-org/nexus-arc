# Response feedback contract

Existing routes and response formats are unchanged:

- `GET /api/studio/feedback` returns `{items: [...]}` for the current account only.
  Studio also uses this route for a fresh JSON export at click time.
- `POST /api/studio/feedback` accepts `run_id`, rating `1` or `-1`, an optional
  comment of at most 2000 characters, and the previously returned `revision`.
  New records use revision zero (also the existing default when omitted).
  The response is the saved record with its incremented revision and status 201.
- `POST /api/studio/feedback/promote` accepts an owned `feedback_id`, evaluation
  name, target and expected phrase. Optional explicitly selected source evidence
  uses the existing artifact ID/revision representation. The response is the saved
  evaluation resource with status 201. This does not execute an evaluation.

Only completed, accessible runs can be rated. Each account has one feedback record
per run. A stale revision returns 400 without changing the saved comment/rating.
History reads remain owner-scoped even for workspace administrators. Revoked
project access denies subsequent reads and writes through existing Nexus sessions.
Promotion requires the existing builder permission and ownership of the feedback;
being an administrator does not bypass feedback ownership. Promoted cases copy
the original task and selected expected/reference data, not the private comment.

The UI now reads the current record before editing, pre-fills the comment/rating,
and sends that record's revision. History connects to existing run details, editing
and evaluation promotion. A saved promotion offers its existing evaluation editor
for review. Exports retain `nexus-response-feedback/v1` and contain only current
owner records from the server, rather than an earlier page snapshot.

## Disposable acceptance

```sh
PYTHONPATH=. python tests/studio_feedback_acceptance.py
```

Use the printed fixture origin and synthetic identities. After the browser flow
documented in `docs/STUDIO-FEEDBACK.md`, request the feedback route with Alice's
or Bob's synthetic `nexus_web_session` cookie from `tests/test_studio_team.py`.
Run `studio-feedback.assertions.js` to check account isolation and current edits.
Request `/api/catalog/evaluations` as Alice with
`studio-feedback-promotion.assertions.js` to check selected pinned passages and
absence of the private comment. Use `--no-report-events` to keep checks local.

These scripts deliberately expect synthetic fixture values. Never use them against
a personal workspace. No provider, external account or model is contacted.

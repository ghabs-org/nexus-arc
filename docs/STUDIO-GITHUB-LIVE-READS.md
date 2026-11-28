# GitHub live read acceptance

The native Studio GitHub REST connector has live read acceptance against
`ghabs-org/nexus-arc` using an existing authenticated account. This used a temporary
local Studio workspace, not the personal VM or an existing user's connector.

The consumer checks account authentication, repository identity, bounded issue/PR
listing and details when available. A strict transport guard allows only GET
requests to the selected repository and account identity endpoint. Failed reads
cannot become successful empty-list evidence. An unselected comment action is
rejected before transport, even with confirmation supplied. The consumer verifies
that its temporary workspace does not contain the token bytes.

Use an already configured credential environment reference; do not put a token
value in the command line or source. From the repository root:

```sh
PYTHONPATH=. python examples/nexus-bot/src/studio_github_readonly.py --repo ghabs-org/nexus-arc --token-env NEXUS_GITHUB_TOKEN
```

The consumer reads that environment reference in memory, stores only its name in
the temporary connector, and removes its workspace after cleanup. It never
obtains credentials automatically, modifies a live connector or sends messages.
Its report excludes account profile details, issue bodies, PR titles and secrets.
When no issue/PR is available, detail checks stay explicitly pending.

## Public contract check

A separate unauthenticated Postman request checks public repository identity and
response shape. It does not prove account access or Studio behavior:

```sh
postman request https://api.github.com/repos/ghabs-org/nexus-arc --header Accept:application/vnd.github+json --timeout 30000 --redirects-ignore --no-report-events --script-post-request @tests/fixtures/studio_github_public_contract.js
```

This request has four assertions and disables remote result reporting. The Python
adapter check is direct application execution, not a Playwright traffic capture;
no Postman application-capture or collection-matching claim is made.

[Retained acceptance](STUDIO-GITHUB-LIVE-READS-CHECKPOINT.json) records successful
GET paths, report hashes, source hashes and the local regression gate. This proves
only the selected repository's single-user reads. Remote writes, webhook delivery,
shared-team/account isolation, private repositories and VM credentials remain
separate acceptance targets. Other integration accounts are not promoted by this
result. No paid provider, microphone or installer is involved.

# Slack selected-channel simulation

Updated 25 October 2026. One `slack` member models the public `chat.postMessage`
and `chat.update` methods. No real workspace, identity or account is used.

## Execution

Start `postman simulation run postman/simulations/studio-slack.sim.yaml --port auto
--no-history --output ndjson`. Take its bound port and set
`NEXUS_ACCEPTANCE_SLACK_URL=http://127.0.0.1:PORT/slack`. Run
`PYTHONPATH=. python -m pytest tests/studio_slack_acceptance.py tests/studio_slack_files_acceptance.py -q -o addopts=''`.

## Assumptions

- `slack-sdk==3.45.0` must be installed separately. Missing SDK/configuration fails
  explicitly; the consumer never installs software or calls a live account.
- Text/thread consumers replace only the SDK base URL. The file consumer rewrites
  HTTPS transport destinations to the same local member after production URL
  checks; headers, streaming, limits, redirects and error handling run unchanged.
- Synthetic provider state is shared across local calls. The consumer records a
  starting counter rather than assuming an empty mock after every test.
- Published owner mock discovery was unavailable while Postman was signed out.
  The mock derives from the official public methods linked in the contract.

## Observations

25 October 2026: final four-test run served 48 requests through the sole `slack`
member: 24 diagnostic reads, 11 metadata/download GETs and 13 SDK POSTs.
Statuses: 42 HTTP 200, 1 HTTP 302, 3 HTTP 500 and 2 HTTP 429. No member stayed
silent. Full log also retains earlier fixture/debug runs; these counts cover only
the final successful run. The direct lost-response peer is separate.

All four tests passed, zero skips. Signed actual Studio HTTP events retain private
sender history and return real SDK parent-thread replies. The file consumer saves
private text and PNG artifacts and starts one text run with pinned revisions.
Duplicate receipt causes no extra reads. Metadata errors, HTML errors, oversized
files, redirects and truncated bodies fail without dispatch or extra file saves.
Event-supplied URLs are discarded. No external host or real Slack account is used.

Orbit's files.info brief confirms metadata, files:read and bearer auth but omits
the download operation. Official file-object documentation supplies that contract.
Hosted owner mock discovery remains unavailable while signed out. This proves
local HTTP behavior against public-contract stand-ins; real scopes/delivery,
vision providers, outbound artifacts and workflow semantics remain separate.

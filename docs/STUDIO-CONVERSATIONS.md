# Conversations, pinned sources and execution history

This additive framework extension keeps existing plain-text `output`, sessions,
run routes, graph/YAML workflows and account authorization readable. No rewriting
migration or frontend build is required. Existing private state upgrades retain
backup/recovery behavior described in [STUDIO-RECOVERY.md](STUDIO-RECOVERY.md).

## Conversation contract

Run polling now returns optional `stream` metadata: version 1, invocation ID,
monotonic sequence, status (`streaming`, `completed`, `interrupted-or-failed`) and
truncation flag. The recoverable preview is bounded to 1,048,576 characters.
The provider's final `output` is authoritative; intermediate tool turns are not
concatenated into the committed answer. Failed streams preserve partial evidence;
late callbacks from another or finished invocation are ignored. Guards that must
check output before display continue to disable streaming.

Providers can return `metadata.structured_content`, a bounded list of `text`,
`markdown`, `json` or `artifact` parts. Validated parts appear in the run and saved
assistant message with `content_version: 1`. Artifact parts require an authorized
artifact and immutable revision. Unknown types, foreign references and invalid
JSON values are omitted. Existing consumers can continue reading only `output`.

Markdown, code-copy controls, links, tables and citations update as snapshots
arrive. Reconnecting observes the same saved run. Playback and polling never
submit work; an unacknowledged submission is not automatically retried.

At phone widths (up to 767 pixels), conversation settings start collapsed behind
the Settings button. Opening or closing them preserves project, agent, provider,
model and effort selections. Settings open over the conversation and scroll
independently, keeping the composer in place. Escape closes settings and returns
focus to the button. Desktop
controls remain visible. The mobile composer keeps the message and send action
visible while moving explanatory hints out of the reading area.

## Quick Build validation and effort

Quick Build accepts an optional `effort` override through `POST /api/builds` and
the Effort selector. Model profiles apply provider, model and effort together.
Drafts retain effort and tool selection when reopened; old drafts default to
provider effort and web tools. Generated agents retain their own explicit effort
when saved. No state rewrite is required.

The builder receives workspace resource names and IDs, without connection
settings or credentials. Unknown resource references produce a named validation
error. A malformed proposal gets at most one additional generation call to
correct validation errors, using the same provider and effort within the original
600-second generation budget. Provider failures are not retried. This correction
can incur an additional model call under the selected provider's billing.
Invalid proposals never save or activate resources, and failed refinements leave
the previous draft intact. The run exposes `phase: correcting-proposal` during
that correction, then `draft-ready` when validation succeeds. `build_corrections`
records whether the extra call occurred. Review and activation remain explicit
actions.

## Retry from Activity

Activity → Details shows failed steps above the saved output. **Retry and
continue** retries the selected failure and resumes unfinished workflow work.
Completed nodes, map items, outputs and approval decisions stay saved. Nested
step paths identify the exact failed scope. A later failure stops the run again;
retrying never silently marks a step successful.

The read-only `GET /api/studio/runs/{id}/retry` returns a digest and available
steps. `POST` to the same route accepts `digest` and `step_id`. Stale plans,
completed steps and active workers reject dispatch. Repeating the same accepted
digest and step returns the existing receipt, including after completion.
Structured retries reset the selected scope's failed outcome and exhausted retry
budget while preserving acknowledged results. Existing checkpoint dispatch and
SQL lease fencing remain authoritative.

Uncertain integration actions require their existing Workspace tools review
before retry. Failed tools also require explicit confirmation that their effects
have been checked; only then can an unfinished tool's attempt receipt be reset.
Cancellation cleanup retains its separate continuation controls.

Failed chat, Quick Build, prompt/schedule design and standalone tool runs can
retry their saved request with the original provider, effort and pinned inputs.
File deployments retain these private requests for new runs; SQL deployments
reuse their durable job requests. Older standalone runs without a saved request
report unavailable. Other specialized jobs keep their existing recovery controls.
Only the original account with current execution permission can retry. Existing
records remain readable; no bulk migration or automatic replay is performed.

## Pinned sources

`GET /api/catalog/artifacts/{id}/preview?revision=N&page=N` retains the existing
preview route with optional positive integer selectors. PDF returns extracted
text for one page; Office returns a text section in indexed source order, which
is not necessarily a printed page or slide. Responses identify `artifact_id`,
`revision`, `page`, `unit`, extraction availability and, when known, `pages`.
Oversized/unsupported sources and missing optional extractors have bounded,
explicit results rather than silently switching providers.

`/artifact.html?id=ID&revision=N&page=N` is a standalone authenticated source
viewer. Citations open the same pinned revision in Studio's document viewer.
Archiving a document does not invalidate existing authorized citations. Replacing
it leaves old revisions intact. Current and revision-level authorization are both
checked; citation links do not grant access. Download uses the existing
`GET /api/catalog/artifacts/{id}/versions/{revision}` route.

HTML preview remains an empty-sandbox frame with deny-by-default content policy
and sanitizer restrictions. Local adversarial browser checks stripped active
elements, form controls, external loads and credentialed links; fixture request
traps observed zero loads. See the local acceptance record for exact boundaries.

## Read-only execution contract

`GET /api/studio/runs/{id}/execution?limit=500&before=EVENT_ID` returns version 2,
owner-scoped `graph`, `states`, `timeline`, `invocations`, `replay` and `coverage`.
Limit is 1–1000. `replay.previous_cursor` requests an earlier page; missing cursors
fail explicitly. Timeline events contain bounded metadata, not tool arguments,
conversation content or credentials. New records have timestamps and durable
per-run sequence numbers. Missing legacy times remain explicitly unknown.

Live graph/replay includes recorded node, branch, map/nested workflow, invocation,
delegation, tool, gate and artifact events. Artifact events retain their revision.
Workflow definitions remain authoritative. For chat without a workflow, participant
nodes show recorded calls; dotted run links indicate membership, not execution
order. Parent links require a recorded, unambiguous relationship. Provider-internal
calls and absent parent relationships cannot be reconstructed. Legacy artifact
records without a revision can only resolve the current known revision.

Previous/Play/Next/Live and the keyboard slider review saved events only. Earlier
pages load without dispatching or approving work. Graphs retain readable node
sizes in a keyboard-scrollable region; graph/node/event limits are explicit.

## Embedded agent surface

`/agent.html?agent=AGENT_ID` uses existing Nexus sessions and API authorization.
It fixes the selected agent and rejects unavailable agents. By default, framing
is allowed only from the same origin. Team policy may explicitly configure
`embed_origins` with at most ten HTTPS origins, for example:

```json
{"embed_origins": ["https://portal.example"]}
```

Only this page receives the additional frame origins. No session is placed in a
URL and no API authentication bypass or cross-origin credential forwarding is
introduced. Real third-party cookie/session behavior awaits the personal HTTPS
VM. An iframe allowlist alone does not establish hosted login acceptance.

## Example and evidence

Run `PYTHONPATH=. python examples/nexus-bot/src/studio_conversations.py` to consume
these framework APIs with a synthetic streaming provider, cited Office sections,
typed artifact output and an approval workflow. No provider account is contacted.

Required regression checks cover streaming completion/failure, bounds, late
callbacks, ownership/project revocation, pinned revisions, document extraction,
execution paging, workflow formats, gates and delegated participants. Browser
logic checks cover reconnect safety and read-only playback. Isolated browser
checks at 390×844, 768×1024 and 1365×900 verified bounded layout, progressive
formatted chat/code copy, pinned Office navigation and approval history.

Milestone 1's source/upgrade foundation and Milestone 2's local acceptance pass.
Local acceptance covers all preview
families, hostile HTML request traps, three viewport classes and a branching
specialist/gate/tool/artifact run recovering two dropped polling connections with
one synthetic effect. Physical devices, hosted cross-origin login/cookies and
real provider accounts remain separate live checks.

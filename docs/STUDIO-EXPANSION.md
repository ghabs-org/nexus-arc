# Studio expansion implementation record

Requested scope: “Ok go ahead doing them all” and rename the ARC workbench title.
User-facing title: **Workspace tools**; existing hashes/APIs remain compatible.

- [x] Rename user-facing workbench navigation/title
- [x] Native GitHub/GitLab setup and issue/PR/MR actions
- [x] Messaging setup, verified incoming events, approvals and notifications
- [x] Managed MCP configuration and explicit agent tool bridge
- [x] Quick Build DAG/resource-only proposals and definition version activation
- [x] Autonomous local coordination/model routing and external A2A client tasks
- [x] Mapped legacy history import, YAML approvals/checkpoints/parallel groups, typed decisions/retries
- [x] Structured evaluation datasets/judges, execution traces and output guardrails
- [x] Binary artifact versions/previews, PDF/image inputs and selectable stores
- [x] Transactional SQL/PostgreSQL state, leased jobs/recovery, runtime and Loki configuration
- [x] Shared permissions, example/deployment docs, package and browser verification

## Verification

* Expanded Studio/ARC regression: **342 passed, zero skipped**.
* Final native-connection/messaging/typed-node checks: **20 passed**.
* Final shared-auth/permission checks (including separate MCP homes): **14 passed**.
* Durable-state contract on an isolated real PostgreSQL 17 container: **5 passed**.
  Covers persistent queuing, exclusive claims, lease fencing/reaping, concurrent
  updates and graph/YAML approval recovery. Temporary container removed afterward.
* Real local MCP SDK stdio discovery/input schema/tool invocation verified, including
  compatibility with SDK 2 field names. No real messaging or social account contacted.
* Real PDF extraction and multimodal OpenAI request shapes tested with injected API clients.
* Actual Codex CLI Quick Build generated a zero-agent word-count DAG; browser review,
  save and Test completed with output **3** for “one two three”. First live attempt
  exposed ambiguous graph instructions; exact schema examples corrected that path.
* Browser checked renamed main title, version view, incoming channel save, focused
  MCP fields and Quick Build execution. Main local app refreshed on port 5004.
* Python lint, all Studio JavaScript syntax checks, advanced example consumer and
  wheel build passed. Protected pre-existing user edits retain baseline hashes.

These sets overlap and must not be summed. No commit, remote publish or public
infrastructure deployment was performed.

## Practical boundaries

This completes the agreed expansion, not a declaration of every external enterprise
feature. [Studio guide](STUDIO.md) describes the implemented behavior and limits.
[Deployment guide](../deploy/studio/README.md) covers account/infrastructure setup.

A2A supports a 1.0/0.3 JSON-RPC client, streaming and an opt-in text server subset;
push and general protocol equivalence are not claimed. MCP software is
administrator-installed; stdio servers can use per-call or account-scoped pooled sessions. Legacy YAML parallel groups are bounded and
contiguous; arbitrary DAG branching uses the canvas. OpenClaw approval codes use
its explicit signed bridge envelope. Shared SQL workers require common definitions,
audio/local byte volumes and consistent Nexus auth configuration. Leases fence
stale writes and expose interrupted attempts; they do not guarantee exactly-once
third-party side effects. Cloud stores, production SSO/broker, Loki collector and
public TLS deployment still require real operator configuration and live validation.

## Implementation modules

New framework services include `studio_connections`, `studio_messaging`,
`studio_tool_loop`, `studio_nodes`, `studio_coordination`, `studio_external`,
`studio_artifacts`, `studio_artifact_extract`, `studio_memory_sources`,
`studio_legacy_workflows`, `studio_evaluations`, `studio_observability`,
`studio_state`, `studio_runtime` and `studio_versions`.

## Protocol references used

* [A2A 1.0](https://a2a-protocol.org/v1.0.0/specification/), [A2A 0.3](https://a2a-protocol.org/v0.3.0/specification/)
* [OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision?api-mode=chat), [file inputs](https://developers.openai.com/api/docs/guides/file-inputs)
* [Slack request verification](https://docs.slack.dev/authentication/verifying-requests-from-slack/)
* [Discord interactions](https://docs.discord.com/developers/interactions/receiving-and-responding)
* [Telegram webhooks](https://core.telegram.org/bots/api#setwebhook)
* [GitHub pull requests](https://docs.github.com/en/rest/pulls/pulls), [GitLab merge requests](https://docs.gitlab.com/api/merge_requests/)
* [SQLAlchemy locking](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.GenerativeSelect.with_for_update)
* [Loki HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/)


## Follow-up workspace improvements

- [x] Reconnect-safe run monitoring, reload recovery and no mutation replay.
- [x] Owner-scoped approval/recovery inbox with current-state review checks.
- [x] Guided connection checks and explicit revision-bound enabling.
- [x] Installable public app shell with private API/auth cache exclusions.
- [x] Optional MCP session pooling and incoming/outgoing A2A streaming.
- [x] Deployment report and verifier; isolated HTTPS and multi-process checks.
- [ ] Physical Safari/Android checks against an actual shared HTTPS deployment.
- [ ] Live production identity, broker, cloud and account validation.

The last two items require real devices and configured infrastructure; local tests
are not substitutes for those checks.


The 18 October local deployment pass added strict hosted checks and a one-owner
personal VM plan. Real PostgreSQL/MQTT tests and a matching file/database restore
passed. Production SSO/VM/phone/cloud checks remain distinct from this evidence.

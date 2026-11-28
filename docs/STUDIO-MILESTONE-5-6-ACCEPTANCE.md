# Milestones 5 and 6: local acceptance

As of 21 October 2026, all 19 frozen M5/M6 clauses and exits have supported local acceptance. This closes the Nexus implementation profile, not independent reference runtime equivalence or live account/deployment validation. The overall eight-milestone plan remains open.

## Acceptance evidence

| Gate | Passed | Skipped |
| --- | ---: | ---: |
| Required Studio/framework regressions | 1,200 | 0 |
| Optional A2A gRPC, callback SDK and image contracts | 25 | 0 |
| Decision adapters against actual local HTTP simulations | 32 | 0 |
| Five SQL dialects: driver policy and client-boundary behavior | 15 | 0 |
| Fresh PostgreSQL, MySQL, MariaDB and MongoDB servers | 7 | 0 |
| Browser logic | 33 | 0 |

Suites overlap; these numbers are not a unique-test total. The additional framework consumer made ten actual local HTTP requests. Health probes made five. Decision simulations handled 23 and 21 actual requests. All owned simulation processes and four disposable database containers were removed. Vendor services were not called.

## Clause coverage

| Plan clause | Accepted behavior | Primary checks |
| --- | --- | --- |
| M05-01 | Map, bounded loop, nested workflow, switch and typed mappings | test_studio_workflow_v2.py; test_studio_typed_nodes.py; test_studio_workflow_authoring.py |
| M05-02 | Schema checks, timeout, safe retry, handlers and checkpoint recovery | test_studio_workflow_v2.py; test_studio_interop_shared_handlers.py |
| M05-03 | 100-item map, concurrency 4, 100 loop iterations, depth 10; administrator overrides | test_studio_interop_policies.py; test_studio_workflow_v2.py |
| M05-04 | Approvals, uncertain effects and no automatic mutation replay | test_studio_workflow_handoff.py; test_studio_interop_cancellation.py |
| M05-05 | Bounded non-external platform reference agent/workflow round trip and complete unsupported-field preflight | test_studio_interop_configuration.py; test_studio_interop_workflows.py; test_studio_interop_shared_loops.py; test_studio_image_scopes.py |
| M05-06 | Create/refine/reopen supported resources and child workflows; dependency checks | test_quick_build.py; test_studio_build_workflows.py; studio_build_authoring.test.cjs |
| M05-07 | Differences, lifecycle status, health, deactivation and rollback | test_studio_build_lifecycle.py; test_studio_resource_health.py; test_studio_extended_health.py |
| M05-08 | Reviewed-revision activation and explicit credential-dependent enabling | test_studio_build_lifecycle.py; test_studio_team.py |
| M05-09 | Shared validate/plan/apply/export and atomic preflight/recovery | test_studio_cli.py; test_studio_configuration.py; test_studio_release_recovery.py |
| M05-EXIT | Build/refine/activate/test/edit/rollback through shared runtime | test_studio_build_lifecycle.py; test_studio_cli.py |
| M06-01 | MCP stdio, SSE, Streamable HTTP; scoped authentication and delegated OAuth/OIDC | test_studio_mcp_http.py; test_studio_mcp_oauth.py; test_studio_remote_connectors.py |
| M06-02 | Bounded OpenAPI 3.0/3.1 import, selected operations and authenticated invocation | test_studio_openapi_semantics.py; test_studio_remote_connectors.py |
| M06-03 | Five pooled SQL dialects plus SQLite; read-only default and reviewed writes | test_studio_sql_driver_policy.py; studio_sql_live_acceptance.py |
| M06-04 | Bedrock knowledge/Agents/Flows, MongoDB, Nuclia and Power BI | test_studio_enterprise.py; studio_mongo_live_acceptance.py |
| M06-05 | A2A files, typed data, artifacts, continuation, push and cancellation | test_studio_a2a_content.py; test_studio_a2a_push.py; test_studio_external_callbacks.py |
| M06-06 | Shared task service for JSON-RPC, HTTP/JSON and optional gRPC; 0.3/1.0 compatibility | test_studio_a2a_http.py; studio_a2a_grpc_acceptance.py; studio_a2a_callback_sdk_acceptance.py |
| M06-07 | Conservative capabilities and truthful cancellation outcomes | test_studio_a2a_extended.py; test_studio_interop_cancellation.py |
| M06-08 | MCP gateway, authenticated REST/CLI, native Teams and existing messaging adapters | test_studio_mcp_gateway.py; test_studio_teams_events.py; test_studio_messaging.py |
| M06-EXIT | Independent local protocol discovery/auth/stream/continuation/artifact/cancel/recovery contracts | The required and optional protocol suites above |

Check files are under `tests/`. The SQL client-boundary matrix uses synthetic engines and does not prove server behavior. Fresh server checks cover PostgreSQL, MySQL, MariaDB and MongoDB only.

## Supported boundaries

- Completion means the frozen M5/M6 Nexus implementation and supported local acceptance profile; it does not mean every upstream reference construct is supported.
- Mixed map/loop ownership, ambiguous unjoined shared aliases and unsupported source syntax/settings reject before saving. Concurrent declaration-order ties use documented Nexus policy.
- OpenAPI supports the documented bounded 3.0/3.1 profile. Remote/recursive references and unsupported media/serialization constructs reject during preflight.
- OAuth clients require explicit provider registration. Teams subscriptions require operator registration/renewal. Non-probeable health operations report unsupported.
- Actual cancellation reports observed task state and never promises to undo completed external effects.
- Account-dependent resources remain unavailable until configured and explicitly enabled. No credentials or user workspace were changed.

## Live checks still pending

- Real SQL Server and Oracle servers: authentication, driver behavior, timeout/cancellation and pool recovery.
- Selected real MCP/OAuth providers, A2A peers, Microsoft tenant, Bedrock, Nuclia, Power BI and other enterprise accounts.
- Independent installed reference runtime comparison; source/schema fixtures do not prove identical runtime ordering.
- Personal HTTPS VM/TLS operation and physical mobile-device checks.

## Compatibility and operations

The final regression pass found and fixed a failed-run publication race: readers could observe terminal status before the failed conversation receipt existed. Both records now publish under the existing workspace lock/SQL transaction. Deterministic file/SQLite tests cover this boundary; the conversation example demonstrates it.

No bulk state rewrite or new scheduling/authentication service was added. Existing legacy loop checkpoints reconstruct missing controller receipts only from acknowledged final-iteration outputs. Missing evidence stops explicitly without replaying effects. Existing attachment/mobile work remains intact.

The running Studio on port 5004 was not restarted. Restart it through its normal launcher to load the changed runtime. No real accounts, credentials, public endpoints or deployments were changed.

Current private audit details: `STUDIO-M56-FINAL-ACCEPTANCE.json`. Historical checkpoints retain original results and now point to that superseding status. Frozen baseline identities and catalog aliases remain unchanged.

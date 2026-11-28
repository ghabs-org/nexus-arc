# ADR-087: OpenClaw Studio Integration Architecture

**Status:** Accepted  
**Date:** 2026-10-23  
**Milestone:** 12  
**References:** ADR-086, docs/OPENCLAW_OPERATOR_SURFACE.md, packages/nexus-arc/README.md

## Context

OpenClaw acts as the trusted conversational and operator control plane where operators interact with Jarvis. Nexus Studio provides a visual workbench, catalog definitions (agents, workflows, evaluators, datasets), durable execution runs, artifact storage, and human-in-the-loop approval gates.

To provide seamless access to Studio work from OpenClaw, we must establish a clear integration path that unifies agent discovery, execution submission, artifact inspection, and approval continuation without fragmenting the operator experience.

## Evaluation of Architectural Paths

### Path A: Extend `@nexus-arc/openclaw-plugin`

Extend the existing OpenClaw plugin in `packages/nexus-arc` to support Studio entities alongside existing ARC workflows:

- **Pros**:
  - Single plugin installation and configuration block in OpenClaw (`plugins.entries.nexus-arc`).
  - Preserves existing `/nexus`, `/chat`, and `/chatagents` commands without breaking changes.
  - Unifies ARC command bridge and Studio APIs through cohesive operator subcommands:
    - `/nexus studio agents`: List permitted Studio agents.
    - `/nexus studio workflows`: List permitted Studio workflows.
    - `/nexus studio run agent|workflow <id> <task>`: Submit durable work.
    - `/nexus studio status <run_id>`: Inspect progress and pending gates.
    - `/nexus studio artifacts <run_id>`: Inspect exact output revisions.
    - `/nexus studio reply <token> approve|reject`: Answer an approval prompt.
    - `/nexus studio reply <token> input <JSON input-ID map>`: Answer an input prompt.
  - Keeps the legacy bridge commands and adds a signed Studio transport within the same plugin.
- **Cons**:
  - Requires bridging Studio's authenticated API routes (or signed channel entrypoints) through the bridge client.

### Path B: Dedicated `@nexus-studio/openclaw-plugin`

Create a separate plugin package dedicated strictly to Studio:

- **Pros**:
  - Decoupled lifecycle between ARC low-level workflow engine and Studio workspace.
- **Cons**:
  - Operators must install, enable, and authenticate two separate plugins.
  - Confusing command collision and cognitive overhead (e.g. `/nexus` vs `/studio`).
  - Duplication of networking, error handling, polling, and Markdown rendering logic.

## Decision: Adopt Path A (Extend `@nexus-arc/openclaw-plugin`)

We choose **Path A**. Extending `@nexus-arc/openclaw-plugin` provides one operator
surface and preserves the existing bridge commands. Studio operations use
`POST /api/openclaw/<entrypoint_id>` with the existing channel signature and saved
identity mappings. The agent-visible `nexus_studio` tool is optional and requires
an allowlisted version 2 runtime factory with trusted sender/channel context.

## Architectural Contracts & Invariants

1. **Ownership & Affinity**:
   - One execution owner per task (`requester_nexus_id`).
   - The gateway hashes platform, sender, channel and thread into a stable caller-session key. Studio persists a scope-bound mapping from that key to its own conversation ID.
   - Legacy issue/workflow bridge affinity keys remain independent. Studio never interprets a legacy `nexus::session:<id>` or `nexus:<project>:workflow:<id>` string as a Studio conversation ID.
2. **Approval & Input Gates**:
   - Status reads for a waiting workflow return a `reply_token` bound to the caller, run and exact pending gates. The agent tool removes that capability from its results.
   - Human commands pass `token` with `decision` or `values` to the signed endpoint. Studio resumes the existing checkpoint and run; it does not submit a replacement task. Tokens expire after 24 hours and are single-use across new request IDs.
3. **Artifact Handling**:
   - Artifacts generated during runs are referenced by ID and revision; OpenClaw renders summary previews with download references.
4. **Security & Replay Protection**:
   - Legacy ARC bridge routes retain their Bearer authentication. Studio operator requests require HMAC-SHA256 over the timestamp and exact body, a timestamp within 300 seconds, a matching entrypoint ID, and no browser Origin.
   - Enabled entrypoints explicitly grant `operator_targets` and map platform senders/channels to Nexus identities. Current project access and target scope are rechecked for every request.
   - Stable mutation IDs provide deduplication. File-backed dispatch claims remain uncertain after an ambiguous crash. SQL receipt/job changes commit atomically before workers execute them. Changed arguments under an existing request ID are rejected.

## Acceptance scope

The [wire contract](../postman/contracts/studio-openclaw.md) defines exact operation
arguments. Local tests cover the plugin runtime harness, actual Studio HTTP/jobs,
restart recovery, scoped artifacts and approval/input continuation. Configured
gateway channels and personal HTTPS VM verification remain separate pending
checks.

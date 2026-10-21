# Nexus Studio interactive guide

Status: implemented (`GUIDE_VERSION = 1`).
Requested: 20 October 2026.
Placement: reusable Studio framework UI (`nexus/core/studio_onboarding.py`, `nexus/core/studio_workbench.py`, `nexus/studio_static/index.html`, `nexus/studio_static/studio.css`, `nexus/studio_static/experience.js`), with the `examples/nexus-bot/src/studio_experience.py` consumer.

This is a user-requested addition to the roadmap. It does not change the frozen
parity feature inventory, baseline, catalog identities or existing acceptance counts.

## Delivery order

The guide is delivered at version 1. Milestones 5 and 6 now have scoped local
acceptance; their earlier incomplete status is historical. Keep the guide aligned
with screens changed during remaining catalog and operational work, then verify
it on physical devices and the personal HTTPS VM. The
[current SAM implementation plan](STUDIO-SAM-IMPLEMENTATION-PLAN.md) records
remaining work and separates implementation from account/device/VM validation.

## Entry and persistence

- On the first successful Studio visit, offer a welcome with Start guide and
  Skip for now. Do not interrupt an active run, restored conversation or approval.
- Add Help → Interactive guide to the navigation menu, accessible on mobile.
  It can restart the guide or resume saved progress at any time.
- Record completion, dismissal and step progress per workspace and signed-in
  Nexus user; desktop mode has its own local scope. Reuse existing preference
  storage where available. Store no prompts, credentials or provider responses.
- Version the guide independently. An upgrade never silently resets dismissal
  or starts the whole tour again; offer new guidance explicitly when needed.
- If persistent storage is unavailable, keep session progress and allow normal
  navigation. Guide failures never prevent using Studio.

## Walkthrough

Use short explanations anchored to real controls, with progress, a quick-jump step
selector, Back, Next and Skip/Finish across all Studio screens (13 steps total,
plus optional deeper branches and disposable local practice previews):

1. Workspace navigation (`#chat` / `#workspace-navigation`): conversations, agents and reusable resources.
2. Provider/account selection (`#providers` / `#view-providers`): subscription-backed CLI versus API billing;
   show the currently available options without initiating authentication.
3. Conversations (`#chat` / `#chat-form`): choose an agent, model/effort, attach selected files and inspect results.
4. Quick Build (`#quick-build` / `#builder-form`): describe a use case, review generated components and differences.
5. Agents (`#agents` / `#view-agents`): create and version specialists with instructions, tools, skills and guardrails.
6. Workflows (`#workflows` / `#view-workflows`): inspect branches, visual canvas nodes, inputs, testing and saved definitions.
7. Knowledge projects (`#projects` / `#view-projects`): group project instructions, reference documents and cited conversations.
8. Library and connectors (`#connectors` / `#integrations-panel`): prompts, skills, artifacts, schedules, evaluations, external A2A agents and revision-bound read-only connector checks.
9. Registered tools (`#tools` / `#view-tools`): inspect and test registered Nexus tools available to agents and workflow nodes.
10. Workspace tools (`#workbench` / `#view-workbench`): memory, SQLite checkpoints, agent coordination, publishing campaigns, audit analytics, audio transcription, typed decisions, file storage, prompt/launch controls and deployment checks.
11. Workspace, mesh, and sharing (`#workspace` / `#view-workspace`): local runtime queue health, live execution events, portable workspace export, Response feedback (`#feedback`) and Conversation sharing (`#sharing`).
12. Activity and approval inbox (`#inbox` / `#view-inbox`): inspect `#activity` execution traces and review pending workflow gates or external changes.
13. Where to find Help and reopen the guide (`#open-interactive-guide` / `#help-guide-button`).

Use installed capabilities and the user's permissions to choose valid steps.
Explain unavailable features or omit the optional branch; do not point at missing
controls. Preserve the current screen and restore it after a user-requested tour
where practical. Users may leave the guide and continue normal work at any point.

Navigation and highlighting do not create resources, launch agents, run connector
checks, send messages, upload documents or consume model usage. If a user chooses
a real setup/test action, use its existing form, review and approval flow.
Optional practice uses clearly labelled disposable local examples and requires an
explicit user action; it never modifies existing definitions.

## UI and accessibility

Keep the current Studio components and visual language. Package any required
assets locally; installation must not require a frontend build or CDN access.

- Desktop: a compact anchored callout with clear target highlighting.
- Phone/tablet: a readable docked panel that keeps its target and navigation usable;
  accommodate the mobile navigation drawer and on-screen keyboard.
- Accessible labelled controls and progress announcements; keyboard Back/Next and
  Escape dismissal; restore focus when closing. A nonmodal anchored step must not
  trap focus away from the control it explains. A welcome dialog uses the existing
  accessible dialog behavior.
- Respect reduced motion. Reposition on resize and scrolling; prevent off-screen
  callouts and horizontal overflow. Missing/hidden targets fail gracefully.
- Use plain product language; avoid ARC implementation terms and internal IDs.

## Acceptance and delivery

Framework implementation first, followed by a small nexus-bot example consuming
the same guide configuration. Include migration notes and update the roadmap.

Verify:

- Fresh visit offers the guide; completion and dismissal prevent repeated prompts.
- Help reopens/restarts it; progress survives reload where storage is available.
- Users and workspaces have independent progress; no previous user's state leaks.
- Existing users, restored chats, active runs and approvals are not interrupted.
- Missing providers, empty workspaces and restricted roles produce valid steps.
- No tour step starts a model call, connector action or external mutation by itself.
- Keyboard/screen-reader behavior, reduced motion and storage failure work.
- Real browser checks at phone, tablet and desktop widths; save visual evidence.

Do not mark this feature complete based on documentation or menu visibility alone.

## Implementation and migration notes (v1)

- **Server & storage scope**: `StudioOnboarding.guide_state` and `save_guide_state` in `nexus/core/studio_onboarding.py` persist per-user/per-workspace progress in the `onboarding-guide` state collection (`GET /api/workbench/guide` and `POST /api/workbench/guide`), keyed by `StudioWorkbench.settings_key(actor)`. In shared team deployments, all authenticated roles (`viewer`, `operator`, `builder`, `owner`) may read and update their own guide progress (`nexus/core/studio_team.py`).
- **Browser fallback**: `nexus/studio_static/experience.js` mirrors progress to a workspace- and user-scoped `localStorage`/`sessionStorage`/in-memory key (`nexus-guide:<mode>:<user>:<workspace>`) so offline or storage-restricted sessions remain usable without blocking Studio.
- **Version upgrades**: `GUIDE_VERSION = 1` is tracked independently of `SCHEMA_VERSION`. When `seen_version < GUIDE_VERSION` for an already completed or dismissed user, `upgrade_available` highlights **Help → Interactive guide** (`Updated` badge) without resetting dismissal or auto-opening a modal.
- **Example consumer**: `examples/nexus-bot/src/studio_experience.py` accepts `--guide {status,start,complete,dismiss,reset}` to inspect or pre-configure the workspace onboarding guide state via framework APIs.

## Planned provider addition: Gemini API key (Google AI Studio vs. Vertex AI)

To complement subscription-backed Gemini execution via Antigravity CLI (`agy`) and existing API-billed providers (`openai`, `claude`), Studio can expose a `gemini` API provider (`kind: "api"`):

1. **Primary API-key source — Google AI Studio (`GEMINI_API_KEY`)**:
   - **Why default to AI Studio**: Keys are created directly at `https://aistudio.google.com/apikey` with a single environment variable (`GEMINI_API_KEY`), requiring no Google Cloud project IAM setup, service-account JSON files, or `gcloud` ADC login. This matches the exact ergonomic model of `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in local desktop and shared Studio modes.
   - **Shared-mode storage**: Reuses the existing `gemini_api_key_enc` column already preserved in `nexus_user_credentials` (`nexus/core/auth/credential_store.py`).
   - **Reasoning effort**: Maps Studio's `effort` levels (`low`, `medium`, `high`, `xhigh`, `max`) to `thinking_config` (`thinking_level` / `thinking_budget`) in the unified `google-genai` SDK.
2. **Optional enterprise source — Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=true`)**:
   - **When to use Vertex AI**: Organizations requiring GCP consolidated billing, regional endpoints (`GOOGLE_CLOUD_PROJECT` + `GOOGLE_CLOUD_LOCATION`), VPC Service Controls, or Vertex AI Express API keys.
   - **Unified adapter path**: The official `google-genai` SDK supports both AI Studio (`genai.Client(api_key=...)`) and Vertex AI (`genai.Client(vertexai=True, project=..., location=...)` or Vertex Express API key) behind a single `GeminiAPIProvider` adapter without duplicating model execution logic.

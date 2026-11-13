# Studio model router, provider fallback and token use

Studio can let [nexus-router](../../nexus-router) choose the model for a chat or
workflow run, fall back to other providers when one is out of quota, and show
the tokens each provider reported. Everything here is opt-in.

## Connect a router

Add `router` to the Studio runtime configuration file (outside the project,
mode `0600`, passed to `nexus studio --runtime-config`):

```json
{"router": {"url": "http://127.0.0.1:7771", "allow_private": true, "timeout": 20}}
```

`allow_private` is required for plain HTTP. For a router behind an authenticating
proxy, add `"token_env": "ROUTER_TOKEN"`; in team mode each user's own value of
that variable is used, never the host's. The URL is server-owned: browsers and
models cannot point Studio at another router.

`timeout` (seconds, 1–120, default 20) bounds each router call. Keep it above the
router's own classifier budget (`JEV_TIMEOUT_SECONDS`); a slower answer is treated
as "router unavailable" and Auto falls back to a ready provider. This setting is
Studio-specific (`StudioModelRouter`); the shared `RouterClient` still defaults
to 10 seconds for other callers unless they select a different timeout. A router
can be healthy yet miss Studio's deadline while classifying a request; a fallback
notice alone does not prove a failed generation or missing release notes.

In chat the notice names the fallback provider while a run is pending. It says
**Answered by** only after completion. **Working…** is the pending state; inspect
Activity or reopen the saved conversation for the final status and output.

The separate Nexus Router source fix `0e7933d` allows healthy alternatives when
all preferred candidates belong to unhealthy providers. Updating Studio alone
does not deploy that router fix; qualify the actual router revision separately.

### Desktop app

The built-in router requires a random token generated at every launch; other
local processes cannot call it. The desktop app needs no flag:

1. If `runtime.json` exists in the desktop config folder, it is used like
   `--runtime-config` (same rules: mode `0600`, same keys). macOS:
   `~/Library/Preferences/NexusStudio/runtime.json`; Windows:
   `%APPDATA%\NexusStudio\config\runtime.json`; Linux:
   `~/.config/nexus-studio/runtime.json`. A file that fails those rules is ignored
   with a warning in the app log rather than stopping the app.
2. If it names no router, the app starts the **nexus-router built into the app**
   inside its own backend process, on `127.0.0.1` at a free port. Its database
   lives in the desktop data folder (`.../NexusStudio/router`). The Model router
   panel labels it "Router built into this app".

The built-in router chooses from the model list (`generated/models.json`)
captured when the app was built. Its file date is the build date, so the panel's
"registry is older than 48h" warning does not reflect how old the list really is;
the bundle manifest records the router revision and list digest. A router you run
yourself and refresh from OpenClaw stays current. It never sends
task text to a hosted classifier (`JEV_BACKEND=off` unless you set it), and its
optional local classifier model is not shipped, so it classifies with
heuristics.

Packaging: `apps/studio-desktop/build_desktop.py` ships a sibling `nexus-router`
checkout automatically, or the one given with `--router-source` /
`NEXUS_STUDIO_ROUTER_SOURCE` (then it is required). The build environment needs
`httpx` (the full profile has it); the bundle manifest records `model_router`
(included, git revision, registry digest).

## Auto (model router)

With a router configured, the chat **Provider** menu and a workflow run's
**Provider override** offer **Auto (model router)**. Saved agents can also select
`auto`; a workflow override applies to its individual steps.

For each model call Studio:

1. sends the task to `POST /route` with `allowed_providers` (the router ids of the
   providers you have set up) and the conversation as `experiment_key`;
2. maps the router's choice and fallbacks to Studio providers
   (`nexus/core/studio_routing.py`): `openai-codex`→Codex, `anthropic`→Claude Code,
   then the Anthropic API, `github-copilot`→Copilot, `google-antigravity`→Antigravity,
   `opencode`→OpenCode, `openai`→OpenAI API;
3. runs the first one that is ready, and records `router_decision_id`,
   `routed_model` and `provider_requested: "auto"` on the run;
4. posts `/outcome` (success, latency, tokens). A switch after a usage limit is
   reported as a fallback; a switch because the pick was not set up here is
   reported as an override, not a model failure. Cancelled runs are not reported.

If the router is unreachable or picks nothing Studio can run, the answer still
comes from your first ready provider and the run carries `router_error`.

Every workflow step routes on its own, because a workflow's provider override
applies to each step.

### Feedback

Routed answers show where they ran and offer **Good pick**, **Needed a stronger
model**, **A lighter model would do** and **Wrong kind of task?**. These go to
`POST /api/studio/router-feedback`, which takes the decision id from the run
record (never from the browser), checks you own the run, and posts the router's
`/feedback` with `source_surface: "studio"`. The payload builder is shared with
the Telegram and Discord feedback (`nexus/core/router_feedback.py`).

### What Studio sends, and what it keeps

- **Caller catalog:** the router ids of your saved model profiles (Library →
  Models) and today's model suggestions (`nexus/core/studio_models.py`) for your
  ready providers, as `available_models`. The router
  scores exactly these, so a router whose catalog is older than Studio's models
  (including the one built into the desktop app) still picks current models.
- **Links:** `nexus_context` (workspace, run or workflow, step) and
  `source_tag: studio:<run_id>`; the Model router panel lists recent routed runs
  from the router's `GET /decisions`.
- **Signals:** whether the message has image attachments, the last few
  conversation turns (used for classification, not stored by the router), a
  workflow agent node's optional `task_type`, and an experiment variant's
  `route_mode`.
- **Kept on the run:** a trimmed explanation (rules, task, top three candidates)
  shown as **Why this model?** under routed answers, and one entry per routed
  step in `router_decisions` (shown in run detail), each with its outcome.

### Where Auto is available

Chats, workflow runs, saved agents (provider `auto`), delegation coordinators,
the Studio assistant, Quick Build (generation runs on the router's pick; the
proposed agents keep `auto`), OpenClaw entrypoints (optional `provider`, may be
`auto`) and experiment variants (`provider: auto` plus an optional
`route_mode`: balanced, fast, reasoning, eco, free). An experiment case's
verdict is reported as the outcome of its routed decision. "Build with AI" for
prompts, schedules and datasets generates on the router's pick, like Quick Build.

While Auto is selected, the composer shows **Auto would use …** for the draft
(a router dry run: nothing is recorded).

### Learning from Studio

- Thumbs up/down on a routed answer is also sent to the router (correct /
  confirmation, wrong / quality), from the web, Slack or Teams.
- Every usage/rate-limit failure a provider returns in Studio, Auto or not, is
  reported with `POST /observe`, so the router's provider cooldowns also cover
  OpenClaw and CLI traffic.

## Fallback providers

**Workspace tools → Controls → Fallback providers** is your ordered list, empty by
default. Studio moves to the next provider only when:

- the provider is not ready (not installed, not signed in, unavailable for you), or
- it failed with a usage/rate-limit message (`usage limit`, `rate limit`, `quota`,
  `429`, ...) **before streaming anything**.

Runs with tools never switch after starting, because the next provider could
repeat side effects. Evaluations and experiments never fall back: they compare
exact providers. A switched run records `fallback_from` and `attempts`.

## Token use

Controls also shows your own token use for the last 30 days per provider, from
the invocation traces (`GET /api/workbench/usage?days=30`), with an estimated
cost: CLI providers show "subscription"; API providers are priced from the
router's tariff table (`GET /pricing`, `policies/pricing.yaml` in nexus-router,
checked against the Anthropic and OpenAI price pages on 2026-11-08, including
their long-context tiers) when a router is configured, else "no tariff". Cache
reads are priced as base input, so cached prompts are overestimated. Claude Code,
Antigravity and the Anthropic API report `input_tokens`/`output_tokens`; they are
now stored as `prompt_tokens`/`completion_tokens` (cache reads/writes count as
prompt tokens) so the existing per-run token summary sees them too. Codex does not
report usage and shows "not reported". Money and subscription quota are not measured.

## Providers view

With a router configured (owners only in team mode), each provider card adds a
**Router view** line: the router's health score, sign-in and quota state, and any
cooldown for the matching router provider. This is the router's view, which also
gives Copilot and OpenCode a sign-in state Studio cannot check locally.

## Model router panel

**Workspace tools → Model router** (owners only in team mode) shows the router's
`/diagnostics` warnings, registry and classifier state, provider health, model
cooldowns, and the CLI wire proxy's pinned sessions and 24-hour token use, plus the
`export` lines to attach a local CLI. Each part fails on its own.

## Verification

```bash
.venv/bin/pytest -o addopts='' tests/test_studio_router.py
node --test tests/studio_router.test.cjs
python examples/nexus-bot/scripts/check_router_status.py   # against a running router
```

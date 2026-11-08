# Studio model router, provider fallback and token use

Studio can let [nexus-router](../../nexus-router) choose the model for a chat or
workflow run, fall back to other providers when one is out of quota, and show
the tokens each provider reported. Everything here is opt-in.

## Connect a router

Add `router` to the Studio runtime configuration file (outside the project,
mode `0600`, passed to `nexus studio --runtime-config`):

```json
{"router": {"url": "http://127.0.0.1:7771", "allow_private": true}}
```

`allow_private` is required for plain HTTP. For a router behind an authenticating
proxy, add `"token_env": "ROUTER_TOKEN"`; in team mode each user's own value of
that variable is used, never the host's. The URL is server-owned: browsers and
models cannot point Studio at another router.

### Desktop app

The desktop app needs no flag:

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
captured when the app was built, so its "registry is old" warning is expected; a
router you run yourself and refresh from OpenClaw stays current. It never sends
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
**Provider override** offer **Auto (model router)**. Agents keep a fixed provider.

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
the invocation traces (`GET /api/workbench/usage?days=30`). Claude Code,
Antigravity and the Anthropic API report `input_tokens`/`output_tokens`; they are
now stored as `prompt_tokens`/`completion_tokens` (cache reads/writes count as
prompt tokens) so the existing per-run token summary sees them too. Codex does not
report usage and shows "not reported". Money and subscription quota are not measured.

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

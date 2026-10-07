# Integrations in Nexus ARC

One config section builds the whole external surface. All types resolve
through `AdapterRegistry` (`nexus/adapters/registry.py`), so adding an
integration means adding one loader branch plus constructor kwargs — no
other wiring.

```yaml
# nexus.yaml
storage:
  type: file
  base_path: ./data
git:
  type: github
  repo: myorg/myrepo
notifications:
  - type: slack
    token: ${SLACK_TOKEN}
    default_channel: "#ops"
  - type: discord
    webhook_url: ${DISCORD_WEBHOOK_URL}
  - type: openclaw
    bridge_url: ${NEXUS_OPENCLAW_BRIDGE_URL}
  # Telegram chat goes through interactive_clients below, not notifications.
interactive_clients:
  - type: telegram-interactive-http
  - type: discord-interactive-http
ai:
  - type: opencode             # free account-auth default
  - type: copilot
```

## Matrix

| Kind | Types | Notes |
|---|---|---|
| `storage` | `file`, `postgres`, `redis` | `base_path` / `connection_string` |
| `git` | `github`, `gitlab` | API transport default; `cli` opt-in |
| `notifications` | `slack`, `discord`, `openclaw` | Telegram chat goes through the interactive plugin, not a channel |
| `interactive_clients` | `telegram-interactive-http`, `discord-interactive-http` | two-way chat surfaces for the bot |
| `ai` | `opencode`, `copilot`, `gemini`, `codex`, `claude`, `openai` | opencode needs no API key |
| `transcription` | `whisper` | local STT |
| `transport` | `memory`, `mqtt` | event bus; `NEXUS_TRANSPORT`, `MQTT_HOST` |
| `gateway` | ABC + echo/telegram adapters | one file per chat transport |

## Adding one

1. Implement the channel/plugin class with plain `__init__` kwargs.
2. Add a branch to the matching `_load_builtin_*` in `nexus/adapters/registry.py`.
3. Add a row here and a registry test in `tests/test_adapters.py`.

"""Show when Studio agents use a CLI worker's built-in web search.

Run with: PYTHONPATH=. python examples/nexus-bot/src/studio_native_web.py
No provider, network or model calls are made.

In shared mode without research.search, an agent granted web_search on a Codex,
Claude Code or OpenCode worker searches with the worker's own web tools.
"""

import tempfile

from nexus.core.studio import StudioService


def exercise(studio):
    names = {name.rsplit(":", 1)[1]: name for name in studio.research.names}
    agent_tools = [names["web_search"], names["web_fetch"], names["finance"]]

    remaining, native = studio.research.native_web("claude-code", agent_tools)
    assert native and remaining == [names["finance"]]

    kept, native_api = studio.research.native_web("openai", agent_tools)
    assert not native_api and kept == agent_tools

    studio.research.search = {"provider": "tavily", "token_env": "TAVILY_API_KEY"}
    _, native_configured = studio.research.native_web("codex", agent_tools)
    assert not native_configured
    return {
        "cli_worker_native": native,
        "api_native": native_api,
        "with_account": native_configured,
    }


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-native-web-") as workspace:
        app = StudioService(workspace)
        try:
            print(f"Verified native web selection: {exercise(app)}")
        finally:
            app.close()


if __name__ == "__main__":
    main()

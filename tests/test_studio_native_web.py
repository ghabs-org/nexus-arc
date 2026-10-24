"""Workflow agents with search tools use a CLI worker's own web search when no account is set."""

import pytest

from tests.test_studio_team import team as team
from tests.test_studio_team import wait


def tool(service, action):
    return next(name for name in service.research.names if name.endswith(":" + action))


def run_workflow(service, provider):
    service.save_definition(
        "workflows",
        {
            "id": "news-flow",
            "name": "News Flow",
            "steps": [{"id": "step_1", "agent_type": "searcher", "prompt": "Find {task}"}],
        },
    )
    run = service.start_run(
        {
            "kind": "workflow",
            "workflow_id": "news-flow",
            "task": "release news",
            "provider": provider,
            "requester_nexus_id": "alice",
        }
    )
    return wait(service, run)


@pytest.fixture
def searcher(team):
    service, _, _, _, calls = team
    service.save_definition(
        "agents",
        {
            "id": "searcher",
            "name": "Searcher",
            "instructions": "Search the web",
            "tool_ids": [tool(service, "web_search"), tool(service, "web_fetch")],
        },
    )
    return service, calls


def test_cli_worker_searches_natively_without_search_account(searcher):
    service, calls = searcher
    assert run_workflow(service, "codex")["status"] == "completed"
    assert calls[-1].metadata["worker_tools"] == "web"
    assert "registered tools" not in calls[-1].prompt


def test_configured_search_account_keeps_studio_tools(searcher):
    service, calls = searcher
    service.research.search = {"provider": "tavily", "token_env": "TAVILY_API_KEY"}
    run_workflow(service, "codex")
    assert calls[-1].metadata["worker_tools"] is None
    assert "registered tools" in calls[-1].prompt and "web_search" in calls[-1].prompt


def test_worker_without_tool_levels_keeps_studio_tools(searcher):
    service, calls = searcher
    run_workflow(service, "agy")
    assert calls[-1].metadata["worker_tools"] is None
    assert "registered tools" in calls[-1].prompt


def test_native_web_keeps_other_tools_and_fetch_only_agents(team):
    service = team[0]
    search, fetch = tool(service, "web_search"), tool(service, "web_fetch")
    finance = tool(service, "finance")
    assert service.research.native_web("claude-code", [search, fetch, finance]) == (
        [finance],
        True,
    )
    assert service.research.native_web("codex", [fetch]) == ([fetch], False)
    assert service.research.native_web("openai", [search]) == ([search], False)


def test_toolset_search_tools_use_the_worker_and_pass_activation_checks(team):
    service, _, _, _, calls = team
    from nexus.core.studio_activation_setup import local_prerequisites

    service.catalog.save(
        "toolsets",
        {
            "id": "discovery",
            "name": "Discovery",
            "data": {"tools": [tool(service, "web_search"), tool(service, "web_fetch")]},
        },
    )
    service.save_definition(
        "agents",
        {
            "id": "searcher",
            "name": "Searcher",
            "instructions": "Search the web",
            "provider": "claude-code",
            "toolset_ids": ["discovery"],
        },
    )
    assert run_workflow(service, "claude-code")["status"] == "completed"
    assert calls[-1].metadata["worker_tools"] == "web"
    assert "registered tools" not in calls[-1].prompt

    agent = service.definition("agents", "searcher")
    assert local_prerequisites(service, "agents:searcher", agent)["issues"] == []
    toolset = service.catalog.get("toolsets", "discovery")
    assert local_prerequisites(service, "toolsets:discovery", toolset)["issues"] == []
    api_agent = {**agent, "spec": {**agent["spec"], "provider": "openai"}}
    assert local_prerequisites(service, "agents:searcher", api_agent)["issues"] == [
        "tool_setup_required:web_search"
    ]

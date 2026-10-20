"""Additional health checks contact read operations and never persist private bodies."""

import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_resource_health import StudioResourceHealth, probe_action
from tests.test_studio_build_lifecycle import proposal, seed
from tests.test_studio_external_content import peer as peer
from tests.test_studio_resource_health import request
from tests.test_studio_teams import graph as graph


def test_teams_health_reads_metadata_without_sending(graph):
    app, connector, seen, _, _ = graph
    build = seed(
        app,
        proposal(
            resources=[
                {
                    "kind": "connectors",
                    "id": "review-channel",
                    "name": "Review channel",
                    "data": connector["data"],
                }
            ]
        ),
    )
    key = "connectors:" + next(
        r["id"] for r in build["proposal"]["resources"] if r["kind"] == "connectors"
    )
    health = StudioResourceHealth(app.resource_lifecycle)
    assert not seen
    assert health.inspect(build, {"key": key, "managed": True})["supported"]
    assert not seen
    result = health.check(build["id"], request(build, key))
    assert result["status"] == "passed" and result["action"] == "status"
    assert len(seen) == 1 and seen[0][0] == "GET" and "$select=id,displayName" in seen[0][1]
    assert "Fixture text" not in json.dumps(result)
    assert app.catalog.get("connectors", key.split(":")[1])["data"]["enabled"] is False


def test_disabled_external_agent_can_probe_card_without_enabling_or_sending(
    tmp_path, peer, monkeypatch
):
    monkeypatch.setenv("PEER_TOKEN", "synthetic-peer-token")
    app = StudioService(tmp_path)
    try:
        build = seed(
            app,
            proposal(
                resources=[
                    {
                        "kind": "externalagents",
                        "id": "peer",
                        "name": "Remote reviewer",
                        "data": {
                            "url": peer["origin"] + "/rpc",
                            "card_path": "/card",
                            "token_env": "PEER_TOKEN",
                            "allow_private": True,
                        },
                    }
                ]
            ),
        )
        key = "externalagents:" + next(
            r["id"] for r in build["proposal"]["resources"] if r["kind"] == "externalagents"
        )
        result = StudioResourceHealth(app.resource_lifecycle).check(
            build["id"], request(build, key)
        )
        assert result["status"] == "passed" and result["action"] == "agent_card"
        assert len(peer["requests"]) == 1 and not peer["messages"]
        assert not app.catalog.get("externalagents", key.split(":")[1])["data"]["enabled"]
    finally:
        app.close()


@pytest.mark.parametrize(
    "kind,action",
    [
        ("http", "fetch"),
        ("github", "list_issues"),
        ("gitlab", "list_issues"),
        ("mongodb", "inspect_schema"),
        ("powerbi", "read"),
        ("teams", "status"),
    ],
)
def test_only_actual_selected_read_actions_are_health_supported(kind, action):
    assert probe_action("connectors:test", {"data": {"type": kind, "actions": [action]}}) == action
    if kind != "http":
        assert (
            probe_action("connectors:test", {"data": {"type": kind, "actions": ["send"]}}) is None
        )
    for unsafe in ["slack", "telegram", "discord", "openclaw", "bedrock", "bedrock-flow", "nuclia"]:
        assert (
            probe_action(
                "connectors:test",
                {"data": {"type": unsafe, "actions": ["status", "invoke", "query"]}},
            )
            is None
        )

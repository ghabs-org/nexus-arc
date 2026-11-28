"""Operational reads preserve account scope and never execute providers or probes."""

import json

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_overview import StudioOverview
from tests.test_studio_build_lifecycle import seed
from tests.test_studio_feedback import completed
from tests.test_studio_team import team as team


def report(studio, identity, owner, status):
    run = completed(studio, identity, owner)
    studio._write(studio._path("runs", identity), {**run, "status": status})
    studio._write(
        studio._path("evaluation-reports", identity),
        {
            "id": identity,
            "owner_id": owner,
            "name": identity,
            "passed": 1,
            "total": 1,
            "results": [],
            "suite_revision": 1,
        },
    )


def test_overview_http_scope_private_feedback_partial_reports_and_no_execution(team):
    studio, _, manager, request, calls = team
    for identity, status in [("alice", "completed"), ("bob", "failed")]:
        report(studio, identity, identity, status)
        studio.collaboration.feedback(
            {"run_id": identity, "rating": 1, "comment": "PRIVATE_NOTE"},
            {"nexus_id": identity, "role": "builder"},
        ) if status == "completed" else None
        studio._write(
            studio._path("builds", identity),
            {"id": identity, "owner_id": identity, "status": "draft", "revision": 1},
        )
    path = "/api/studio/overview"
    assert request(path)[0] == 401
    code, alice = request(path, user="alice")
    assert code == 200
    assert alice["feedback"] == {"total": 1, "helpful": 1, "needs_improvement": 0}
    assert alice["evaluations"]["completed"] == 1
    assert [r["id"] for r in alice["evaluations"]["items"]] == ["alice"]
    assert [b["id"] for b in alice["builds"]["items"]] == ["alice"]
    for identity, account, content, date in [
        ("old", "alice", "PRIVATE_OLD", "2026-01-01"),
        ("new", "alice", "PRIVATE_NEW", "2026-01-02"),
        ("foreign", "bob", "PRIVATE_FOREIGN", "2026-01-03"),
    ]:
        studio._write(
            studio._path("configuration-releases", identity),
            {
                "id": identity,
                "owner_id": account,
                "created_at": date,
                "metadata": {"build_id": "alice"},
                "document": {
                    "definitions": [
                        {
                            "collection": "agents",
                            "id": "review",
                            "document": {"instructions": content},
                        }
                    ]
                },
            },
        )
    comparison = request(path, user="alice")[1]["builds"]["items"][0]["comparison"]
    assert comparison["from_release_id"] == "old" and comparison["to_release_id"] == "new"
    assert comparison["changed"] == ["agents:review"]
    assert "PRIVATE" not in json.dumps(comparison)
    bob = request(path, user="bob")[1]
    assert bob["feedback"]["total"] == 0 and bob["evaluations"]["completed"] == 0
    assert bob["evaluations"]["items"][0]["status"] == "failed"
    owner = request(path, user="owner")[1]
    assert owner["scope"] == "workspace" and owner["evaluations"]["total"] == 2
    assert owner["feedback"]["total"] == 0
    assert "PRIVATE_NOTE" not in json.dumps(owner)
    assert request(path, user="viewer")[1]["builds"]["total"] == 0
    assert calls == []
    manager.revoked.add("alice")
    assert request(path, user="alice")[0] == 401


def test_comparison_includes_enable_changes_but_never_values():
    overview = StudioOverview(None)
    before = {
        "resources": [
            {
                "kind": "connectors",
                "id": "remote",
                "name": "Remote",
                "data": {"enabled": False, "token_env": "PRIVATE_REFERENCE"},
            }
        ]
    }
    after = json.loads(json.dumps(before))
    after["resources"][0]["data"]["enabled"] = True
    after["definitions"] = [
        {"collection": "agents", "id": "review", "document": {"instructions": "PRIVATE_PROMPT"}}
    ]
    before["definitions"] = [{"collection": "agents", "id": "old", "document": {}}]
    result = overview.comparison(
        [{"id": "new", "document": after}, {"id": "old", "document": before}]
    )
    assert result["added"] == ["agents:review"]
    assert result["removed"] == ["agents:old"]
    assert result["changed"] == ["connectors:remote"]
    assert "PRIVATE" not in json.dumps(result)
    assert overview.comparison([]) is None


@pytest.fixture
def local_studio(tmp_path):
    studio = StudioService(tmp_path)
    yield studio
    studio.close()


def test_saved_health_uses_existing_lifecycle_without_probes(local_studio, monkeypatch):
    studio = local_studio
    build = seed(studio)
    from nexus.core.studio_resource_health import StudioResourceHealth

    def forbidden(*args, **kwargs):
        raise AssertionError("Overview must never perform a health check")

    monkeypatch.setattr(StudioResourceHealth, "check", forbidden)
    item = StudioOverview(studio).summary()["builds"]["items"][0]
    assert item["id"] == build["id"]
    assert item["health_status"] == "available"
    assert item["health"]["checked"] == 0
    assert item["comparison"] is None
    monkeypatch.setattr(
        studio.resource_lifecycle,
        "inspect_build",
        lambda *args: (_ for _ in ()).throw(FileNotFoundError("SECRET_PATH")),
    )
    item = StudioOverview(studio).summary()["builds"]["items"][0]
    assert item["health_status"] == "unavailable"
    assert "SECRET_PATH" not in json.dumps(item)


def test_recent_limits_do_not_truncate_totals(local_studio):
    studio = local_studio
    for index in range(23):
        report(studio, f"report-{index:02}", None, "completed")
    summary = StudioOverview(studio).summary()
    assert summary["evaluations"]["total"] == summary["evaluations"]["completed"] == 23
    assert len(summary["evaluations"]["items"]) == summary["recent_limit"] == 20

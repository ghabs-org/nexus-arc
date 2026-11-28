"""Feedback history, optimistic edits and promotion stay scoped to their owner."""

import json

import pytest

from tests.test_studio_team import team as team


def completed(studio, identity="feedback-run", owner="alice"):
    run = {
        "id": identity,
        "kind": "chat",
        "status": "completed",
        "requester_nexus_id": owner,
        "task": "Summarize the synthetic policy",
        "output": "Approved",
        "structured_content": [],
    }
    studio._write(studio._path("runs", identity), run)
    return run


def test_feedback_edit_history_and_conflict_preserve_a_single_owned_record(team):
    studio, _, manager, request, _ = team
    run = completed(studio)
    path = "/api/studio/feedback"
    assert request(path)[0] == 401
    assert request(path, {"run_id": run["id"], "rating": 1}, user="viewer")[0] == 403
    status, first = request(
        path, {"run_id": run["id"], "rating": 1, "comment": "Initial"}, user="alice"
    )
    assert status == 201 and first["revision"] == 1 and first["owner_id"] == "alice"
    for user in ("bob", "viewer", "owner"):
        assert request(path, user=user)[1]["items"] == []
    assert request(path, {"run_id": run["id"], "rating": -1, "revision": 1}, user="bob")[0] == 403
    assert request(path, {"run_id": run["id"], "rating": -1}, user="alice")[0] == 400
    assert request(path, user="alice")[1]["items"][0]["comment"] == "Initial"
    status, edited = request(
        path,
        {"run_id": run["id"], "rating": -1, "comment": "New\nComment", "revision": 1},
        user="alice",
    )
    assert status == 201 and edited["id"] == first["id"] and edited["revision"] == 2
    history = request(path, user="alice")[1]["items"]
    assert len(history) == 1 and history[0]["comment"] == "New\nComment"
    assert history[0]["rating"] == -1
    manager.revoked.add("alice")
    assert request(path, user="alice")[0] == 401


@pytest.mark.parametrize(
    "values", [{"rating": True}, {"rating": 0}, {"comment": None}, {"comment": "x" * 2001}]
)
def test_invalid_feedback_does_not_create_history(team, values):
    studio, _, _, request, _ = team
    run = completed(studio)
    assert (
        request("/api/studio/feedback", {"run_id": run["id"], "rating": 1, **values}, user="alice")[
            0
        ]
        == 400
    )
    assert request("/api/studio/feedback", user="alice")[1]["items"] == []


def test_promotion_copies_original_task_without_private_comment_or_execution(team):
    studio, policy, _, request, calls = team
    run = completed(studio)
    _, feedback = request(
        "/api/studio/feedback",
        {"run_id": run["id"], "rating": 1, "comment": "PRIVATE_COMMENT"},
        user="alice",
    )
    payload = {
        "feedback_id": feedback["id"],
        "name": "Feedback review",
        "expected": "Approved",
        "target_kind": "agent",
        "target_id": "reviewer",
    }
    policy.bindings["bob"] = "builder"
    assert request("/api/studio/feedback/promote", payload, user="bob")[0] == 403
    assert request("/api/studio/feedback/promote", payload, user="owner")[0] == 403
    status, saved = request("/api/studio/feedback/promote", payload, user="alice")
    assert status == 201 and saved["owner_id"] == "alice"
    assert saved["data"]["cases"][0]["input"] == run["task"]
    assert saved["data"]["cases"][0]["contains"] == ["Approved"]
    assert "PRIVATE_COMMENT" not in json.dumps(saved)
    assert len(studio.records("runs")) == 1 and calls == []

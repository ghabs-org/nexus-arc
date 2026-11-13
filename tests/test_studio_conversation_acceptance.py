"""A real branching handoff preserves replay across approval without repeat effects."""

import time

from nexus.tools import tool_registry
from tests.studio_conversation_acceptance import create_fixture


def test_branch_handoff_gate_tool_artifact_history_is_stable_after_resume(tmp_path):
    studio, _, _, review, counters, _ = create_fixture(tmp_path)
    try:
        before = studio.execution.snapshot(review["id"], None)
        assert before["states"]["root/skipped"] == "skipped"
        assert any(
            event["kind"] == "invocation" and event["status"] == "completed"
            for event in before["timeline"]
        )
        assert counters["effects"] == 0
        assert studio.execution.snapshot(review["id"], None) == before
        studio.graph.resume(review["id"], "approve")
        for _ in range(600):
            current = studio.run(review["id"])
            if current["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        assert current["status"] == "completed", current
        final = studio.execution.snapshot(review["id"], None)
        assert counters["effects"] == 1
        assert (
            len(
                [
                    event
                    for event in final["timeline"]
                    if event["kind"] == "branch" and event["node_id"] == "root/skipped"
                ]
            )
            == 1
        )
        assert all(event["at"] for event in final["timeline"])
        assert any(
            event.get("tool") == "acceptance:effect" and event["status"] == "completed"
            for event in final["timeline"]
        )
        assert any(
            event["kind"] == "artifact" and event["artifact_revision"] == 1
            for event in final["timeline"]
        )
        assert len(studio.execution.snapshot(review["id"], None)["timeline"]) == len(
            final["timeline"]
        )
        assert counters["effects"] == 1
    finally:
        studio.close()
        tool_registry.unregister("acceptance:effect")

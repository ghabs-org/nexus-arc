import json
import time

import pytest

from nexus.core.studio import StudioService


@pytest.fixture
def studio(tmp_path):
    service = StudioService(tmp_path)
    yield service
    service.close()


def wait(studio, run, status="completed"):
    for _ in range(500):
        record = studio.run(run["id"])
        if record["status"] not in {"running", "queued"}:
            assert record["status"] == status, record
            return record
        time.sleep(0.01)
    pytest.fail("Workflow did not finish")


def save(studio, name, graph):
    return studio.save_definition("workflows", {"name": name, "graph": {"version": 2, **graph}})


def test_map_orders_results_and_structured_references(studio):
    workflow = save(
        studio,
        "Count items",
        {
            "nodes": [
                {
                    "id": "each",
                    "kind": "map",
                    "items": {"ref": "input"},
                    "body": {
                        "version": 2,
                        "nodes": [
                            {
                                "id": "count",
                                "kind": "tool",
                                "tool_id": "text:wordcount",
                                "kwargs": {"text": {"ref": "input"}},
                            }
                        ],
                        "output_mapping": {"ref": "nodes.count"},
                    },
                }
            ],
            "output_mapping": {"ref": "nodes.each"},
        },
    )
    run = wait(
        studio,
        studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": workflow["id"],
                "task": "Count",
                "workflow_input": ["one two", "three", ""],
            }
        ),
    )
    assert json.loads(run["output"]) == [2, 1, 0]


def test_nested_workflow_switch_and_loop_limits(studio):
    child = save(
        studio,
        "Classify",
        {
            "nodes": [
                {
                    "id": "classify",
                    "kind": "switch",
                    "cases": [
                        {
                            "condition": {"left": {"ref": "input"}, "op": "gt", "right": 2},
                            "value": "large",
                        }
                    ],
                    "default": "small",
                }
            ],
            "output_mapping": {"ref": "nodes.classify"},
        },
    )
    parent = save(
        studio,
        "Parent",
        {
            "nodes": [{"id": "child", "kind": "workflow", "workflow_id": child["id"]}],
            "output_mapping": {"ref": "nodes.child"},
        },
    )
    result = wait(
        studio,
        studio.start_run(
            {
                "kind": "workflow",
                "workflow_id": parent["id"],
                "task": "Classify",
                "workflow_input": 3,
            }
        ),
    )
    assert json.loads(result["output"]) == "large"
    loop = save(
        studio,
        "Bounded loop",
        {
            "nodes": [
                {
                    "id": "loop",
                    "kind": "loop",
                    "max_iterations": 2,
                    "condition": {"left": 1, "op": "eq", "right": 1},
                    "body": {
                        "version": 2,
                        "nodes": [
                            {
                                "id": "check",
                                "kind": "switch",
                                "cases": [
                                    {
                                        "condition": {"left": 1, "op": "eq", "right": 1},
                                        "value": True,
                                    }
                                ],
                            }
                        ],
                    },
                }
            ]
        },
    )
    failed = wait(
        studio,
        studio.start_run({"kind": "workflow", "workflow_id": loop["id"], "task": "Loop"}),
        "failed",
    )
    assert "iteration limit" in failed["error"]


def test_map_approval_resume_keeps_completed_siblings(studio):
    workflow = save(
        studio,
        "Map gate",
        {
            "nodes": [
                {
                    "id": "each",
                    "kind": "map",
                    "items": ["one two", "three"],
                    "body": {
                        "version": 2,
                        "nodes": [
                            {
                                "id": "count",
                                "kind": "tool",
                                "tool_id": "text:wordcount",
                                "kwargs": {"text": {"ref": "input"}},
                            },
                            {"id": "gate", "kind": "approval", "depends_on": ["count"]},
                        ],
                        "output_mapping": {"ref": "nodes.count"},
                    },
                }
            ],
            "output_mapping": {"ref": "nodes.each"},
        },
    )
    run = wait(
        studio,
        studio.start_run({"kind": "workflow", "workflow_id": workflow["id"], "task": "Count"}),
        "waiting",
    )
    assert len(run["awaiting_approval"]) == 2
    count_before = len(studio.records("node-traces", limit=None))
    result = wait(studio, studio.graph.resume(run["id"], "approve"))
    assert json.loads(result["output"]) == [2, 1]
    assert len(studio.records("node-traces", limit=None)) == count_before


def test_rejects_unsafe_retries_and_cycles(studio):
    with pytest.raises(ValueError, match="generation-only"):
        save(
            studio,
            "Unsafe retry",
            {"nodes": [{"id": "tool", "kind": "tool", "tool_id": "text:wordcount", "retries": 1}]},
        )
    with pytest.raises(ValueError, match="cyclic"):
        save(
            studio,
            "Cycle",
            {
                "nodes": [
                    {"id": "one", "kind": "delay", "delay_seconds": 0, "depends_on": ["two"]},
                    {"id": "two", "kind": "delay", "delay_seconds": 0, "depends_on": ["one"]},
                ]
            },
        )

"""Consume geographic and grouped ruleset tools without real accounts.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_geography_rulesets.py
The geographic client is a deterministic stand-in; no external request is sent.
"""

import base64
import json
import tempfile
import time
from contextlib import closing
from pathlib import Path

from nexus.core.studio import StudioService

RULES = {
    "name": "travel_policy",
    "description": "Synthetic travel policy",
    "grouping_parameter": "country",
    "input_parameters": [{"name": "country", "type": "string", "required": True}],
    "decision_tree_groups": {
        "Italy": {
            "decision_trees": [
                {
                    "name": "Travel review",
                    "description": "Approval policy",
                    "decision_logic": "Request operator approval before booking travel.",
                }
            ]
        },
        "_default": {
            "decision_trees": [
                {
                    "name": "Default review",
                    "description": "Fallback policy",
                    "decision_logic": "Ask for the destination policy. No booking is authorized.",
                }
            ]
        },
    },
}


def exercise(studio):
    artifact = studio.catalog.save(
        "artifacts",
        {
            "name": "Travel policy",
            "data": {
                "filename": "travel-policy.json",
                "content_base64": base64.b64encode(json.dumps(RULES).encode()).decode(),
            },
        },
    )
    workflow = studio.save_definition(
        "workflows",
        {
            "name": "Travel research",
            "graph": {
                "version": 2,
                "nodes": [
                    {
                        "id": "city",
                        "kind": "tool",
                        "tool_id": next(
                            n for n in studio.research.names if n.endswith(":city_to_timezone")
                        ),
                        "kwargs": {"city": "Rome", "country_code": "IT"},
                    },
                    {
                        "id": "policy",
                        "kind": "tool",
                        "tool_id": next(iter(studio.rulesets.names)),
                        "kwargs": {
                            "artifact_id": artifact["id"],
                            "revision": 1,
                            "parameters": {"country": "ITALY"},
                        },
                    },
                ],
                "output_mapping": {
                    "city": {"ref": "nodes.city"},
                    "policy": {"ref": "nodes.policy"},
                },
            },
        },
    )
    run = studio.start_run(
        {
            "kind": "workflow",
            "workflow_id": workflow["id"],
            "task": "Research travel without booking",
        }
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = studio.run(run["id"])
        if run["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    if run["status"] != "completed":
        raise RuntimeError("Example workflow failed: " + str(run.get("error")))
    result = json.loads(run["output"])
    assert result["city"]["locations"][0]["timezone"] == "Europe/Rome"
    assert result["policy"]["group"] == "Italy"
    assert result["policy"]["source"]["artifact_revision"] == 1
    assert len(run["citations"]) == 2
    return result


def run_example(root):
    def request(method, url, headers, body, text):
        return {
            "results": [
                {
                    "name": "Rome",
                    "latitude": 41.9,
                    "longitude": 12.5,
                    "country_code": "IT",
                    "country": "Italy",
                    "timezone": "Europe/Rome",
                }
            ]
        }

    with closing(
        StudioService(
            root, research_options={"config": {"geographic": True}, "request_client": request}
        )
    ) as studio:
        result = exercise(studio)
        print(json.dumps(result, indent=2))
        print("Geographic evidence and pinned policy workflow verified; no booking performed.")
        return result


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="nexus-geographic-example-") as directory:
        run_example(Path(directory))

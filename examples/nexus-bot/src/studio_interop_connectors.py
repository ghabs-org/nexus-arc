"""Review and execute a connector bundle against an explicitly chosen test service.

Start the repository's studio-interop-connectors Postman simulation first.
Run with --url http://127.0.0.1:PORT/service. Only loopback test URLs are accepted.
Model responses are synthetic; no account or real helpdesk is represented.
"""

import argparse
import json
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def wait(studio, run, expected):
    deadline = time.monotonic() + 5
    while (record := studio.run(run["id"]))["status"] in {"queued", "running"}:
        if time.monotonic() > deadline:
            raise TimeoutError("Fixture workflow did not finish")
        time.sleep(0.01)
    assert record["status"] == expected, record
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument(
        "--fail-write", action="store_true", help="Exercise a received write returning HTTP 503"
    )
    options = parser.parse_args()
    parsed = urlparse(options.url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        parser.error("Use the loopback URL of the disposable Postman simulation")
    calls = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            assert "Use evidence only" in context.prompt
            calls.append(context)
            return AgentResult(success=True, output="fail" if options.fail_write else "Reviewed")

    def counters():
        with urllib.request.urlopen(options.url.rstrip("/") + "/stats", timeout=5) as response:
            return json.load(response)

    before = counters()
    with tempfile.TemporaryDirectory(prefix="nexus-connector-bundle-") as root:
        studio = StudioService(root, provider_factory=lambda _: Provider())
        try:
            studio.catalog.save(
                "skills",
                {"id": "guidance", "name": "Guidance", "data": {"content": "Use evidence only"}},
            )
            studio.catalog.save(
                "toolsets", {"id": "local", "name": "Local", "data": {"tools": ["text:wordcount"]}}
            )
            studio.catalog.save(
                "connectors",
                {
                    "id": "service",
                    "name": "Fixture service",
                    "data": {
                        "type": "openapi",
                        "url": options.url,
                        "allow_private": True,
                        "enabled": True,
                        "actions": ["read", "invoke"],
                        "operations": ["lookup", "record"],
                        "spec": {
                            "openapi": "3.0.3",
                            "paths": {
                                "/lookup": {
                                    "get": {
                                        "operationId": "lookup",
                                        "parameters": [
                                            {
                                                "name": "id",
                                                "in": "query",
                                                "required": True,
                                                "schema": {"type": "string"},
                                            }
                                        ],
                                    }
                                },
                                "/record": {
                                    "post": {
                                        "operationId": "record",
                                        "requestBody": {
                                            "required": True,
                                            "content": {
                                                "application/json": {"schema": {"type": "object"}}
                                            },
                                        },
                                    }
                                },
                            },
                        },
                    },
                },
            )
            source = (
                Path(__file__).parents[1] / "config/portable-connector-review.yaml"
            ).read_text()
            converted = studio.configuration.convert_interop(
                {
                    "text": source,
                    "bindings": {
                        "models": {"account": {"provider": "codex"}},
                        "skills": {"guidance": "guidance"},
                        "toolsets": {"local": "local"},
                        "tools": {"words": "text:wordcount"},
                        "connectors": {"service": "service"},
                        "operations": {
                            "service/lookup": {"operation_id": "lookup", "input": "parameters"},
                            "service/record": {"operation_id": "record", "input": "body"},
                        },
                    },
                },
                None,
            )
            assert converted["valid"], converted
            plan = studio.configuration.plan(converted["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            assert counters() == before and not calls
            run = wait(
                studio,
                studio.start_run(
                    {
                        "kind": "workflow",
                        "workflow_id": "review-flow",
                        "task": "Review",
                        "workflow_input": {"id": "7", "text": "one two"},
                    }
                ),
                "waiting",
            )
            paused = counters()
            assert paused == {"reads": before["reads"] + 1, "writes": before["writes"]}
            completed = wait(
                studio,
                studio.graph.resume(run["id"], "approve"),
                "failed" if options.fail_write else "completed",
            )
            after = counters()
            assert after == {"reads": before["reads"] + 1, "writes": before["writes"] + 1}
            assert len(calls) == 1
            if options.fail_write:
                assert studio.records("integration-effects")[0]["status"] == "attempting"
            else:
                assert json.loads(completed["output"])["result"]["saved"]
            print(
                json.dumps(
                    {
                        "failure_case": options.fail_write,
                        "conversion_requests": 0,
                        "reads": 1,
                        "writes_before_approval": 0,
                        "writes_after_approval": 1,
                        "agent_calls": 1,
                    }
                )
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

"""Exercise selected Power BI queries and agent synthesis through local HTTP.

Start postman/simulations/studio-powerbi.sim.yaml on port 49872 first.
Only a disposable workspace, synthetic credentials and deterministic AI are used.
"""

import argparse
import asyncio
import json
import os
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService

DATASET = "cfafbeb1-8037-4d0c-896e-a46fb27ff229"
WORKSPACE = "11111111-2222-3333-4444-555555555555"


def exercise(base="http://127.0.0.1:49872/powerbi"):
    selected = urlparse(base)
    if (
        selected.scheme != "http"
        or selected.hostname != "127.0.0.1"
        or selected.username
        or selected.password
    ):
        raise ValueError("This example accepts only its local HTTP simulation")
    prompts = []
    tool = None

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            if len(prompts) in {1, 3}:
                return AgentResult(
                    success=True,
                    output=json.dumps(
                        {
                            "tool_call": {
                                "name": tool,
                                "arguments": {
                                    "action": "query",
                                    "arguments": {
                                        "query": 'EVALUATE ROW("Revenue", 42, "Missing", BLANK())'
                                        if len(prompts) == 1
                                        else "fixture:table-error"
                                    },
                                },
                            }
                        }
                    ),
                )
            assert "Untrusted tool result:" in context.prompt
            assert "[Revenue]" in context.prompt and "42" in context.prompt
            return AgentResult(
                success=True,
                output=json.dumps({"final": "Revenue is 42. Missing value remains null."}),
            )

    with (
        tempfile.TemporaryDirectory(prefix="nexus-powerbi-") as directory,
        patch.dict(os.environ, {"EXAMPLE_POWERBI_KEY": "synthetic-powerbi-key"}),
    ):
        studio = StudioService(directory, provider_factory=lambda _: Provider())
        try:
            # Preserve the production path, request encoder, response decoder and bounds.
            # Only the external hostname is mapped to the isolated local simulation.
            def peer(method, url, headers, body):
                origin = "https://api.powerbi.com"
                if not url.startswith(origin + "/v1.0/myorg/"):
                    raise AssertionError("Unexpected external service")
                return studio.research.request(
                    method, base + url[len(origin) :], headers, body, private=True
                )

            studio.connections.enterprise.request_client = peer
            record = studio.catalog.save(
                "connectors",
                {
                    "name": "Revenue model",
                    "data": {
                        "type": "powerbi",
                        "dataset_id": DATASET,
                        "workspace_id": WORKSPACE,
                        "token_env": "EXAMPLE_POWERBI_KEY",
                        "actions": ["read", "query"],
                        "enabled": False,
                    },
                },
            )

            def attempts():
                response = requests.get(base + "/fixture/attempts", timeout=5)
                response.raise_for_status()
                return response.json()["attempts"]

            def call(action, arguments=None):
                return asyncio.run(
                    studio.connections.execute(
                        record["id"], {"action": action, "arguments": arguments or {}}
                    )
                )

            before = attempts()
            try:
                call("read")
            except ValueError:
                pass
            else:
                raise AssertionError("Disabled connector executed")
            assert attempts() == before
            record = studio.catalog.save(
                "connectors", {**record, "data": {**record["data"], "enabled": True}}
            )
            assert call("read")["id"] == DATASET
            result = call("query", {"query": 'EVALUATE ROW("Revenue", 42)'})
            assert result["results"][0]["tables"][0]["rows"][0] == {
                "[Revenue]": 42,
                "[Missing]": None,
            }
            assert result["informationProtectionLabel"]["name"] == "Private"
            assert (
                call("query", {"query": "fixture:empty"})["results"][0]["tables"][0]["rows"] == []
            )
            failures = [
                "top-error",
                "result-error",
                "table-error",
                "two-tables",
                "malformed",
                "reject",
                "redirect",
                "bad-json",
                "oversized",
            ]
            for case in failures:
                count = attempts()
                try:
                    call("query", {"query": "fixture:" + case})
                except ValueError as error:
                    assert "Integration action failed" in str(error)
                    assert "PRIVATE_PROVIDER_DETAIL" not in str(error)
                else:
                    raise AssertionError("Invalid or partial result reported as success: " + case)
                assert attempts() == count + 1
            tool = next(
                name for name in studio.operations.tools if name.endswith(":" + record["id"])
            )
            agent = studio.save_definition(
                "agents",
                {
                    "name": "Revenue analyst",
                    "instructions": "Use the selected dataset tool. Preserve nulls and report provider errors.",
                    "tool_ids": [tool],
                    "max_tool_calls": 1,
                },
            )
            run = studio.start_run({"agent_id": agent["id"], "task": "What is revenue?"})
            for _ in range(500):
                run = studio.run(run["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            assert run["status"] == "completed", run
            assert run["output"] == "Revenue is 42. Missing value remains null."
            assert len(prompts) == 2
            failed = studio.start_run(
                {"agent_id": agent["id"], "task": "Inspect an incomplete result"}
            )
            for _ in range(500):
                failed = studio.run(failed["id"])
                if failed["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            assert failed["status"] == "failed", failed
            assert len(prompts) == 3, "Partial rows reached model synthesis"
            assert "PRIVATE_PROVIDER_DETAIL" not in str(failed)
            # A configured token reference never falls back to another account.
            count = attempts()
            with patch.dict(os.environ, {"EXAMPLE_POWERBI_KEY": ""}):
                try:
                    call("read")
                except (PermissionError, ValueError):
                    pass
                else:
                    raise AssertionError("Missing credential did not block execution")
            assert attempts() == count
            # Personal My workspace uses the same selected dataset without a group path.
            record = studio.catalog.save(
                "connectors", {**record, "data": {**record["data"], "workspace_id": ""}}
            )
            assert call("read")["id"] == DATASET
            assert call("query", {"query": 'EVALUATE ROW("Revenue", 42)'})["results"]
            assert "synthetic-powerbi-key" not in str(
                studio.catalog.get("connectors", record["id"])
            )
            return {
                "dataset_scoped": True,
                "partial_results_rejected": len(failures),
                "agent_synthesis": True,
                "requests": attempts() - before,
                "live_verified": False,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49872/powerbi")
    print(exercise(parser.parse_args().base))

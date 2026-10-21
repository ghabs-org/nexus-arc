"""Consume Nuclia answers, private citations and artifact revisions over local HTTP.

Start the repository studio-nuclia simulation on port 49873 first.
Only synthetic credentials, a temporary workspace and deterministic AI are used.
"""

import argparse
import asyncio
import base64
import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def exercise(base="http://127.0.0.1:49873/nuclia"):
    url = urlparse(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("This example accepts only its local HTTP simulation")
    prompts = []
    tool = None

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            if len(prompts) == 1:
                return AgentResult(
                    success=True,
                    output=json.dumps(
                        {
                            "tool_call": {
                                "name": tool,
                                "arguments": {
                                    "action": "ask",
                                    "arguments": {
                                        "query": "What is the return policy?",
                                        "filter_expression": {
                                            "field": {"prop": "resource", "id": "resource"}
                                        },
                                        "prompt": {"user": "Use {context} to answer {question}"},
                                        "rephrase": False,
                                    },
                                },
                            }
                        }
                    ),
                )
            assert "Thirty days." in context.prompt and "[source:1]" in context.prompt
            return AgentResult(success=True, output='{"final":"Thirty days [source:1]."}')

    with (
        tempfile.TemporaryDirectory(prefix="nexus-nuclia-") as directory,
        patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": "synthetic-nuclia-key"}),
    ):
        studio = StudioService(directory, provider_factory=lambda _: Provider())
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_studio_handler(studio, "synthetic-nuclia-csrf")
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            record = studio.catalog.save(
                "connectors",
                {
                    "name": "Selected knowledge",
                    "data": {
                        "type": "nuclia",
                        "url": base,
                        "allow_private": True,
                        "knowledge_box": "selected",
                        "token_env": "EXAMPLE_NUCLIA_KEY",
                        "enabled": True,
                    },
                },
            )

            def attempts():
                response = requests.get(base + "/fixture/attempts", timeout=5)
                response.raise_for_status()
                return response.json()["attempts"]

            def call(action, arguments):
                return asyncio.run(
                    studio.connections.execute(
                        record["id"], {"action": action, "arguments": arguments}
                    )
                )

            before = attempts()
            assert call("query", {"query": "Policy", "page": 1})["page_number"] == 1
            try:
                call("ask", {"query": "Policy"})
            except PermissionError:
                pass
            else:
                raise AssertionError("Generation was enabled implicitly")
            assert attempts() == before + 1
            record = studio.catalog.save(
                "connectors", {**record, "data": {**record["data"], "actions": ["query", "ask"]}}
            )
            tool = next(
                name for name in studio.operations.tools if name.endswith(":" + record["id"])
            )
            agent = studio.save_definition(
                "agents",
                {
                    "name": "Cited policy assistant",
                    "instructions": "Answer from the selected knowledge box. Cite only returned source references.",
                    "tool_ids": [tool],
                    "max_tool_calls": 1,
                },
            )
            run = studio.start_run({"agent_id": agent["id"], "task": "What is the return policy?"})
            for _ in range(500):
                run = studio.run(run["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            assert run["status"] == "completed", run
            assert run["output"] == "Thirty days [source:1]."
            assert len(run["citations"]) == 1 and len(run["structured_content"]) == 2
            citation = run["citations"][0]
            assert citation["original_page"] == 4 and citation["page"] == 1
            assert (
                studio.session(run["session_id"])["messages"][-1]["citations"] == run["citations"]
            )
            origin = f"http://127.0.0.1:{server.server_port}"
            path = f"/api/catalog/artifacts/{citation['artifact_id']}/preview?revision={citation['artifact_revision']}&page=1"
            preview = requests.get(origin + path, timeout=5)
            preview.raise_for_status()
            assert "Thirty days." in preview.text and "Original provider page: 4" in preview.text
            snapshot = studio.catalog.get("artifacts", citation["artifact_id"])
            studio.catalog.save(
                "artifacts",
                {
                    **snapshot,
                    "data": {
                        "filename": "changed.txt",
                        "content_base64": base64.b64encode(b"New revision").decode(),
                    },
                },
            )
            pinned = requests.get(origin + path, timeout=5)
            pinned.raise_for_status()
            assert "Thirty days." in pinned.text and "New revision" not in pinned.text
            no_context = call("ask", {"query": "fixture:empty"})
            assert no_context["status"] == "no_context" and not no_context["sources"]
            assert len(no_context["artifacts"]) == 1
            failures = ["error", "missing-citation", "reject", "redirect", "bad-json", "oversized"]
            for case in failures:
                count, artifacts = attempts(), len(studio.catalog.list("artifacts"))
                try:
                    call("ask", {"query": "fixture:" + case})
                except ValueError as error:
                    assert "Integration action failed" in str(error)
                    assert "PRIVATE_PROVIDER_DETAIL" not in str(error)
                else:
                    raise AssertionError("Invalid provider answer was accepted")
                assert attempts() == count + 1
                assert len(studio.catalog.list("artifacts")) == artifacts
            count = attempts()
            with patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": ""}):
                try:
                    call("ask", {"query": "Policy"})
                except (ValueError, PermissionError):
                    pass
                else:
                    raise AssertionError("Missing credential did not stop generation")
            assert attempts() == count
            return {
                "requests": attempts() - before,
                "failure_cases": len(failures),
                "private_revision_citations": True,
                "explicit_generation": True,
                "live_verified": False,
            }
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49873/nuclia")
    print(exercise(parser.parse_args().base))

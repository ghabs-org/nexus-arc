"""Use reviewed audit templates against the disposable local Nuclia simulation.

Start postman/simulations/studio-nuclia.sim.yaml, then run this consumer with
--base http://127.0.0.1:<port>/nuclia. No provider account is used.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from nexus.core.studio import StudioService
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor
from nexus.core.studio_nuclia_interop import POLICY


def exercise(base="http://127.0.0.1:49873/nuclia"):
    url = urlparse(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("This consumer accepts only its local HTTP simulation")

    def attempts():
        response = requests.get(base + "/fixture/attempts", timeout=5)
        response.raise_for_status()
        return response.json()["attempts"]

    source = {
        "tool_type": "python",
        "component_module": "uninstalled_nuclia_tool.nuclia_rag_tool",
        "class_name": "NucliaRagTool",
        "tool_config": {
            "base_url": base + "/api/v1",
            "kb_id": "selected",
            "token": "${EXAMPLE_NUCLIA_KEY}",
            "top_k": 10,
            "template_parameters": [
                {"name": "scope", "required": True, "context_expression": "workspace.name"}
            ],
            "audit_metadata": {
                "enabled": True,
                "fields": {
                    "scope": "{scope}",
                    "question": "{query}",
                    "environment": "local-fixture",
                },
            },
        },
    }
    with (
        tempfile.TemporaryDirectory(prefix="nexus-nuclia-audit-") as directory,
        patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": "synthetic-nuclia-key"}),
    ):
        studio = StudioService(Path(directory) / "workspace")
        try:
            before = attempts()
            converted = convert_tool_descriptor(source, policy=POLICY)
            assert any("Nuclia activity logs" in change for change in converted["adaptations"])
            assert attempts() == before
            saved = studio.catalog.save(
                "connectors",
                {
                    "id": "knowledge",
                    "name": "Audited knowledge",
                    "data": {**converted["connector"], "allow_private": True},
                },
            )
            assert not saved["data"]["enabled"]
            # This explicit local-fixture activation follows inspection of the conversion.
            saved = studio.catalog.save(
                "connectors",
                {**saved, "data": {**saved["data"], "enabled": True, "actions": ["ask", "query"]}},
            )

            def call(action="ask", **arguments):
                return asyncio.run(
                    studio.connections.execute(
                        "knowledge", {"action": action, "arguments": arguments}
                    )
                )

            answer = call(query="fixture:audit")
            assert answer["output"] == "Thirty days."
            assert "local-fixture" not in json.dumps(answer)
            assert "audit_metadata" not in json.dumps(answer)
            assert call("query", query="fixture:audit")["total"] == 2
            after = attempts()
            assert after == before + 2
            for arguments in (
                {"query": "fixture:audit", "parameters": {"scope": "SPOOFED"}},
                {"query": "fixture:audit", "audit_metadata": {"scope": "SPOOFED"}},
                {"query": "é" * 1900},
            ):
                try:
                    call(**arguments)
                except ValueError as error:
                    assert "SPOOFED" not in str(error)
                else:
                    raise AssertionError("Invalid metadata reached the provider")
            assert attempts() == after
            try:
                call(query="fixture:audit-reject")
            except ValueError as error:
                assert "PRIVATE_AUDIT_PROVIDER_DETAIL" not in str(error)
            else:
                raise AssertionError("Provider rejection was accepted")
            assert attempts() == after + 1
            saved["data"]["template"]["audit_metadata"]["enabled"] = False
            studio.catalog.save("connectors", saved)
            assert call(query="What is the return policy?")["output"] == "Thirty days."
            assert attempts() == before + 4
            return {
                "provider_requests": 4,
                "search_and_answer": True,
                "preflight_rejections": 3,
                "no_action_replay": True,
                "live_verified": False,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49873/nuclia")
    print(json.dumps(exercise(parser.parse_args().base), indent=2))

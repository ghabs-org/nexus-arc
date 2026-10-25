"""Review and run Nuclia answer presentation against the existing local simulation.

Run with --base http://127.0.0.1:<port>/nuclia. Only disposable workspace data
and synthetic credentials are used; no live model or provider account is called.
"""

import argparse
import asyncio
import json
import os
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from nexus.core.studio import StudioService
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor
from nexus.core.studio_nuclia_interop import POLICY


def exercise(base="http://127.0.0.1:49873/nuclia", *, mode="default", artifact=True):
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
            "tool_name": "Knowledge",
            "base_url": base + "/api/v1",
            "kb_id": "selected",
            "token": "${EXAMPLE_NUCLIA_KEY}",
            "top_k": 10,
            "citations": mode,
            "output_response_as_artifact": artifact,
            "output_filename_base": "policy-notes.md",
            "inline_citation_links": False,
            "include_citations_in_tool_response": True,
            "artifact_description_query_max_length": 7,
        },
    }
    with (
        tempfile.TemporaryDirectory(prefix="nexus-presentation-") as directory,
        patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": "synthetic-nuclia-key"}),
    ):
        studio = StudioService(directory)
        try:
            before = attempts()
            converted = convert_tool_descriptor(source, policy=POLICY, preserve_presentation=True)
            saved = studio.catalog.save(
                "connectors",
                {
                    "id": "knowledge",
                    "name": "Knowledge",
                    "data": {**converted["connector"], "allow_private": True},
                },
            )
            request = {
                "documents": [
                    {
                        "agent_name": "knowledge-helper",
                        "instruction": "Use evidence.",
                        "tools": [source],
                    }
                ],
                "bindings": {
                    "agents": {"knowledge-helper": {"provider": "codex"}},
                    "nuclia": {
                        "Knowledge": {
                            "connector_id": "knowledge",
                            "policy": POLICY,
                            "preserve_presentation": True,
                        }
                    },
                },
            }
            report = studio.configuration.convert_interop(request, None)
            assert report["valid"], report
            assert attempts() == before and saved["data"]["enabled"] is False
            plan = studio.configuration.plan(report["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            exported = studio.configuration.export_interop({"document": report["document"]}, None)
            assert exported["valid"]
            restored = studio.configuration.convert_interop(
                {"documents": [exported["bundle"]]}, None
            )
            assert restored["valid"] and restored["document"] == report["document"]
            saved = studio.catalog.get("connectors", "knowledge")
            studio.catalog.save("connectors", {**saved, "data": {**saved["data"], "enabled": True}})

            def call(**arguments):
                return asyncio.run(
                    studio.connections.execute(
                        "knowledge", {"action": "ask", "arguments": arguments}
                    )
                )

            result = call(query="fixture:presentation")
            assert result["output"] == (
                "Thirty days.[^policy]" if mode == "llm_footnotes" else "Thirty days."
            )
            assert len(result["citations"]) == (0 if mode == "none" else 1)
            assert len(result["artifacts"]) == int(artifact) + len(result["citations"])
            if artifact:
                file = result["response_artifact"]
                assert file["filename"] == "policy-notes.md"
                record = studio.artifacts.revision(file["id"], file["revision"])
                body = studio.artifacts.get(record).decode()
                assert record["description"] == "Nuclia answer for: fixture"
            else:
                body = result["response_content"]
            if mode != "none":
                assert " [1]" in body and "[^policy]" not in body
                citation = result["citations"][0]
                assert citation["url"].startswith("/api/catalog/artifacts/")
                assert citation["url"] in body
                pinned = studio.artifacts.revision(
                    citation["artifact_id"], citation["artifact_revision"]
                )
                assert studio.artifacts.get(pinned).decode().endswith("Thirty days.")
            else:
                assert "**Sources:**" not in body
            after = attempts()
            assert after == before + 1
            for arguments in (
                {
                    "query": "fixture:presentation",
                    "citations": "none" if mode != "none" else "default",
                },
                {
                    "query": "fixture:presentation",
                    "answer_options": {"output_response_as_artifact": not artifact},
                },
            ):
                try:
                    call(**arguments)
                except ValueError:
                    pass
                else:
                    raise AssertionError("A caller changed reviewed presentation")
            assert attempts() == after
            try:
                call(query="fixture:presentation-reject")
            except ValueError as error:
                assert "PRIVATE_PROVIDER_DETAIL" not in str(error)
            else:
                raise AssertionError("Provider failure was accepted")
            assert attempts() == after + 1
            return {
                "provider_requests": 2,
                "mode": mode,
                "answer_file": artifact,
                "plain_output_preserved": True,
                "no_action_replay": True,
                "live_verified": False,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49873/nuclia")
    parser.add_argument("--mode", choices=["default", "none", "llm_footnotes"], default="default")
    parser.add_argument("--inline", action="store_true")
    args = parser.parse_args()
    print(json.dumps(exercise(args.base, mode=args.mode, artifact=not args.inline), indent=2))

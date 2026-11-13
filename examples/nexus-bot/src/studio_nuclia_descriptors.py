"""Review a Nuclia descriptor, bind a native connection and invoke local HTTP.

Start postman/simulations/studio-nuclia.sim.yaml on port 50950, then run:
PYTHONPATH=. python examples/nexus-bot/src/studio_nuclia_descriptors.py
Only synthetic credentials and a disposable workspace are used.
"""

import argparse
import asyncio
import copy
import json
import os
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from nexus.core.studio import StudioService
from nexus.core.studio_nuclia_descriptors import convert_tool_descriptor
from nexus.core.studio_nuclia_interop import POLICY


def source(base):
    return {
        "tool_type": "python",
        "component_module": "uninstalled_nuclia_tool.nuclia_rag_tool",
        "class_name": "NucliaRagTool",
        "tool_config": {
            "tool_name": "CompanyKnowledge",
            "base_url": base + "/api/v1",
            "kb_id": "selected",
            "token": "${EXAMPLE_NUCLIA_KEY}",
            "top_k": 10,
            "user_prompt": "Use {context} to answer {question}",
        },
    }


def configure(studio, base, *, preserve_retrieval=False):
    descriptor = source(base)
    converted = convert_tool_descriptor(
        descriptor, policy=POLICY, preserve_retrieval=preserve_retrieval
    )
    studio.catalog.save(
        "connectors",
        {
            "id": "knowledge",
            "name": "Imported knowledge",
            "data": {**converted["connector"], "allow_private": True},
        },
    )
    return {
        "documents": [
            {
                "agent_name": "knowledge-helper",
                "instruction": "Use company evidence.",
                "tools": [descriptor],
            }
        ],
        "bindings": {
            "agents": {"knowledge-helper": {"provider": "codex"}},
            "nuclia": {
                "CompanyKnowledge": {
                    "connector_id": "knowledge",
                    "policy": POLICY,
                    **({"preserve_retrieval": True} if preserve_retrieval else {}),
                }
            },
        },
    }


def exercise(base="http://127.0.0.1:50950/nuclia", *, preserve_retrieval=False):
    url = urlparse(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("This example accepts only its local HTTP simulation")

    def attempts():
        response = requests.get(base + "/fixture/attempts", timeout=5)
        response.raise_for_status()
        return response.json()["attempts"]

    with (
        tempfile.TemporaryDirectory(prefix="nexus-nuclia-descriptor-") as directory,
        patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": "synthetic-nuclia-key"}),
    ):
        studio = StudioService(directory)
        try:
            request = configure(studio, base, preserve_retrieval=preserve_retrieval)
            before = attempts()
            report = studio.configuration.convert_interop(request, None)
            assert report["valid"], report
            assert report["adaptations"][0]["policy"] == POLICY
            if preserve_retrieval:
                assert "may increase usage" in json.dumps(report["adaptations"])
            assert not studio.definitions("agents")
            assert attempts() == before
            plan = studio.configuration.plan(report["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            record = studio.catalog.get("connectors", "knowledge")
            assert not record["data"]["enabled"]
            exported = studio.configuration.export_interop({"document": report["document"]}, None)
            assert exported["valid"], exported
            restored = studio.configuration.convert_interop(
                {"documents": [exported["bundle"]]}, None
            )
            assert restored["valid"] and restored["document"] == report["document"]

            # Activation remains a separate, explicit operator action.
            studio.catalog.save(
                "connectors", {**record, "data": {**record["data"], "enabled": True}}
            )

            def call(arguments):
                return asyncio.run(
                    studio.connections.execute(
                        "knowledge", {"action": "ask", "arguments": arguments}
                    )
                )

            answer = call(
                {
                    "query": "fixture:expanded"
                    if preserve_retrieval
                    else "What is the return policy?"
                }
            )
            assert answer["output"] == "Thirty days."
            assert len(answer["artifacts"]) == (3 if preserve_retrieval else 2)
            if preserve_retrieval:
                assert {item["augmentation_type"] for item in answer["sources"]} == {
                    "neighbouring_paragraphs",
                    "hierarchy",
                }
                assert {item["text"] for item in answer["sources"]} == {
                    "Return unused items within thirty days.",
                    "Returns policy",
                }
                for item in answer["sources"]:
                    artifact = studio.artifacts.revision(
                        item["artifact_id"], item["artifact_revision"]
                    )
                    assert studio.artifacts.get(artifact).decode().endswith(item["text"])
            after = attempts()
            assert after == before + 1
            try:
                call({"query": "Policy?", "top_k": 1})
            except ValueError:
                pass
            else:
                raise AssertionError("A caller replaced fixed settings")
            assert attempts() == after
            if preserve_retrieval:
                try:
                    call({"query": "Policy?", "rag_strategies": []})
                except ValueError:
                    pass
                else:
                    raise AssertionError("A caller removed fixed context strategies")
                assert attempts() == after
            try:
                call({"query": "fixture:reject"})
            except ValueError:
                pass
            else:
                raise AssertionError("Provider rejection was accepted")
            assert attempts() == after + 1

            rejected = copy.deepcopy(request)
            rejected["documents"][0]["tools"][0]["tool_config"]["api_key"] = (
                "SYNTHETIC_INLINE_SECRET"
            )
            failure = studio.configuration.convert_interop(rejected, None)
            assert not failure["valid"] and failure["document"] is None
            assert "SYNTHETIC_INLINE_SECRET" not in json.dumps(failure)
            return {
                "requests": 2,
                "reviewed_binding": True,
                "no_action_replay": True,
                "live_verified": False,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:50950/nuclia")
    parser.add_argument("--preserve-retrieval", action="store_true")
    args = parser.parse_args()
    print(json.dumps(exercise(args.base, preserve_retrieval=args.preserve_retrieval), indent=2))

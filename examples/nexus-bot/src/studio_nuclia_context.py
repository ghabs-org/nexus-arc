"""Consume server-bound query context against the local Nuclia dependency simulation."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from nexus.core.studio import StudioService
from nexus.core.studio_nuclia_interop import POLICY, convert_template_settings


def exercise(base="http://127.0.0.1:49873/nuclia"):
    with (
        tempfile.TemporaryDirectory(prefix="nexus-nuclia-context-") as directory,
        patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": "synthetic-nuclia-key"}),
    ):
        studio = StudioService(Path(directory) / "workspace")
        try:
            template = convert_template_settings(
                {
                    "template_parameters": [
                        {
                            "name": "workspace",
                            "type": "string",
                            "required": True,
                            "context_expression": "workspace.name",
                        }
                    ],
                    "prompt_rephrasing": {"template": "Workspace: {workspace}; Question: {query}"},
                },
                policy=POLICY,
            )["template"]
            connector = studio.catalog.save(
                "connectors",
                {
                    "name": "Server context",
                    "data": {
                        "type": "nuclia",
                        "url": base,
                        "knowledge_box": "selected",
                        "token_env": "EXAMPLE_NUCLIA_KEY",
                        "allow_private": True,
                        "actions": ["query"],
                        "template": template,
                        "enabled": True,
                    },
                },
            )
            with urlopen(base + "/fixture/attempts", timeout=3) as response:
                before = json.load(response)["attempts"]
            result = asyncio.run(
                studio.connections.execute(
                    connector["id"],
                    {
                        "action": "query",
                        "arguments": {"query": "What is the return policy?"},
                    },
                )
            )
            assert result["resources"]["resource"]["fields"]["f/document"]["paragraphs"]
            try:
                asyncio.run(
                    studio.connections.execute(
                        connector["id"],
                        {
                            "action": "query",
                            "arguments": {
                                "query": "What is the return policy?",
                                "parameters": {"workspace": "another-workspace"},
                            },
                        },
                    )
                )
            except ValueError:
                pass
            else:
                raise AssertionError("Caller overrode server context")
            with urlopen(base + "/fixture/attempts", timeout=3) as response:
                assert json.load(response)["attempts"] == before + 1
            data = {**connector["data"], "actions": ["query", "ask"]}
            data.pop("template")
            studio.catalog.save("connectors", {**connector, "data": data})
            augmented = asyncio.run(
                studio.connections.execute(
                    connector["id"],
                    {
                        "action": "ask",
                        "arguments": {"query": "fixture:augmented"},
                    },
                )
            )
            source = augmented["sources"][0]
            assert source["text"] == "Neighbouring evidence." and source["original_page"] == 6
            assert source["augmentation_type"] == "neighbouring_paragraphs"
            pinned = studio.artifacts.revision(source["artifact_id"], source["artifact_revision"])
            assert b"Neighbouring evidence." in studio.artifacts.get(pinned)
            return {
                "server_context_used": True,
                "override_rejected_before_network": True,
                "augmented_citation_pinned": True,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    print(json.dumps(exercise()))

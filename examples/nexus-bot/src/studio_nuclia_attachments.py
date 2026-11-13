"""Browse and save cited conversation files against the local Nuclia simulation.

Start postman/simulations/studio-nuclia.sim.yaml first. All credentials and
content are synthetic. No remote account or model is contacted.
"""

import argparse
import json
import os
import tempfile
import time
from unittest.mock import patch
from urllib.parse import urlparse

from nexus.core.studio import StudioService
from nexus.core.studio_nuclia_documents import queue_original


def wait(studio, run):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        run = studio.run(run["id"])
        if run["status"] not in {"queued", "running"}:
            if run["status"] != "completed":
                raise RuntimeError(run.get("error", "Local fixture job failed"))
            return run
        time.sleep(0.01)
    raise TimeoutError("Local fixture job did not finish")


def exercise(base):
    if urlparse(base).hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("Use the local synthetic Nuclia simulation only")
    with (
        tempfile.TemporaryDirectory(prefix="nexus-nuclia-files-") as directory,
        patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": "synthetic-nuclia-key"}),
    ):
        studio = StudioService(directory)
        try:
            before = studio.research.request("GET", base + "/fixture/attempts", private=True)[
                "attempts"
            ]
            connector = studio.catalog.save(
                "connectors",
                {
                    "name": "Conversation knowledge",
                    "data": {
                        "type": "nuclia",
                        "url": base,
                        "allow_private": True,
                        "knowledge_box": "selected",
                        "token_env": "EXAMPLE_NUCLIA_KEY",
                        "actions": ["ask", "download_original"],
                        "enabled": True,
                    },
                },
            )
            tool = next(
                name for name in studio.operations.tools if name.endswith(":" + connector["id"])
            )
            source = wait(
                studio,
                studio.start_run(
                    {
                        "kind": "tool",
                        "tool": tool,
                        "kwargs": {
                            "action": "ask",
                            "arguments": {"query": "fixture:conversation"},
                        },
                    }
                ),
            )
            assert source["citations"][0]["field_id"] == "c/discussion"
            selection = {"source_run_id": source["id"], "source_index": 0}

            def submit(**values):
                return json.loads(
                    wait(studio, queue_original(studio, {**selection, **values}, None))["output"]
                )

            first = submit(operation="list", page=1)
            assert len(first["attachments"]) == 2 and first["unsupported_references"] == 1
            saved = submit(attachment_id=first["attachments"][0]["attachment_id"])
            assert submit(attachment_id=first["attachments"][0]["attachment_id"])["cached"]
            assert studio.artifacts.get(
                studio.artifacts.revision(saved["artifacts"][0]["id"], 1)
            ).endswith(b"message:one")
            referenced = submit(attachment_id=first["attachments"][1]["attachment_id"])
            assert (
                studio.artifacts.get(studio.artifacts.revision(referenced["artifacts"][0]["id"], 1))
                == b"Original policy file: Thirty days."
            )
            second = submit(operation="list", page=first["next_page"])
            assert second["next_page"] is None and len(second["attachments"]) == 1
            final = submit(attachment_id=second["attachments"][0]["attachment_id"])
            assert studio.artifacts.get(
                studio.artifacts.revision(final["artifacts"][0]["id"], 1)
            ).endswith(b"message-two")
            stored = json.dumps(studio.records("source-attachment-selections", limit=None))
            assert all(
                value not in stored
                for value in ("EPHEMERAL_SECRET", "UNRELATED_PRIVATE_MESSAGE", "untrusted.invalid")
            )
            return {
                "conversation_pages": 2,
                "saved_files": 3,
                "cached_repeat": True,
                "provider_requests": studio.research.request(
                    "GET", base + "/fixture/attempts", private=True
                )["attempts"]
                - before,
                "live_verified": False,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49873/nuclia")
    print(json.dumps(exercise(parser.parse_args().base)))

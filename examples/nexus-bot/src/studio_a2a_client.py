"""Use the real external-agent client against a local rich-result simulation.

Start Postman simulation studio-a2a-client.sim.yaml on port 49650, then run this
file with PYTHONPATH=. Optional --sql, --legacy, --url-file and --failure modes.
"""

import argparse
import base64
import json
import os
import tempfile
import time
from pathlib import Path

from nexus.core.studio import StudioService


def exercise(
    base="http://127.0.0.1:49650", *, sql=False, legacy=False, url_file=False, failure=False
):
    old = os.environ.get("NEXUS_A2A_CLIENT_FIXTURE_TOKEN")
    os.environ["NEXUS_A2A_CLIENT_FIXTURE_TOKEN"] = "synthetic-client-token"
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-a2a-client-") as directory:
            root = Path(directory)
            options = (
                {
                    "url": "sqlite:///" + str(root / "state.sqlite"),
                    "namespace": "client-example",
                    "workers": 1,
                }
                if sql
                else None
            )
            studio = StudioService(root / "workspace", state_options=options)
            try:
                suffix = "failure-" if failure else "url-" if url_file else ""
                profile = studio.catalog.save(
                    "externalagents",
                    {
                        "name": "Synthetic rich peer",
                        "data": {
                            "url": base + "/agent/" + suffix + "rpc",
                            "card_path": "/agent/" + suffix + "card",
                            "token_env": "NEXUS_A2A_CLIENT_FIXTURE_TOKEN",
                            "protocol_version": "0.3" if legacy else "1.0",
                            "enabled": True,
                            "allow_private": True,
                        },
                    },
                )
                uploaded = studio.catalog.save(
                    "artifacts",
                    {
                        "name": "Evidence",
                        "data": {
                            "filename": "evidence.txt",
                            "content_base64": base64.b64encode(
                                b"Explicitly selected local evidence"
                            ).decode(),
                        },
                    },
                )
                run = studio.start_run(
                    {
                        "kind": "external",
                        "external_agent_id": profile["id"],
                        "task": "Review selected evidence",
                        "external_input": {"city": "Rome"},
                        "artifact_ids": [uploaded["id"]],
                    }
                )
                deadline = time.monotonic() + 10
                while run["status"] in {"queued", "running"}:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Synthetic peer did not finish")
                    time.sleep(0.01)
                    run = studio.run(run["id"])
                if failure:
                    assert run["status"] == "failed", run
                    assert "PRIVATE_RECEIVER_ERROR" not in json.dumps(run)
                    assert studio.records("external-runs")[0]["status"] == "uncertain"
                else:
                    assert run["status"] == "completed", run
                    parts = run["structured_content"]
                    assert next(p["data"] for p in parts if p["type"] == "json") == {"count": 3}
                    file = next(p for p in parts if p["type"] == "artifact")
                    assert (
                        studio.artifacts.get(
                            studio.artifacts.revision(file["artifact_id"], file["revision"])
                        )
                        == b"Returned file evidence"
                    )
                    studio.external.refresh_task(run["id"], None)
                    assert (
                        len(
                            [
                                a
                                for a in studio.catalog.list("artifacts")
                                if a["id"].startswith("remote-")
                            ]
                        )
                        == 1
                    )
                assert len(studio.records("runs")) == 1
                return {
                    "backend": "sqlite" if sql else "files",
                    "version": "0.3" if legacy else "1.0",
                    "file_mode": "url" if url_file else "inline",
                    "result": run["status"],
                    "tasks_created": 1,
                }
            finally:
                studio.close()
    finally:
        if old is None:
            os.environ.pop("NEXUS_A2A_CLIENT_FIXTURE_TOKEN", None)
        else:
            os.environ["NEXUS_A2A_CLIENT_FIXTURE_TOKEN"] = old


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49650")
    for flag in ("sql", "legacy", "url-file", "failure"):
        parser.add_argument("--" + flag, action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            exercise(
                args.base,
                sql=args.sql,
                legacy=args.legacy,
                url_file=args.url_file,
                failure=args.failure,
            )
        )
    )

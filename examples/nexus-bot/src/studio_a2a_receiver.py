"""Consume framework callback recovery against a local Postman peer.

Start studio-a2a-receiver.sim.yaml on port 49652. Set a 32-byte
NEXUS_CREDENTIALS_MASTER_KEY, then run this file with PYTHONPATH=.
All state and HTTP listeners are isolated and removed afterward.
"""

import argparse
import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def exercise(base="http://127.0.0.1:49652", *, sql=False, legacy=False):
    with tempfile.TemporaryDirectory(prefix="nexus-a2a-receiver-") as directory:
        root = Path(directory)
        options = (
            {
                "url": "sqlite:///" + str(root / "state.sqlite"),
                "namespace": "receiver",
                "workers": 1,
            }
            if sql
            else None
        )
        with patch.dict(
            os.environ, {"NEXUS_CALLBACK_EXAMPLE_TOKEN": "synthetic-receiver-peer-token"}
        ):
            studio = StudioService(root / "workspace", state_options=options)
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_studio_handler(studio, "synthetic-csrf")
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                profile = studio.catalog.save(
                    "externalagents",
                    {
                        "name": "Callback peer",
                        "data": {
                            "url": base + "/agent/rpc",
                            "card_path": "/agent/card",
                            "token_env": "NEXUS_CALLBACK_EXAMPLE_TOKEN",
                            "protocol_version": "0.3" if legacy else "1.0",
                            "allow_private": True,
                            "enabled": True,
                        },
                    },
                )
                run = studio.start_run(
                    {
                        "kind": "external",
                        "external_agent_id": profile["id"],
                        "task": "Return verified result",
                        "external_callbacks": True,
                    },
                    callback_origin=f"http://127.0.0.1:{server.server_port}",
                )
                deadline = time.monotonic() + 10
                while studio.run(run["id"])["status"] in {"queued", "running"}:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Synthetic peer did not finish")
                    time.sleep(0.01)
                assert studio.run(run["id"])["status"] == "waiting", studio.run(run["id"])
                assert studio.external.callbacks.status(run["id"], None)["status"] == "pending"
                studio.external.callbacks.tick()
                result = studio.run(run["id"])
                assert (
                    result["status"] == "completed"
                    and result["output"] == "Verified callback recovery"
                ), result
                assert studio.external.callbacks.status(run["id"], None)["status"] == "verified"
                assert studio.external.callbacks.revoke(run["id"], None)["status"] == "revoked"
                return {
                    "backend": "sqlite" if sql else "files",
                    "version": "0.3" if legacy else "1.0",
                    "status": result["status"],
                }
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)
                studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49652")
    parser.add_argument("--sql", action="store_true")
    parser.add_argument("--legacy", action="store_true")
    arguments = parser.parse_args()
    print(json.dumps(exercise(arguments.base, sql=arguments.sql, legacy=arguments.legacy)))

"""Inspect public branding and passive speech readiness through real local Studio HTTP."""

import argparse
import json
import struct
import tempfile
import threading
from http.server import ThreadingHTTPServer

import requests

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def exercise():
    with tempfile.TemporaryDirectory(prefix="nexus-brand-readiness-") as workspace:
        studio = StudioService(workspace)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_studio_handler(studio, "local-readiness-csrf")
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with requests.Session() as client:
                client.trust_env = False

                def get(path):
                    response = client.get(
                        f"http://127.0.0.1:{server.server_port}" + path, timeout=5
                    )
                    response.raise_for_status()
                    return response

                favicon = get("/favicon.ico")
                assert favicon.headers["Content-Type"] == "image/x-icon"
                content = favicon.content
                assert struct.unpack_from("<HHH", content) == (0, 1, 1)
                offset = struct.unpack_from("<I", content, 18)[0]
                assert content[offset:] == get("/icon-192.png").content
                assert '<link rel="icon" href="/favicon.ico"' in get("/").text
                profiles = get("/api/studio/speech").json()["items"]
                assert all(
                    "missing_prerequisites" in p and "verification_scope" in p for p in profiles
                )
                assert not studio.records("runs") and not studio.records("speech-input-owners")
                return {
                    "favicon_served": True,
                    "existing_icon_preserved": True,
                    "profiles": profiles,
                    "runs_started": 0,
                    "live_voice_verified": False,
                }
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)
            studio.close()


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(exercise(), indent=2))

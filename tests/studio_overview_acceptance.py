"""Disposable full-app overview fixture; no provider or external service is called."""

import json
import tempfile
from http.server import ThreadingHTTPServer

from tests.studio_feedback_acceptance import create_feedback_fixture
from tests.test_studio_build_lifecycle import seed
from tests.test_studio_overview import report


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-overview-acceptance-") as root:
        studio, handler = create_feedback_fixture(root)
        seed(studio, owner="owner")
        report(studio, "overview-complete", "owner", "completed")
        report(studio, "overview-partial", "owner", "failed")
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/acceptance"}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            studio.close()


if __name__ == "__main__":
    main()

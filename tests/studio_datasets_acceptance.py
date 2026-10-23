"""Disposable full-app dataset editor fixture; no external model is configured."""

import json
import tempfile
from http.server import ThreadingHTTPServer

from nexus.core.studio_datasets import parse_dataset
from tests.studio_sharing_team_acceptance import create_fixture


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-datasets-acceptance-") as root:
        studio, handler, _ = create_fixture(root)
        studio.save_definition(
            "agents", {"id": "reviewer", "name": "Reviewer", "instructions": "Review policy"}
        )
        studio.catalog.save(
            "datasets",
            {
                "name": "Policy references",
                "requester_nexus_id": "alice",
                "data": parse_dataset(
                    {"format": "csv", "content": "input,expected\nReview policy,Approved"}
                ),
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/acceptance"}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            studio.close()


if __name__ == "__main__":
    main()

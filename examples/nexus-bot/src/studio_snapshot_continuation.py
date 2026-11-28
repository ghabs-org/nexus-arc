"""Copy an explicitly shared local conversation without invoking a model.

Run: PYTHONPATH=. python examples/nexus-bot/src/studio_snapshot_continuation.py
"""

import tempfile
import uuid

from nexus.core.studio import StudioService
from nexus.core.studio_snapshot_continuation import continue_snapshot


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-private-continuation-") as root:
        studio = StudioService(root)
        try:
            studio._write(studio._path("sessions", "source"), {
                "id": "source", "title": "Example", "owner_id": None,
                "messages": [{"role": "user", "content": "Plan a review"}, {"role": "assistant", "content": "Start with requirements"}],
            })
            snapshot = studio.collaboration.share({"session_id": "source"}, None)
            request = {"snapshot": snapshot["id"], "visibility": "private", "request_id": str(uuid.uuid4())}
            copied = continue_snapshot(studio, request)
            assert continue_snapshot(studio, request) == copied
            assert studio.session(copied["session_id"])["messages"][0]["content"] == "Plan a review"
            assert studio.records("runs") == []
            print("Private conversation copied once; no provider invoked.")
        finally:
            studio.close()


if __name__ == "__main__":
    main()

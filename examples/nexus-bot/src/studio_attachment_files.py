"""Demonstrate original-file attachments locally, without calling any model.

Run with PYTHONPATH=. python examples/nexus-bot/src/studio_attachment_files.py.
The example creates a disposable workspace and uses the shared Studio APIs.
"""

import base64
import tempfile
from pathlib import Path

from nexus.core.studio import StudioService


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-attachment-example-") as workspace:
        app = StudioService(workspace)
        try:
            content = b"Original selected file, including binary bytes: \x00\xff"
            artifact = app.catalog.save(
                "artifacts",
                {
                    "name": "Example source",
                    "data": {
                        "filename": "selected.bin",
                        "content_base64": base64.b64encode(content).decode(),
                    },
                },
            )
            payload = app.catalog.expand(
                {"task": "Inspect the selected source", "artifact_ids": [artifact["id"]]}
            )
            with app.artifacts.execution_context(payload, "codex", payload["task"]) as (
                prompt,
                metadata,
            ):
                path = Path(metadata["attachment_files"][0]["path"])
                assert path.read_bytes() == content and path.as_uri() in prompt
                print("Pinned original file is readable through its temporary prompt link.")
            assert not path.exists()
            assert not app.records("runs")
            print("Temporary copy removed; saved original retained. No provider called.")
        finally:
            app.close()


if __name__ == "__main__":
    main()

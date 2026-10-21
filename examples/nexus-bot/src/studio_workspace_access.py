"""Demonstrate Studio assistant and server workspace files APIs locally.

Run with: PYTHONPATH=. python examples/nexus-bot/src/studio_workspace_access.py
No external provider, network or model calls are made.
"""

import base64
import tempfile
from pathlib import Path

from nexus.core.studio import StudioService


def exercise(studio):
    # 1. Ask Studio assistant built-in documentation and live metadata
    help_result = studio.assistant.answer("Where are agent definitions saved?")
    assert help_result["mode"] == "local-help"
    assert "agents" in help_result["workspace"]
    assert any(s["id"] == "storage" for s in help_result["sources"])

    # 2. Upload a regular workspace file
    sample_content = b"Config and workspace data for demonstration."
    upload_res = studio.workspace_files.upload(
        {
            "root": "files",
            "path": "",
            "filename": "demo.txt",
            "content_base64": base64.b64encode(sample_content).decode(),
        }
    )
    assert upload_res["path"] == "demo.txt"
    assert upload_res["size"] == len(sample_content)

    # 3. Create a folder and upload inside it
    folder_res = studio.workspace_files.folder({"root": "files", "path": "", "name": "reports"})
    assert folder_res["created"] is True

    sub_res = studio.workspace_files.upload(
        {
            "root": "files",
            "path": "reports",
            "filename": "summary.txt",
            "content_base64": base64.b64encode(b"Quarterly report content").decode(),
        }
    )
    assert sub_res["path"] == "reports/summary.txt"

    # 4. List files
    listing = studio.workspace_files.listing("files", "")
    names = [item["name"] for item in listing["items"]]
    assert "reports" in names
    assert "demo.txt" in names

    # 5. Preview file
    preview = studio.workspace_files.preview("files", "demo.txt")
    assert preview["text"] == "Config and workspace data for demonstration."
    assert preview["revision"] == upload_res["revision"]

    # 6. Snapshot to private Library artifact
    snapshot = studio.workspace_files.snapshot(
        {"root": "files", "path": "demo.txt", "revision": preview["revision"]}
    )
    assert snapshot["artifact_id"].startswith("workspace-")
    assert snapshot["download_url"].startswith("/api/catalog/artifacts/")

    # 7. Bridge snapshot to chat payload
    chat_payload = studio.catalog.expand(
        {"task": "Summarize demo", "artifact_ids": [snapshot["artifact_id"]]}
    )
    assert len(chat_payload["artifact_ids"]) == 1

    return {
        "help_mode": help_result["mode"],
        "files_count": len(listing["items"]),
        "artifact_id": snapshot["artifact_id"],
        "revision": snapshot["artifact_revision"],
    }


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-workspace-access-") as workspace:
        app = StudioService(workspace)
        try:
            result = exercise(app)
            print(f"Verified workspace access and assistant: {result}")
        finally:
            app.close()


if __name__ == "__main__":
    main()

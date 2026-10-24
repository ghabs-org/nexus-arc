"""Demonstrate a knowledge project linked to a server folder and synced as copies.

Run with: PYTHONPATH=. python examples/nexus-bot/src/studio_knowledge_folders.py
No external provider, network or model calls are made.

In the browser, open Workspace files, choose a folder and select Link folder to
knowledge project; then use Sync now in the project after files change.
"""

import json
import tempfile
import time
from pathlib import Path

from nexus.core.studio import StudioService


def wait(studio, run):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = studio.run(run["id"])
        if current["status"] not in {"queued", "running"}:
            assert current["status"] == "completed", current
            return json.loads(current["output"])
        time.sleep(0.05)
    raise TimeoutError("Folder sync did not finish")


def exercise(studio, source):
    project = studio.knowledge.save({"name": "Handbook"}, None)
    linked = studio.knowledge.folders.link(
        project["id"], {"root": "source", "path": "docs", "exclude": ["drafts/*"]}, None
    )
    first = wait(studio, studio.knowledge.folders.sync(project["id"], None))
    assert first["added"] == 2

    (source / "docs" / "deploy.md").write_text("Deploys run every Friday from the release branch")
    second = wait(studio, studio.knowledge.folders.sync(project["id"], None))
    assert second["updated"] == 1 and second["unchanged"] == 1

    hits = studio.knowledge.search(project["id"], "Friday release", None)["items"]
    assert hits and hits[0]["source_path"] == "docs/deploy.md"
    return {
        "folder": linked["folders"][0]["path"],
        "first_sync": first["added"],
        "updated": second["updated"],
        "top_hit": hits[0]["source_path"],
    }


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-knowledge-folders-") as workspace:
        source = Path(workspace) / "source"
        (source / "docs" / "drafts").mkdir(parents=True)
        (source / "docs" / "deploy.md").write_text("Deploys run every Tuesday")
        (source / "docs" / "oncall.md").write_text("On-call rotates weekly")
        (source / "docs" / "drafts" / "idea.md").write_text("Unpublished draft")
        app = StudioService(
            Path(workspace) / "project",
            workspace_file_options={"roots": {"source": {"name": "Source", "path": str(source)}}},
        )
        try:
            print(f"Verified knowledge folder sync: {exercise(app, source)}")
        finally:
            app.close()


if __name__ == "__main__":
    main()

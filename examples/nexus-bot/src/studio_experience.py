"""Prepare an inbox and disabled local connection through framework APIs only.

No provider or external account is called. --check-local runs the guided SQLite
read check; enabling the connection and approving the workflow remain UI actions.
"""

import argparse
import sqlite3
import time
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_readiness import deployment_readiness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", required=True)
    parser.add_argument("--check-local", action="store_true")
    parser.add_argument(
        "--guide",
        choices=("status", "start", "complete", "dismiss", "reset"),
        default="status",
        help="Inspect or update the interactive Studio onboarding guide state",
    )
    args = parser.parse_args()
    root = Path(args.dir)
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root / "sample-evidence.sqlite") as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS evidence (description TEXT)")
    app = StudioService(root)
    try:
        if not app.catalog._path("connectors", "sample-evidence").exists():
            app.catalog.save(
                "connectors",
                {
                    "id": "sample-evidence",
                    "name": "Sample evidence",
                    "data": {"type": "sqlite", "path": "sample-evidence.sqlite", "enabled": False},
                },
            )
        record = app.catalog.get("connectors", "sample-evidence")
        if args.check_local:
            print(
                app.workbench.onboarding.probe(
                    record["id"], {"revision": record["revision"]}, None
                )["message"]
            )
        if not any(item["id"] == "sample-inbox-review" for item in app.definitions("workflows")):
            app.save_definition(
                "workflows",
                {
                    "id": "sample-inbox-review",
                    "name": "Sample inbox review",
                    "graph": {
                        "nodes": [
                            {"id": "approve", "name": "Review word count", "kind": "approval"},
                            {
                                "id": "count",
                                "name": "Count words",
                                "kind": "tool",
                                "tool_id": "text:wordcount",
                                "kwargs": {"text": "{task}"},
                            },
                        ],
                        "edges": [
                            {"source": "START", "target": "approve"},
                            {"source": "approve", "target": "count"},
                        ],
                    },
                },
            )
        if not any(
            item.get("workflow_definition_id") == "sample-inbox-review" for item in app.records("runs")
        ):
            run = app.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "sample-inbox-review",
                    "task": "Three sample words",
                }
            )
            deadline = time.monotonic() + 5
            while (
                app.run(run["id"])["status"] in {"running", "queued"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
        guide = (
            app.workbench.onboarding.guide_state(None)
            if args.guide == "status"
            else app.workbench.onboarding.save_guide_state({"action": args.guide}, None)
        )
        print(f"Inbox: {len(app.workbench.inbox.items(None))} pending reviews")
        print(f"Deployment checks: {len(deployment_readiness(app, None)['items'])} observations")
        print(
            f"Interactive guide: v{guide['version']} ({guide['status']}, {len(guide['steps'])} steps)"
        )
        print(
            "Open Studio → Help → Interactive guide, Approval inbox, or Connectors → Saved connections → Setup guide."
        )
    finally:
        app.close()


if __name__ == "__main__":
    main()

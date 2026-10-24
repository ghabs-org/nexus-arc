"""Small consumer of the framework Quick Build API; drafts are the default."""

import argparse
import json
import time

from nexus.adapters.ai.base import EFFORT_LEVELS
from nexus.core.studio import StudioService
from nexus.core.studio_models import default_models


def main():
    parser = argparse.ArgumentParser(description="Describe a Nexus team and review its draft")
    parser.add_argument("brief", nargs="?", help="What the agents and workflow should accomplish")
    parser.add_argument("--dir", default=".", help="Workspace for drafts and definitions")
    parser.add_argument(
        "--provider", default="codex", choices=["codex", "claude-code", "openai", "claude"]
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--effort", choices=EFFORT_LEVELS, default="")
    parser.add_argument(
        "--list-models", action="store_true", help="Show suggested models for --provider"
    )
    args = parser.parse_args()
    if args.list_models:
        for model in default_models(args.provider):
            print(f"{model['id']}\t{model['name']}")
        return
    if not args.brief:
        parser.error("brief is required")
    service = StudioService(args.dir)
    try:
        run = service.start_run(
            {
                "kind": "build",
                "task": args.brief,
                "provider": args.provider,
                "model": args.model,
                "effort": args.effort,
            }
        )
        while run["status"] in {"queued", "running"}:
            time.sleep(0.25)
            run = service.run(run["id"])
        if run["status"] != "completed":
            parser.exit(1, run.get("error", "Generation failed") + "\n")
        draft = service.builder.get(run["build_id"])
        print(json.dumps(draft["proposal"], indent=2, ensure_ascii=False))
        print(
            f"\nDraft {draft['id']} generated. Open Quick Build in Nexus Studio to review, save and test."
        )
    finally:
        service.close()


if __name__ == "__main__":
    main()

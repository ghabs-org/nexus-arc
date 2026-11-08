"""Ask a local nexus-router what it would pick and how healthy it is.

    python examples/nexus-bot/scripts/check_router_status.py [router_url] [task...]

Consumes the framework's StudioModelRouter and Studio provider mapping; sends no
credentials and starts no model request (/route only scores, /diagnostics only reads).
"""

import json
import sys

from nexus.core.studio_routing import allowed_router_providers, candidates
from nexus.core.studio_runtime import StudioModelRouter

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:7771"
    task = " ".join(sys.argv[2:]) or "Fix the failing unit test in the parser"
    router = StudioModelRouter({"url": url, "allow_private": True})
    diagnostics = router.diagnostics()
    ready = ["codex", "claude-code"]  # pretend these Studio providers are signed in
    decision = router.route(None, task, allowed_router_providers(ready))
    print(
        json.dumps(
            {
                "warnings": diagnostics.get("warnings", []),
                "decision_id": decision.get("decision_id"),
                "selected_model": decision["selected_model"],
                "studio_candidates": candidates(decision, ready),
                "explain": decision.get("explain", {}).get("rules_triggered"),
            },
            indent=2,
        )
    )

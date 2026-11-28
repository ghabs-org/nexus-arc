"""Ask nexus-router what it would pick and how healthy it is (dry run: nothing is recorded).

    NEXUS_ROUTER_URL=http://127.0.0.1:7771 [NEXUS_ROUTER_TOKEN=...] \
        python examples/nexus-bot/scripts/check_router_status.py [task...]

Consumes the framework's RouterClient (the client ARC workflow agents, the
coordinator and Studio share) and Studio's provider mapping. Sends no provider
credential and starts no model request.
"""

import json
import sys

from nexus.core.router_client import RouterClient
from nexus.core.studio_routing import allowed_router_providers, candidates

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or "Fix the failing unit test in the parser"
    router = RouterClient.from_env(default_url="http://127.0.0.1:7771")
    diagnostics = router.diagnostics(None)
    ready = ["codex", "claude-code"]  # pretend these Studio providers are signed in
    decision = router.route(
        None, task, allowed_providers=allowed_router_providers(ready), dry_run=True
    )
    print(
        json.dumps(
            {
                "router": router.url,
                "warnings": diagnostics.get("warnings", []),
                "selected_model": decision["selected_model"],
                "studio_candidates": candidates(decision, ready),
                "explain": (decision.get("explain") or {}).get("rules_triggered"),
            },
            indent=2,
        )
    )

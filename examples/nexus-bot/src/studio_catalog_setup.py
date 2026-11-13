"""Browse plugin and Studio setup metadata without installing or enabling anything.

PYTHONPATH=. python examples/nexus-bot/src/studio_catalog_setup.py
"""

import json
import tempfile
from contextlib import closing

from nexus.core.studio import StudioService
from nexus.plugins import PluginKind, PluginRegistry, PluginSetup


def run_example():
    registry = PluginRegistry()

    def selected_provider(config):
        raise RuntimeError("This example only inspects metadata; it never creates a provider")

    registry.register_factory(
        PluginKind.AI_PROVIDER,
        "selected-provider",
        "1.0.0",
        selected_provider,
        setup=PluginSetup(
            billing="Selected subscription or API account",
            requirements=("Explicit user account and chosen provider",),
            configuration_template={"credential_env": "YOUR_PROVIDER_TOKEN"},
        ),
    )
    plugin = registry.catalog()[0]
    with tempfile.TemporaryDirectory(prefix="nexus-catalog-example-") as root:
        with closing(StudioService(root)) as studio:
            listing = studio.recipes.list()
            rows = listing["items"] + listing["official_items"]
            assert len(rows) == 27 and not any(r["live_verified"] for r in rows)
            result = {
                "plugin": plugin,
                "integrations": [
                    {
                        "id": r["id"],
                        "billing": r["setup_metadata"]["billing"],
                        "readiness": r["readiness"]["state"],
                    }
                    for r in rows
                ],
            }
            print(json.dumps(result, indent=2))
            return result


if __name__ == "__main__":
    run_example()

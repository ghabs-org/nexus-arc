"""Serve the framework's responsive workspace, including mobile navigation, the approval inbox, setup guides and installation.

The same Studio UI adapts to phones, tablets and desktop browsers. For access
from a separate phone, configure authenticated team hosting over HTTPS; a phone's
localhost address does not point to the computer running this example.
"""

import os

from nexus.studio import serve_studio


def main():
    serve_studio(
        os.environ.get("NEXUS_STUDIO_DIR", "."),
        port=int(os.environ.get("NEXUS_STUDIO_PORT", "5003")),
        host=os.environ.get("NEXUS_STUDIO_HOST", "127.0.0.1"),
        team_config=os.environ.get("NEXUS_STUDIO_TEAM_CONFIG"),
        state_dir=os.environ.get("NEXUS_STUDIO_STATE_DIR"),
    )


if __name__ == "__main__":
    main()

"""Use the framework's public signing-identity preflight without exporting keys."""

import json

from nexus.core.studio_desktop_release import developer_identities

if __name__ == "__main__":
    print(json.dumps({"developer_id_application_certificates": len(developer_identities())}))

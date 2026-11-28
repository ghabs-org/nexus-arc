"""Check cleanup host identity in two independent installed Python processes.

No workspace, Docker request, account, environment value or machine identifier is
exported. Run with the same installed runtime used by the selected Studio host.
"""

import json
import subprocess
import sys


def exercise():
    command = [
        sys.executable,
        "-c",
        "from nexus.core.studio_container_recovery import host_fingerprint; "
        "print(host_fingerprint())",
    ]
    identities = [
        subprocess.run(command, capture_output=True, check=True, timeout=15).stdout.strip()
        for _ in range(2)
    ]
    assert len(identities[0]) == 64 and identities[0] == identities[1], (
        "Cleanup host identity changed across independent processes"
    )
    return {"independent_processes": 2, "host_identity_stable": True}


if __name__ == "__main__":
    print(json.dumps(exercise(), sort_keys=True))

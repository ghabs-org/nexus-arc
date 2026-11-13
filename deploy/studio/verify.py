"""Read-only checks against a published Studio origin. Never runs agents or writes.

Use NEXUS_STUDIO_CHECK_SESSION for an existing Nexus session, not a password.
TLS verification is mandatory; --ca-file supports a private certificate authority.
"""

from __future__ import annotations

import argparse
import json
import os

from nexus.core.studio_deployment import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin")
    parser.add_argument("--ca-file")
    args = parser.parse_args()
    try:
        report = verify(
            args.origin, session=os.getenv("NEXUS_STUDIO_CHECK_SESSION"), ca_file=args.ca_file
        )
    except Exception:
        report = {
            "passed": False,
            "error": "Verification failed. Check HTTPS, hostname, routing and account configuration.",
        }
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Consume the framework's read-only verifier for a personal hosted workspace."""

import argparse
import json
import os

from nexus.core.studio_deployment import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("origin", help="Your Studio HTTPS origin")
    parser.add_argument("--ca-file", help="Trusted private CA file, if used")
    args = parser.parse_args()
    report = verify(
        args.origin, session=os.getenv("NEXUS_STUDIO_CHECK_SESSION"), ca_file=args.ca_file
    )
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

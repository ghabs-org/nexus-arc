"""Inspect an explicit Studio/proxy deployment pair without modifying it.

Run on the selected Docker daemon host. Reports contain status and hashed
identities, not container names, paths, credentials or ledger contents.
"""

import argparse
import json

from nexus.core.studio_host_checks import verify_proxy_layout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("studio_container")
    parser.add_argument("proxy_container")
    parser.add_argument("--docker-context", required=True)
    args = parser.parse_args()
    report = verify_proxy_layout(
        args.studio_container, args.proxy_container, docker_context=args.docker_context
    )
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Check a private Studio report without contacting a server or running agents.

From the repository root:
PYTHONPATH=. python examples/nexus-bot/src/studio_evaluation_gate.py report.json
"""

import argparse
import json

from nexus.core.studio_evaluation_gate import check_report, read_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report")
    parser.add_argument("--threshold", type=float)
    args = parser.parse_args()
    try:
        result = check_report(read_report(args.report), threshold=args.threshold)
    except ValueError:
        print("Invalid evaluation report; inspect the private source file.")
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

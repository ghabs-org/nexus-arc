"""Read-only audit of local plan claims; never promotes acceptance records.

Run explicitly: python tests/studio_completion_audit.py [--require-complete].
The frozen audit documents are operator-owned and may be absent in a source-only install.
"""

import argparse
import json
import re
from pathlib import Path


def audit(directory):
    directory = Path(directory)
    inventory = json.loads((directory / "STUDIO-REQUIREMENTS.json").read_text())
    records = inventory["requirements"]
    errors = []
    identities = [row["id"] for row in records]
    if len(identities) != len(set(identities)):
        errors.append("Duplicate requirement identities")
    if any(type(row.get("acceptance_complete")) is not bool for row in records):
        errors.append("Every acceptance flag must be explicitly boolean")
    accepted = sum(row.get("acceptance_complete") is True for row in records)
    opened = [row["id"] for row in records if row.get("acceptance_complete") is not True]
    plan = (directory / "STUDIO-IMPLEMENTATION-PLAN.md").read_text()
    expected = f"{accepted} records have local acceptance and {len(opened)} remain unaccepted."
    if expected not in plan:
        errors.append("Plan totals differ from the frozen requirement inventory")
    milestones = {}
    for number in range(1, 9):
        clauses = [r for r in records if r["id"].startswith(f"M{number:02}-")]
        done = sum(r.get("acceptance_complete") is True for r in clauses)
        milestones[str(number)] = {"accepted": done, "total": len(clauses)}
        line = next((s for s in plan.splitlines() if s.startswith(f"| {number}.")), "")
        columns = line.split("|")
        if len(columns) < 5 or columns[3].strip() != f"{done}/{len(clauses)}":
            errors.append(f"Milestone {number} table count differs from its requirement flags")
    checkpoints = {}
    for name in (
        "STUDIO-M7-CATALOG-FINAL-CHECKPOINT.json",
        "STUDIO-M8-EXIT-CHECKPOINT.json",
        "STUDIO-M9-DESKTOP-CHECKPOINT.json",
    ):
        checkpoint = json.loads((directory / name).read_text())
        number = checkpoint["milestone"]
        related = [r for r in records if r.get("milestone") == number]
        checkpoints[str(number)] = checkpoint.get("milestone_complete") is True
        if checkpoints[str(number)] and any(not r.get("acceptance_complete") for r in related):
            errors.append(f"Milestone {number} claims completion with open requirements")
        if number == 9 and not checkpoints["9"]:
            line = next((s for s in plan.splitlines() if s.startswith("| 9.")), "")
            if re.search(r"\|\s*Locally accepted\s*\|", line):
                errors.append("Desktop table claims acceptance while its delivery gate is open")
    # Added scope has separate delivery gates outside the frozen 135 records.
    for number, label in ((10, "MANIFEST"), (11, "AUTHORING"), (12, "OPENCLAW")):
        path = directory / f"STUDIO-M{number}-{label}-CHECKPOINT.json"
        if not path.exists():
            checkpoints[str(number)] = False
            continue
        checkpoint = json.loads(path.read_text())
        checkpoints[str(number)] = checkpoint.get("milestone_complete") is True
    complete = not opened and all(checkpoints.values()) and not errors
    if inventory.get("completion_declared") is True and not complete:
        errors.append("Whole-plan completion is declared without all acceptance gates")
    return {
        "format": "nexus-studio-completion-audit/v1",
        "scope": "Recorded-claim consistency only; this command does not run acceptance tests or certify runtime behavior.",
        "total": len(records),
        "accepted": accepted,
        "open": len(opened),
        "open_ids": opened,
        "milestones": milestones,
        "checkpoints_complete": checkpoints,
        "plan_complete": complete,
        "inconsistencies": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", type=Path, default=Path(__file__).resolve().parents[1] / "docs")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    result = audit(args.docs)
    print(json.dumps(result, indent=2))
    return (
        1
        if result["inconsistencies"] or args.require_complete and not result["plan_complete"]
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())

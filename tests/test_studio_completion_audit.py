"""Completion claims must follow records, including separate desktop delivery gates."""

import json

import pytest

from tests.studio_completion_audit import audit


@pytest.fixture
def documents(tmp_path):
    records = [
        {"id": "M07-EXIT", "milestone": 7, "acceptance_complete": True},
        {"id": "M08-EXIT", "milestone": 8, "acceptance_complete": False},
    ]
    (tmp_path / "STUDIO-REQUIREMENTS.json").write_text(
        json.dumps(
            {
                "requirements": records,
                "completion_declared": False,
            }
        )
    )
    lines = ["1 records have local acceptance and 1 remain unaccepted."]
    for number in range(1, 9):
        count = "1/1" if number == 7 else "0/1" if number == 8 else "0/0"
        lines.append(f"| {number}. Milestone | Partial local acceptance | {count} | Open |")
    lines.append(
        "| 9. Desktop | Incomplete prototype | Separate gate: open | Build/install pending |"
    )
    (tmp_path / "STUDIO-IMPLEMENTATION-PLAN.md").write_text("\n".join(lines))
    for number, name in ((7, "CATALOG-FINAL"), (8, "EXIT"), (9, "DESKTOP")):
        (tmp_path / f"STUDIO-M{number}-{name}-CHECKPOINT.json").write_text(
            json.dumps(
                {
                    "milestone": number,
                    "milestone_complete": number == 7,
                }
            )
        )
    return tmp_path


def test_partial_results_never_declare_plan_complete(documents):
    result = audit(documents)
    assert not result["inconsistencies"]
    assert result["open_ids"] == ["M08-EXIT"] and not result["plan_complete"]


def test_rejects_aggregate_promotion_while_underlying_records_stay_open(documents):
    path = documents / "STUDIO-M8-EXIT-CHECKPOINT.json"
    path.write_text(json.dumps({"milestone": 8, "milestone_complete": True}))
    errors = audit(documents)["inconsistencies"]
    assert "Milestone 8 claims completion with open requirements" in errors


def test_detects_stale_counts_and_false_desktop_acceptance(documents):
    path = documents / "STUDIO-IMPLEMENTATION-PLAN.md"
    path.write_text(
        path.read_text()
        .replace("1 remain unaccepted", "0 remain unaccepted")
        .replace("Incomplete prototype", "Locally accepted")
    )
    errors = audit(documents)["inconsistencies"]
    assert "Plan totals differ from the frozen requirement inventory" in errors
    assert "Desktop table claims acceptance while its delivery gate is open" in errors


def test_detects_desktop_checkpoint_contradicting_open_plan_gate(documents):
    path = documents / "STUDIO-M9-DESKTOP-CHECKPOINT.json"
    path.write_text(
        json.dumps(
            {
                "milestone": 9,
                "milestone_complete": True,
                "whole_plan_complete": True,
                "pending": ["Windows/Linux startup"],
            }
        )
    )
    result = audit(documents)
    assert not result["plan_complete"]
    assert "Milestone 9 claims completion with pending items" in result["inconsistencies"]
    assert "Milestone 9 checkpoint claims whole-plan completion" in result["inconsistencies"]
    assert (
        "Desktop checkpoint claims completion while plan delivery gate is open"
        in result["inconsistencies"]
    )

"""Minimal eval harness for workflow tools and agent replies.

Cases are plain dicts so suites live comfortably in YAML/JSON::

    {"name": "wordcount", "kind": "tool",
     "tool": "text:wordcount", "args": ["a b c"], "expect_output": 3}
    {"name": "greeting", "kind": "text", "input": "hi",
     "expect_contains": ["hello"], "reference": "hello there"}

Text similarity is ROUGE-1 F1 (unigram overlap) — a cheap relatedness signal,
not a quality proof. Reports print a one-page summary; ``run_suite`` returns
the machine-readable result.
"""

from __future__ import annotations

import asyncio
import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def rouge1_f1(candidate: str, reference: str) -> float:
    """Unigram F1 overlap between two strings (0.0-1.0)."""
    cand = str(candidate or "").split()
    ref = str(reference or "").split()
    if not cand or not ref:
        return 0.0
    overlap = 0
    remaining = list(ref)
    for token in cand:
        if token in remaining:
            overlap += 1
            remaining.remove(token)
    if overlap == 0:
        return 0.0
    precision = overlap / len(cand)
    recall = overlap / len(ref)
    return 2 * precision * recall / (precision + recall)


@dataclass
class CaseResult:
    name: str
    passed: bool
    score: float = 0.0
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def evaluate_tool_case(case: dict[str, Any], registry: Any) -> CaseResult:
    """Run one tool case against a registry with ``call(name, *args)``."""
    name = str(case.get("name") or "unnamed")
    try:
        result = asyncio.run(registry.call(case["tool"], *case.get("args", [])))
    except Exception as exc:
        return CaseResult(name=name, passed=False, detail=f"call crashed: {exc}")
    if not result.ok:
        return CaseResult(name=name, passed=False, detail=f"tool failed: {result.error}")
    output = result.output
    if "expect_output" in case and output != case["expect_output"]:
        return CaseResult(
            name=name, passed=False, detail=f"expected {case['expect_output']!r}, got {output!r}"
        )
    for snippet in case.get("expect_contains") or []:
        if str(snippet) not in str(output):
            return CaseResult(name=name, passed=False, detail=f"missing {snippet!r} in {output!r}")
    return CaseResult(name=name, passed=True, score=1.0)


def evaluate_text_case(output: str, case: dict[str, Any]) -> CaseResult:
    """Score a reply: required substrings plus optional ROUGE-1 vs reference."""
    name = str(case.get("name") or "unnamed")
    for snippet in case.get("expect_contains") or []:
        if str(snippet) not in str(output or ""):
            return CaseResult(name=name, passed=False, detail=f"missing {snippet!r}")
    score = 1.0
    reference = case.get("reference")
    if reference:
        score = rouge1_f1(output, reference)
        threshold = float(case.get("min_f1", 0.5))
        if score < threshold:
            return CaseResult(
                name=name, passed=False, score=score,
                detail=f"rouge1 {score:.2f} < {threshold}",
            )
    return CaseResult(name=name, passed=True, score=score)


def run_suite(
    cases: list[dict[str, Any]],
    registry: Any | None = None,
    respond: Callable[[str], str] | None = None,
    judge_provider: Any | None = None,
    workspace: Any = "/tmp",
) -> dict[str, Any]:
    """Run tool/text/judge cases; returns cases + summary (passed/total/mean_f1)."""
    from nexus.tools import tool_registry as default_registry

    results: list[CaseResult] = []
    for case in cases:
        kind = str(case.get("kind") or "tool")
        if kind == "tool":
            results.append(evaluate_tool_case(case, registry or default_registry))
        elif kind == "text":
            if respond is None:
                results.append(
                    CaseResult(name=str(case.get("name")), passed=False, detail="no respond fn")
                )
            else:
                try:
                    results.append(evaluate_text_case(respond(str(case.get("input") or "")), case))
                except Exception as exc:
                    results.append(
                        CaseResult(name=str(case.get("name")), passed=False, detail=str(exc))
                    )
        elif kind == "judge":
            if respond is None or judge_provider is None:
                results.append(
                    CaseResult(
                        name=str(case.get("name")),
                        passed=False,
                        detail="judge cases need respond + judge_provider",
                    )
                )
            else:
                try:
                    results.append(
                        judge_text(
                            respond(str(case.get("input") or "")),
                            case,
                            judge_provider,
                            workspace,
                        )
                    )
                except Exception as exc:
                    results.append(
                        CaseResult(name=str(case.get("name")), passed=False, detail=str(exc))
                    )
        else:
            results.append(
                CaseResult(name=str(case.get("name")), passed=False, detail=f"unknown kind {kind}")
            )
    passed = sum(1 for r in results if r.passed)
    return {
        "cases": [
            {"name": r.name, "passed": r.passed, "score": round(r.score, 3), "detail": r.detail}
            for r in results
        ],
        "summary": {
            "passed": passed,
            "total": len(results),
            "mean_score": round(statistics.fmean([r.score for r in results]), 3) if results else 0.0,
        },
    }


def format_report(result: dict[str, Any]) -> str:
    """One-page text report for a suite result."""
    summary = result["summary"]
    lines = [
        f"Eval: {summary['passed']}/{summary['total']} passed "
        f"(mean score {summary['mean_score']})",
        "",
    ]
    for case in result["cases"]:
        mark = "PASS" if case["passed"] else "FAIL"
        lines.append(f"[{mark}] {case['name']} ({case['score']}) {case['detail']}")
    return "\n".join(lines)


def judge_text(
    output: str,
    case: dict[str, Any],
    provider: Any,
    workspace: Any = "/tmp",
) -> CaseResult:
    """Score a reply with a judge provider (``Score:``/``Reasoning:`` protocol).

    The case carries ``judge_rubric`` (what good looks like) and
    ``judge_min_score`` (0-10 scale, default 7). Provider failures fail open
    as unscored, never as passed.
    """
    from pathlib import Path as _Path

    from nexus.adapters.ai.base import ExecutionContext

    name = str(case.get("name") or "unnamed")
    rubric = str(case.get("judge_rubric") or "").strip()
    if not rubric:
        return CaseResult(name=name, passed=False, detail="no judge_rubric")
    threshold = float(case.get("judge_min_score", 7))
    prompt = (
        "You are an impartial judge. Score the RESPONSE against the RUBRIC on a 0-10 scale.\n"
        f"RUBRIC: {rubric}\n\nRESPONSE:\n{output}\n\n"
        "Reply in exactly this format:\nScore: <number>\nReasoning: <one sentence>"
    )
    try:
        import asyncio as _asyncio

        result = _asyncio.run(
            provider.execute_agent(
                ExecutionContext(
                    agent_name="eval-judge", prompt=prompt, workspace=_Path(str(workspace))
                )
            )
        )
    except Exception as exc:
        return CaseResult(name=name, passed=False, score=0.0, detail=f"judge failed: {exc}")
    if not result.success:
        return CaseResult(name=name, passed=False, score=0.0, detail="judge provider failed")
    score: float | None = None
    reasoning = ""
    for line in (result.output or "").splitlines():
        text = line.strip()
        if text.lower().startswith("score:"):
            try:
                score = float(text.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        elif text.lower().startswith("reasoning:"):
            reasoning = text.split(":", 1)[1].strip()
    if score is None:
        return CaseResult(name=name, passed=False, score=0.0, detail="judge gave no score")
    normalized = max(0.0, min(1.0, score / 10.0))
    if score >= threshold:
        return CaseResult(name=name, passed=True, score=normalized, detail=reasoning)
    return CaseResult(
        name=name, passed=False, score=normalized,
        detail=f"score {score} < {threshold}. {reasoning}".strip(),
    )

"""Eval harness scoring and reporting."""


def test_tool_case_pass_and_fail():
    from nexus.eval import run_suite
    from nexus.tools import ToolRegistry

    registry = ToolRegistry()
    registry.register("t:echo", lambda s: s)
    result = run_suite(
        [
            {"name": "ok", "kind": "tool", "tool": "t:echo", "args": ["hi"],
             "expect_output": "hi"},
            {"name": "bad", "kind": "tool", "tool": "t:echo", "args": ["hi"],
             "expect_output": "bye"},
            {"name": "missing", "kind": "tool", "tool": "t:nope", "args": []},
        ],
        registry=registry,
    )
    assert result["summary"] == {"passed": 1, "total": 3, "mean_score": 0.333}


def test_text_case_contains_and_rouge():
    from nexus.eval import evaluate_text_case, rouge1_f1

    assert rouge1_f1("the cat sat", "the cat sat") == 1.0
    assert rouge1_f1("zzz", "aaa bbb") == 0.0
    ok = evaluate_text_case(
        "hello world", {"name": "g", "expect_contains": ["hello"], "reference": "hello world"}
    )
    assert ok.passed and ok.score == 1.0
    bad = evaluate_text_case(
        "hello world", {"name": "g", "expect_contains": ["bye"]}
    )
    assert not bad.passed


def test_suite_text_with_respond_and_report():
    from nexus.eval import format_report, run_suite

    result = run_suite(
        [{"name": "g", "kind": "text", "input": "hi",
          "expect_contains": ["HELLO"], "reference": "HELLO"}],
        respond=lambda prompt: "HELLO",
    )
    assert result["summary"]["passed"] == 1
    report = format_report(result)
    assert "1/1 passed" in report


class _JudgeProvider:
    def __init__(self, reply):
        self._reply = reply

    async def execute_agent(self, context):
        from nexus.core.models import AgentResult

        return AgentResult(success=True, output=self._reply)


def test_judge_pass_and_fail():
    from nexus.eval import judge_text

    case = {"name": "tone", "judge_rubric": "Be concise.", "judge_min_score": 7}
    ok = judge_text("hi", case, _JudgeProvider("Score: 9\nReasoning: crisp."))
    assert ok.passed is True and ok.score == 0.9
    bad = judge_text("hi", case, _JudgeProvider("Score: 3\nReasoning: rambling."))
    assert bad.passed is False and bad.score == 0.3


def test_judge_no_score_or_rubric():
    from nexus.eval import judge_text

    assert judge_text("hi", {"name": "x"}, _JudgeProvider("Score: 9")).passed is False
    result = judge_text(
        "hi",
        {"name": "x", "judge_rubric": "Be nice."},
        _JudgeProvider("Lovely stuff, no score here."),
    )
    assert result.passed is False




def test_run_suite_judge_kind():
    from nexus.eval import run_suite

    case = {
        "kind": "judge",
        "name": "tone",
        "input": "hi",
        "judge_rubric": "Be concise.",
        "judge_min_score": 7,
    }
    result = run_suite([case], respond=lambda prompt: "hi", judge_provider=_JudgeProvider("Score: 9\nReasoning: crisp."))
    assert result["summary"] == {"passed": 1, "total": 1, "mean_score": 0.9}
    missing = run_suite([dict(case, name="x")], respond=lambda prompt: "hi")
    assert missing["cases"][0]["passed"] is False

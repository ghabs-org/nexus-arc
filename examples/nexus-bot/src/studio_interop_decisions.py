"""Consume typed external decisions, reviewed releases and optional local HTTP fixtures."""

import argparse
import json
import tempfile
import time
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from nexus.adapters.decisions.errors import DecisionCallError
from nexus.adapters.decisions.jev import ask_strict
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler

SOURCE = """kind: workflow
name: decision-review
description: Typed ticket triage with explicit review fallback.
spec:
  appConfig:
    decision:
      model_alias: triage
      timeout_seconds: 2
    tools:
      - tool_type: builtin
        tool_name: count_words
    nodes:
      - id: triage
        type: decision
        input: {ticket: '{{workflow.input.ticket}}'}
        questions:
          team:
            type: choice
            instructions: Which team should review this ticket?
            criteria:
              billing: Payments and invoices
              technical: Bugs and outages
              other: Any other question
          urgent:
            type: boolean
            instructions: Does this ticket need immediate attention?
          detail:
            type: score
            instructions: How much useful detail does this ticket contain?
            criteria:
              - low: Incomplete
              - high: Complete
        route_on: team
        routes: {billing: billing, technical: technical}
        default: general
        min_confidence: 0.8
        escalate: review
        escalation_mode: handoff
        on_provider_error: escalate
      - id: billing
        type: tool
        tool_name: count_words
        depends_on: [triage]
        input: {text: one two}
      - id: technical
        type: tool
        tool_name: count_words
        depends_on: [triage]
        input: {text: one two three}
      - id: general
        type: tool
        tool_name: count_words
        depends_on: [triage]
        input: {text: one}
      - id: review
        type: tool
        tool_name: count_words
        depends_on: [triage]
        input: {text: one two three four}
    output_mapping:
      decision: '{{triage.output}}'
      count:
        coalesce:
          - '{{billing.output}}'
          - '{{technical.output}}'
          - '{{general.output}}'
          - '{{review.output}}'
"""


def fixture_client(mode, calls):
    def classify(state, questions, **kwargs):
        calls.append(state)
        if mode == "unauthorized":
            raise DecisionCallError("unauthorized")
        if mode == "unavailable":
            raise DecisionCallError("unreachable")
        answers = {}
        for name, question in questions.items():
            if question["type"] == "noul":
                answers[name] = {"type": "noul", "noul": 0.8}
            elif question["type"] == "choice":
                answers[name] = {
                    "type": "choice",
                    "choice": "billing",
                    "confidence": 0.2 if mode == "low-confidence" else 0.9,
                    "probabilities": {
                        key: 1 if key == "billing" else 0 for key in question["criteria"]
                    },
                }
            else:
                levels = question["criteria"]
                answers[name] = {
                    "type": "score",
                    "score": len(levels) - 1,
                    "confidence": 0.9,
                    "probabilities": {
                        str(i): 1 if i == len(levels) - 1 else 0 for i in range(len(levels))
                    },
                    "legend": {str(i): level for i, level in enumerate(levels)},
                }
        return answers

    return classify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Empty disposable Studio with a synthetic decision client",
    )
    parser.add_argument(
        "--case",
        choices=["success", "low-confidence", "unavailable", "unauthorized"],
        default="success",
    )
    parser.add_argument(
        "--http", help="Explicit loopback Postman simulation decision endpoint; never a cloud URL"
    )
    parser.add_argument(
        "--native",
        action="store_true",
        help="Use native OpenAI Decisions shapes against --http only",
    )
    parser.add_argument(
        "--typesafe",
        action="store_true",
        help="Use direct TypeSafe System One provider binding",
    )
    args = parser.parse_args()
    if args.native and args.typesafe:
        parser.error("Select at most one of --native or --typesafe")
    calls = []
    client = fixture_client(args.case, calls)
    if args.native and not args.http:
        parser.error("--native requires the explicit local Postman simulation URL in --http")
    if args.http:
        parts = urlsplit(args.http)
        if (
            parts.scheme != "http"
            or parts.hostname != "127.0.0.1"
            or not parts.port
            or parts.username
            or parts.password
        ):
            parser.error("Use an explicit loopback-only HTTP fixture URL")
        if args.native:
            from nexus.adapters.decisions.openai import ask_strict as native_decision
        elif args.typesafe:
            from nexus.adapters.decisions.typesafe import ask_strict as native_decision
        else:
            native_decision = ask_strict
        client = partial(
            native_decision,
            endpoint=args.http,
            api_key="synthetic-fixture-key",
            model="gpt-6-luna" if args.native else ("jev-1.13" if args.typesafe else args.case),
        )
    backend = "openai" if args.native else ("typesafe" if args.typesafe else "openrouter")
    model = "gpt-6-luna" if args.native else ("jev-1.13" if args.typesafe else args.case)
    bindings = {
        "decisions": {
            "triage": {
                "backend": backend,
                "model": model,
            }
        },
        "tools": {"count_words": "text:wordcount"},
    }
    with tempfile.TemporaryDirectory(prefix="nexus-interop-decisions-") as directory:
        studio = StudioService(Path(directory), workbench_options={"decision_client": client})
        try:
            if args.serve:
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), make_studio_handler(studio, "interop-decision-fixture")
                )
                print(
                    f"Synthetic decision Studio: http://127.0.0.1:{server.server_port}/", flush=True
                )
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                return
            report = studio.configuration.convert_interop({"text": SOURCE, "bindings": bindings}, None)
            assert report["valid"], report
            assert not calls and not studio.records("runs")
            plan = studio.configuration.plan(report["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            run = studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "decision-review",
                    "task": "Synthetic ticket triage",
                    "workflow_input": {
                        "ticket": args.case if args.native else "Synthetic local invoice question"
                    },
                }
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                run = studio.run(run["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            if args.case == "unauthorized":
                assert run["status"] == "failed" and "unauthorized" in run["error"], run
            else:
                assert run["status"] == "completed", run
                output = json.loads(run["output"])
                expected = "billing" if args.case == "success" else "review"
                assert output["decision"]["selected_branch"] == expected
                assert output["count"] == (2 if expected == "billing" else 4)
                assert all(
                    run["graph_nodes"].get("root/" + node)
                    == ("completed" if node == expected else "skipped")
                    for node in ["billing", "technical", "general", "review"]
                )
            exported = studio.configuration.export_interop({}, None)
            assert exported["valid"], exported
            reimported = studio.configuration.convert_interop({"documents": [exported["bundle"]]}, None)
            assert reimported["document"] == report["document"], reimported
            print(
                "Typed decision conversion, reviewed apply, branch isolation and export/reimport verified with synthetic data."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

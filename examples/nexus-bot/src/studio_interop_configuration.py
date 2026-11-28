"""Consume external conversion, reviewed native releases and portable exports."""

import argparse
import json
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler

SOURCE = """kind: agent
name: reviewer
description: Review a supplied result without any external action.
spec:
  systemPrompt: Return a JSON review flag for the supplied word count.
  modelProvider: [subscription]
  inputModes: [text]
  outputModes: [text]
  skills:
    - name: Evidence review
      description: Review a supplied count using provided evidence only.
      tags: [review, evidence]
  toolsets: [local]
  additionalConfigurations:
    request_timeout_seconds: 20
    max_llm_calls_per_task: 2
    output_schema: {type: object, properties: {reviewed: {type: boolean}}, required: [reviewed]}
---
kind: workflow
name: count-review
description: Count words locally and review their result.
spec:
  appConfig:
    tools:
      - tool_type: builtin
        tool_name: count_words
    nodes:
      - id: count
        type: tool
        tool_name: count_words
        input:
          text: '{{workflow.input.text}}'
      - id: review
        type: agent
        agent_name: reviewer
        depends_on: [count]
        input:
          message: 'Review word count {{count.output}}'
    output_mapping:
      count: '{{count.output}}'
      review: '{{review.output}}'
"""
BINDINGS = {
    "models": {"subscription": {"provider": "codex"}},
    "tools": {"count_words": "text:wordcount"},
}


CONTROL_SOURCE = """kind: workflow
name: control-review
description: Map entries, repeat a review and select one local branch.
spec:
  appConfig:
    tools:
      - tool_type: builtin
        tool_name: count_words
    nodes:
      - id: each
        type: map
        node: count_one
        items: '{{workflow.input.entries}}'
        concurrency_limit: 2
      - id: count_one
        type: tool
        tool_name: count_words
        when: "'{{_map_item}}' != ''"
        input:
          text: '{{_map_item}}'
      - id: each_again
        type: map
        node: count_one
        items: '{{workflow.input.entries}}'
        concurrency_limit: 2
      - id: repeat
        type: loop
        node: review_step
        condition: '{{_loop_iteration}} < 1'
        max_iterations: 3
      - id: review_step
        type: tool
        tool_name: count_words
        input:
          text: '{{workflow.input.review}}'
      - id: choose
        type: switch
        cases:
          - condition: "'{{workflow.input.route}}' == 'short'"
            node: short
        default: long
      - id: short
        type: tool
        tool_name: count_words
        depends_on: [choose]
        input:
          text: one
      - id: long
        type: tool
        tool_name: count_words
        depends_on: [choose]
        input:
          text: one two three
    output_mapping:
      counts: '{{each.output}}'
      counts_again: '{{each_again.output}}'
      loop: '{{repeat.output}}'
      last: '{{review_step.output}}'
      route: '{{choose.output}}'
      answer:
        coalesce:
          - '{{short.output}}'
          - '{{long.output}}'
"""


POLICY_SOURCE = """kind: agent
name: reviewer
spec:
  systemPrompt: Return a JSON review flag without taking external actions.
  modelProvider: [api-demo]
---
kind: workflow
name: policy-review
spec:
  appConfig:
    retry_strategy:
      limit: 1
      retry_policy: OnFailure
      backoff: {duration: 1ms, factor: 2, max_duration: 2ms}
    tools:
      - tool_type: builtin
        tool_name: count_words
    on_exit:
      always: cleanup
      on_success: notify
    nodes:
      - id: review
        type: agent
        agent_name: reviewer
      - id: cleanup
        type: tool
        tool_name: count_words
        retry_strategy: {limit: 0}
        input: {text: '{{workflow.status}}'}
      - id: notify
        type: tool
        tool_name: count_words
        retry_strategy: {limit: 0}
        input: {text: '{{workflow.status}}'}
    output_mapping:
      review: '{{review.output}}'
      cleanup: '{{cleanup.output}}'
      notify: '{{notify.output}}'
"""


class SyntheticProvider:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        return AgentResult(success=True, output='{"reviewed":true}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Open an empty disposable Studio for browser import checks",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--controls",
        action="store_true",
        help="Exercise imported map/loop/switch semantics with local tools",
    )
    modes.add_argument(
        "--policies",
        action="store_true",
        help="Exercise bounded retries and success/always exit handlers with a synthetic API provider",
    )
    args = parser.parse_args()

    class RetryProvider(SyntheticProvider):
        calls = 0

        async def execute_agent(self, context):
            self.calls += 1
            if self.calls == 1:
                return AgentResult(success=False, output="", error="Synthetic reported failure")
            return await super().execute_agent(context)

    provider = RetryProvider() if args.policies else SyntheticProvider()
    with tempfile.TemporaryDirectory(prefix="nexus-interop-config-") as directory:
        studio = StudioService(Path(directory), provider_factory=lambda _: provider)
        try:
            if args.serve:
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0), make_studio_handler(studio, "interop-config-fixture")
                )
                print(f"Synthetic Studio: http://127.0.0.1:{server.server_port}/", flush=True)
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
                return
            source = POLICY_SOURCE if args.policies else CONTROL_SOURCE if args.controls else SOURCE
            bindings = {"tools": BINDINGS["tools"]} if args.controls else BINDINGS
            if not args.controls and not args.policies:
                studio.catalog.save(
                    "toolsets",
                    {
                        "id": "local-tools",
                        "name": "Local tools",
                        "data": {"tools": ["text:wordcount"]},
                    },
                )
                bindings = {**bindings, "toolsets": {"local": "local-tools"}}
            if args.policies:
                bindings = {
                    "models": {"api-demo": {"provider": "openai"}},
                    "tools": BINDINGS["tools"],
                }
            report = studio.configuration.convert_interop({"text": source, "bindings": bindings}, None)
            assert report["valid"], report["diagnostics"]
            assert not studio.definitions("agents") and not studio.records("configuration-plans")
            plan = studio.configuration.plan(report["document"], None)
            studio.configuration.apply(plan["id"], plan["digest"], None)
            run = studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "policy-review"
                    if args.policies
                    else "control-review"
                    if args.controls
                    else "count-review",
                    "task": "Review",
                    "workflow_input": {
                        "entries": ["one two", "three"],
                        "review": "local review",
                        "route": "short",
                    }
                    if args.controls
                    else {"text": "one two three"},
                }
            )
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                run = studio.run(run["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            assert run["status"] == "completed", run
            expected = (
                {
                    "counts": [2, 1],
                    "counts_again": [2, 1],
                    "loop": {"iterations_completed": 2, "stopped_reason": "condition_false"},
                    "last": 2,
                    "route": {
                        "selected_branch": "short",
                        "selected_case_index": 0,
                        "cases_unresolved": 0,
                    },
                    "answer": 1,
                }
                if args.controls
                else {"count": 3, "review": {"reviewed": True}}
            )
            if args.policies:
                expected = {"review": {"reviewed": True}, "cleanup": 1, "notify": 1}
                assert provider.calls == 2
            assert json.loads(run["output"]) == expected
            # Bound local dependencies stay in native configuration; this export
            # demonstrates the source-backed external definitions and their references.
            exported = studio.configuration.export_interop({"document": report["document"]}, None)
            assert exported["valid"], exported["diagnostics"]
            imported = studio.configuration.convert_interop(
                {"text": json.dumps(exported["bundle"])}, None
            )
            assert imported["valid"] and imported["document"] == report["document"]
            print(
                "External conversion, non-executing review, ordered tool/agent execution and export round-trip verified."
            )
        finally:
            studio.close()


if __name__ == "__main__":
    main()

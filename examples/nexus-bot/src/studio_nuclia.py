"""Consume Nuclia answers, private citations and artifact revisions over local HTTP.

Start the repository studio-nuclia simulation on port 49873 first.
Only synthetic credentials, a temporary workspace and deterministic AI are used.
"""

import argparse
import asyncio
import base64
import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.core.studio_nuclia_interop import POLICY, convert_template_settings
from nexus.studio import make_studio_handler


def exercise(
    base="http://127.0.0.1:49873/nuclia",
    *,
    templates=False,
    portable_template=False,
    evidence=False,
    originals=False,
):
    templates = templates or portable_template
    url = urlparse(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("This example accepts only its local HTTP simulation")
    prompts = []
    tool = None

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            if "Evaluate the answer using only the rubric." in context.prompt:
                assert '["Thirty days."]' in context.prompt
                return AgentResult(
                    success=True,
                    output='{"score":1.0,"reason":"Grounded in saved reference context"}',
                )
            if evidence and len(prompts) >= 2:
                return AgentResult(success=True, output="Thirty days.")
            prompts.append(context.prompt)
            if len(prompts) == 1:
                return AgentResult(
                    success=True,
                    output=json.dumps(
                        {
                            "tool_call": {
                                "name": tool,
                                "arguments": {
                                    "action": "ask",
                                    "arguments": {
                                        "query": "What is the return policy?",
                                        **(
                                            {
                                                "parameters": {
                                                    "region": "emea",
                                                    "document": "resource",
                                                }
                                            }
                                            if templates
                                            else {
                                                "filter_expression": {
                                                    "field": {"prop": "resource", "id": "resource"}
                                                }
                                            }
                                        ),
                                        "prompt": {"user": "Use {context} to answer {question}"},
                                        "rephrase": False,
                                    },
                                },
                            }
                        }
                    ),
                )
            assert "Thirty days." in context.prompt and "[source:1]" in context.prompt
            return AgentResult(success=True, output='{"final":"Thirty days [source:1]."}')

    with (
        tempfile.TemporaryDirectory(prefix="nexus-nuclia-") as directory,
        patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": "synthetic-nuclia-key"}),
    ):
        studio = StudioService(directory, provider_factory=lambda _: Provider())
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_studio_handler(studio, "synthetic-nuclia-csrf")
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            record = studio.catalog.save(
                "connectors",
                {
                    "name": "Selected knowledge",
                    "data": {
                        "type": "nuclia",
                        "url": base,
                        "allow_private": True,
                        "knowledge_box": "selected",
                        "token_env": "EXAMPLE_NUCLIA_KEY",
                        "enabled": True,
                    },
                },
            )

            def attempts():
                response = requests.get(base + "/fixture/attempts", timeout=5)
                response.raise_for_status()
                return response.json()["attempts"]

            def call(action, arguments):
                return asyncio.run(
                    studio.connections.execute(
                        record["id"], {"action": action, "arguments": arguments}
                    )
                )

            before = attempts()
            assert call("query", {"query": "Policy", "page": 1})["page_number"] == 1
            try:
                call("ask", {"query": "Policy"})
            except PermissionError:
                pass
            else:
                raise AssertionError("Generation was enabled implicitly")
            assert attempts() == before + 1
            record = studio.catalog.save(
                "connectors",
                {
                    **record,
                    "data": {
                        **record["data"],
                        "actions": ["query", "ask", *(["download_original"] if originals else [])],
                        **({"capture_evaluation": True} if evidence else {}),
                    },
                },
            )
            if templates:
                template = {
                    "version": 1,
                    "parameters": [
                        {"name": "region", "type": "string", "required": True},
                        {"name": "document", "type": "string", "default": "resource"},
                    ],
                    "query_prompt": "Region: {{region}}; Question: {{query}}",
                    "filter_expression": {"field": {"prop": "resource", "id": "{{document}}"}},
                }
                if portable_template:
                    converted = convert_template_settings(
                        {
                            "template_parameters": template["parameters"],
                            "prompt_rephrasing": {
                                "template": "Region: {region}; Question: {query}"
                            },
                            "filter_expression_template": {
                                "field": {"prop": "resource", "id": "{document}"}
                            },
                        },
                        policy=POLICY,
                    )
                    assert converted["adaptations"]
                    template = converted["template"]
                    # Review through the same full preflight used by imported configurations.
                    exported = studio.configuration.export(None)
                    selected = next(
                        row for row in exported["resources"] if row["id"] == record["id"]
                    )
                    selected["data"]["template"] = template
                    selected["data"]["enabled"] = False
                    plan = studio.configuration.plan(exported, None)
                    studio.configuration.apply(plan["id"], plan["digest"], None)
                    record = studio.catalog.get("connectors", record["id"])
                record = studio.catalog.save(
                    "connectors",
                    {**record, "data": {**record["data"], "template": template, "enabled": True}},
                )
            tool = next(
                name for name in studio.operations.tools if name.endswith(":" + record["id"])
            )
            agent = studio.save_definition(
                "agents",
                {
                    "name": "Cited policy assistant",
                    "instructions": "Answer from the selected knowledge box. Cite only returned source references.",
                    "tool_ids": [tool],
                    "max_tool_calls": 1,
                },
            )
            run = studio.start_run({"agent_id": agent["id"], "task": "What is the return policy?"})
            for _ in range(500):
                run = studio.run(run["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.01)
            assert run["status"] == "completed", run
            assert run["output"] == "Thirty days [source:1]."
            assert len(run["citations"]) == 1 and len(run["structured_content"]) == (
                3 if evidence else 2
            )
            citation = run["citations"][0]
            assert citation["original_page"] == 4 and citation["page"] == 1
            assert (
                studio.session(run["session_id"])["messages"][-1]["citations"] == run["citations"]
            )
            origin = f"http://127.0.0.1:{server.server_port}"
            path = f"/api/catalog/artifacts/{citation['artifact_id']}/preview?revision={citation['artifact_revision']}&page=1"
            preview = requests.get(origin + path, timeout=5)
            preview.raise_for_status()
            assert "Thirty days." in preview.text and "Original provider page: 4" in preview.text
            snapshot = studio.catalog.get("artifacts", citation["artifact_id"])
            studio.catalog.save(
                "artifacts",
                {
                    **snapshot,
                    "data": {
                        "filename": "changed.txt",
                        "content_base64": base64.b64encode(b"New revision").decode(),
                    },
                },
            )
            pinned = requests.get(origin + path, timeout=5)
            pinned.raise_for_status()
            assert "Thirty days." in pinned.text and "New revision" not in pinned.text
            if originals:
                assert citation["original_document"]["connector_id"] == record["id"]
                before_download = attempts()
                original_ids = []
                for _ in range(2):
                    queued = requests.post(
                        origin + "/api/studio/original-document",
                        headers={"X-Nexus-Studio-Token": "synthetic-nuclia-csrf"},
                        json={"source_run_id": run["id"], "source_index": 0},
                        timeout=5,
                    )
                    queued.raise_for_status()
                    download = queued.json()
                    for _ in range(500):
                        download = studio.run(download["id"])
                        if download["status"] not in {"queued", "running"}:
                            break
                        time.sleep(0.01)
                    assert download["status"] == "completed", download
                    copied = json.loads(download["output"])
                    assert copied["remote_revision_pinned"] is False
                    artifact = copied["artifacts"][0]
                    original_ids.append(artifact["id"])
                    content = requests.get(
                        origin
                        + f"/api/catalog/artifacts/{artifact['id']}/versions/{artifact['revision']}",
                        timeout=5,
                    )
                    content.raise_for_status()
                    assert content.content == b"Original policy file: Thirty days."
                assert original_ids[0] == original_ids[1] and attempts() == before_download + 1
                assert studio.run(run["id"])["citations"][0] == citation
            if evidence:
                report = next(
                    item
                    for item in run["structured_content"]
                    if item.get("filename") == "retrieval-evidence.json"
                )
                artifact = studio.artifacts.revision(report["artifact_id"], report["revision"])
                document = json.loads(studio.artifacts.get(artifact))
                assert document["contexts"] == ["Thirty days."]
                assert document["learning_id"] == "synthetic-learning-id"
                assert document["question"] == "What is the return policy?"
                headers = {"X-Nexus-Studio-Token": "synthetic-nuclia-csrf"}
                feedback = requests.post(
                    origin + "/api/studio/feedback",
                    headers=headers,
                    json={"run_id": run["id"], "rating": 1},
                    timeout=5,
                )
                feedback.raise_for_status()
                promoted = requests.post(
                    origin + "/api/studio/feedback/promote",
                    headers=headers,
                    json={
                        "feedback_id": feedback.json()["id"],
                        "name": "Grounded policy answer",
                        "expected": "Thirty days",
                        "target_kind": "agent",
                        "target_id": agent["id"],
                        "evidence": {
                            "artifact_id": report["artifact_id"],
                            "revision": report["revision"],
                        },
                        "judge": {
                            "provider": "codex",
                            "rubric": "Answer must follow the saved reference context",
                        },
                    },
                    timeout=5,
                )
                promoted.raise_for_status()
                suite = promoted.json()
                assert suite["data"]["cases"][0]["contexts"] == ["Thirty days."]
                evaluation = studio.start_run(
                    {
                        "kind": "evaluation",
                        "evaluation_id": suite["id"],
                        "task": "Check grounded answer",
                    }
                )
                for _ in range(500):
                    evaluation = studio.run(evaluation["id"])
                    if evaluation["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.01)
                assert evaluation["status"] == "completed", evaluation
                evaluation_report = json.loads(evaluation["output"])
                assert (
                    evaluation_report["passed"] == 1 and evaluation_report["mean_judge_score"] == 1
                )
            if templates:
                exported = studio.configuration.export(None)
                configured = next(row for row in exported["resources"] if row["id"] == record["id"])
                assert configured["data"]["template"] == record["data"]["template"]
                count = attempts()
                try:
                    call("ask", {"query": "Policy?", "parameters": {"region": 12}})
                except ValueError:
                    pass
                else:
                    raise AssertionError("Invalid typed value reached provider")
                assert attempts() == count
                data = {key: value for key, value in record["data"].items() if key != "template"}
                record = studio.catalog.save("connectors", {**record, "data": data})
            no_context = call("ask", {"query": "fixture:empty"})
            assert no_context["status"] == "no_context" and not no_context["sources"]
            assert len(no_context["artifacts"]) == (2 if evidence else 1)
            failures = ["error", "missing-citation", "reject", "redirect", "bad-json", "oversized"]
            for case in failures:
                count, artifacts = attempts(), len(studio.catalog.list("artifacts"))
                try:
                    call("ask", {"query": "fixture:" + case})
                except ValueError as error:
                    assert "Integration action failed" in str(error)
                    assert "PRIVATE_PROVIDER_DETAIL" not in str(error)
                else:
                    raise AssertionError("Invalid provider answer was accepted")
                assert attempts() == count + 1
                assert len(studio.catalog.list("artifacts")) == artifacts
            count = attempts()
            with patch.dict(os.environ, {"EXAMPLE_NUCLIA_KEY": ""}):
                try:
                    call("ask", {"query": "Policy"})
                except (ValueError, PermissionError):
                    pass
                else:
                    raise AssertionError("Missing credential did not stop generation")
            assert attempts() == count
            return {
                "requests": attempts() - before,
                "failure_cases": len(failures),
                "private_revision_citations": True,
                "explicit_generation": True,
                "live_verified": False,
            }
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49873/nuclia")
    parser.add_argument("--templates", action="store_true")
    parser.add_argument("--portable-template", action="store_true")
    parser.add_argument("--evidence", action="store_true")
    parser.add_argument("--originals", action="store_true")
    args = parser.parse_args()
    print(
        exercise(
            args.base,
            templates=args.templates,
            portable_template=args.portable_template,
            evidence=args.evidence,
            originals=args.originals,
        )
    )

"""Consume rich conversations, pinned sources and read-only execution replay.

Run from the checkout: PYTHONPATH=. python examples/nexus-bot/src/studio_conversations.py
Everything uses synthetic documents and an injected provider; no account is contacted.
"""

import asyncio
import base64
import io
import tempfile
import time
import zipfile

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


def settled(studio, identity):
    for _ in range(500):
        run = studio.run(identity)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.01)
    raise TimeoutError("Example job did not finish")


def create_demo(root, delay=0):
    evidence = {}

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent_streaming(self, context, delta):
            chunks = [
                "## Evidence review\n\nRefunds are available within thirty days. [source:1]\n\n",
                "| Source | Result |\n|---|---|\n| Policy | Verified |\n\n",
                "```python\nprint('Evidence retained')\n```\n\n[Reference](https://example.com)\n",
            ]
            for chunk in chunks:
                delta(chunk)
                if delay:
                    await asyncio.sleep(delay)
            return AgentResult(
                success=True,
                output="".join(chunks),
                metadata={
                    "structured_content": [
                        {"type": "json", "data": {"verified": True}},
                        {"type": "artifact", "artifact_id": evidence["id"], "revision": 1},
                    ]
                },
            )

    studio = StudioService(root, provider_factory=lambda _: Provider())
    studio.save_definition(
        "agents", {"name": "Evidence guide", "instructions": "Cite the provided policy."}
    )
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for number, text in enumerate(
            (
                "Refunds are available within thirty days.",
                "Keep the receipt for proof of purchase.",
            ),
            1,
        ):
            archive.writestr(
                f"ppt/slides/slide{number}.xml",
                f'<slide xmlns:a="urn:example"><a:t>{text}</a:t></slide>',
            )
    artifact = studio.catalog.save(
        "artifacts",
        {
            "name": "Returns policy",
            "data": {
                "filename": "returns.pptx",
                "content_base64": base64.b64encode(target.getvalue()).decode(),
            },
        },
    )
    evidence.update(id=artifact["id"])
    project = studio.knowledge.save(
        {"name": "Policies", "default_agent_id": "evidence-guide"}, None
    )
    document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)
    assert settled(studio, document["run_id"])["status"] == "completed"
    studio.save_definition(
        "workflows",
        {
            "name": "Review evidence",
            "graph": {
                "nodes": [
                    {"id": "review", "name": "Review request", "kind": "approval"},
                    {
                        "id": "count",
                        "name": "Count words",
                        "kind": "tool",
                        "tool_id": "text:wordcount",
                        "args": ["one two three"],
                    },
                ],
                "edges": [
                    {"source": "START", "target": "review"},
                    {"source": "review", "target": "count"},
                ],
            },
        },
    )
    return studio, project, artifact


def run_example():
    with tempfile.TemporaryDirectory(prefix="nexus-conversation-example-") as root:
        studio, project, artifact = create_demo(root)
        try:
            preview = studio.artifacts.preview(studio.artifacts.revision(artifact["id"], 1), page=2)
            assert preview["revision"] == 1 and "receipt" in preview["text"]
            run = studio.start_run(
                {
                    "task": "What is the refund policy?",
                    "knowledge_project_id": project["id"],
                    "agent_id": "evidence-guide",
                }
            )
            result = settled(studio, run["id"])
            assert result["status"] == "completed" and result["citations"]
            assert result["stream"]["status"] == "completed" and result["structured_content"]
            review = studio.start_run(
                {
                    "kind": "workflow",
                    "workflow_id": "review-evidence",
                    "task": "Count words after review",
                }
            )
            assert settled(studio, review["id"])["status"] == "waiting"
            resumed = studio.graph.resume(review["id"], "approve")
            assert settled(studio, resumed["id"])["status"] == "completed"
            snapshot = studio.execution.snapshot(review["id"], None)
            assert {event["kind"] for event in snapshot["timeline"]} >= {
                "node",
                "approval",
                "artifact",
            }
            print("Cited streaming reply, pinned Office section and approval replay verified.")
        finally:
            studio.close()


if __name__ == "__main__":
    run_example()

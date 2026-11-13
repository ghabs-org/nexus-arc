"""Consume additive framework features without bot-specific implementations.

Run with ``PYTHONPATH=. python examples/nexus-bot/src/studio_parity.py`` from the checkout.
Uses an isolated workspace and deterministic provider; no account is contacted.
"""

import base64
import tempfile
import time

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService


class ExampleProvider:
    async def check_availability(self):
        return True

    async def execute_agent(self, context):
        return AgentResult(success=True, output="Returns allowed within thirty days [source:1].")


def run_example():
    with tempfile.TemporaryDirectory(prefix="nexus-parity-example-") as root:
        studio = StudioService(root, provider_factory=lambda _: ExampleProvider())
        try:
            project = studio.knowledge.save(
                {"name": "Customer policies", "instructions": "Cite sources"}, None
            )
            artifact = studio.catalog.save(
                "artifacts",
                {
                    "name": "Returns policy",
                    "data": {
                        "filename": "returns.md",
                        "content_base64": base64.b64encode(
                            b"Returns allowed within thirty days."
                        ).decode(),
                    },
                },
            )
            document = studio.knowledge.attach(project["id"], {"artifact_id": artifact["id"]}, None)

            def finish(run):
                for _ in range(500):
                    record = studio.run(run["id"])
                    if record["status"] not in {"running", "queued"}:
                        if record["status"] != "completed":
                            raise RuntimeError(record.get("error", "Example job failed"))
                        return record
                    time.sleep(0.01)
                raise TimeoutError("Example job did not finish")

            finish({"id": document["run_id"]})
            prompt = studio.catalog.save(
                "prompts",
                {
                    "name": "Review policy",
                    "data": {
                        "content": "Explain {{Policy}}",
                        "shortcut": "policy",
                        "tags": ["support"],
                    },
                },
            )
            run = finish(
                studio.start_run(
                    {
                        "task": "/policy",
                        "prompt_values": {prompt["id"]: {"Policy": "returns"}},
                        "knowledge_project_id": project["id"],
                    }
                )
            )
            studio.collaboration.feedback(
                {"run_id": run["id"], "rating": 1, "comment": "Source checked"}, None
            )
            share = studio.collaboration.share({"session_id": run["session_id"]}, None)
            assert studio.collaboration.get_share(share["id"])["messages"]
            assert studio.features.get(["schema"], {}, None)["version"] == 1
            integrations = studio.features.get(["integrations"], {}, None)["items"]
            assert len(integrations) == 12
            connection = studio.recipes.configure(
                "tavily", {"token_env": "EXAMPLE_TAVILY_KEY"}, None
            )
            assert not connection["data"]["enabled"]
            agent = studio.save_definition(
                "agents",
                {
                    "name": "Knowledge assistant",
                    "provider": "codex",
                    "instructions": "Cite supplied sources",
                },
            )
            gateway = studio.catalog.save(
                "entrypoints",
                {
                    "name": "Example MCP gateway",
                    "data": {
                        "target_kind": "agent",
                        "target_id": agent["id"],
                        "token_env": "EXAMPLE_GATEWAY_TOKEN",
                        "enabled": False,
                        "mcp_enabled": True,
                    },
                },
            )
            assert not gateway["data"]["enabled"]
            studio.catalog.save(
                "connectors",
                {
                    "name": "Example Bedrock knowledge",
                    "data": {
                        "type": "bedrock-kb",
                        "region": "eu-west-1",
                        "resource_id": "EXAMPLEKB1",
                        "enabled": False,
                    },
                },
            )
            assert studio.configuration.validate(studio.configuration.export(None), None)["valid"]
            assert run["citations"], "Retrieved source must be pinned in the conversation"
            print(run["output"])
            print(
                f"Indexed document, pinned citations, prompt variables, feedback and private snapshot: {len(run['citations'])} sources"
            )
        finally:
            studio.close()


if __name__ == "__main__":
    run_example()

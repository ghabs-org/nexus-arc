"""Research uses the existing agent loop; synthesis never bypasses its budget."""

import hashlib
import json

import pytest

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from tests.test_studio_dynamic_embeds import settled
from tests.test_studio_research import public_dns as public_dns


@pytest.mark.parametrize("mode", ["synthesize", "exhaust", "failure", "forbidden"])
def test_iterative_research_retains_citations_and_enforces_agent_budget(
    tmp_path, monkeypatch, public_dns, mode
):
    prompts, requests = [], []
    tool = None

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            if len(prompts) == 3 and mode == "synthesize":
                assert "Thirty days." in context.prompt and "Receipt required." in context.prompt
                return AgentResult(
                    success=True,
                    output='{"final":"Thirty days [source:1]; receipt required [source:2]."}',
                )
            return AgentResult(
                success=True,
                output=json.dumps(
                    {
                        "tool_call": {
                            "name": "text:wordcount" if mode == "forbidden" else tool,
                            "arguments": {
                                "queries": ["returns" if len(prompts) == 1 else "receipt"],
                                "fetch_limit": 1,
                            },
                        }
                    }
                ),
            )

    def request(method, url, headers, body, text):
        requests.append((url, body))
        if mode == "failure":
            raise ValueError("Selected search service unavailable")
        if text:
            assert not headers
            return {
                "content_type": "text/plain",
                "text": "Thirty days." if url.endswith("returns") else "Receipt required.",
            }
        path = "returns" if body["query"] == "returns" else "receipt"
        return {
            "results": [{"url": "https://example.org/" + path, "title": path, "content": "Excerpt"}]
        }

    monkeypatch.setenv("RESEARCH_ITERATION_KEY", "synthetic-key")
    studio = StudioService(
        tmp_path,
        provider_factory=lambda _: Provider(),
        research_options={
            "config": {"search": {"provider": "tavily", "token_env": "RESEARCH_ITERATION_KEY"}},
            "request_client": request,
        },
    )
    try:
        tool = next(name for name in studio.research.names if name.endswith(":research"))
        agent = studio.save_definition(
            "agents",
            {
                "name": "Researcher",
                "instructions": "Research and cite",
                "tool_ids": [tool],
                "max_tool_calls": 2,
            },
        )
        run = settled(
            studio,
            studio.start_run(
                {"agent_id": agent["id"], "task": "Compare returns policy and receipt requirements"}
            ),
        )
        if mode == "synthesize":
            assert run["status"] == "completed", run
            assert len(prompts) == 3 and len(requests) == 4
            assert "Untrusted tool result:" in prompts[1]
            assert run["output"] == "Thirty days [source:1]; receipt required [source:2]."
            sources = run["citations"]
            assert [s["url"] for s in sources] == [
                "https://example.org/returns",
                "https://example.org/receipt",
            ]
            for source, content in zip(sources, ["Thirty days.", "Receipt required."], strict=True):
                assert source["sha256"] == hashlib.sha256(content.encode()).hexdigest()
                assert source["excerpt"] == content and source["retrieved_at"]
            reply = studio.session(run["session_id"])["messages"][-1]
            assert reply["citations"] == sources and reply["content"] == run["output"]
        elif mode == "exhaust":
            assert run["status"] == "failed" and "2-call" in run["error"]
            assert len(prompts) == 3 and len(requests) == 4
        else:
            assert run["status"] == "failed", run
            assert len(prompts) == 1
            assert len(requests) == (1 if mode == "failure" else 0)
    finally:
        studio.close()

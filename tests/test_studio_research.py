import asyncio
import socket
from datetime import UTC, datetime

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_research import public_url


@pytest.fixture
def public_dns(monkeypatch):
    original = socket.getaddrinfo
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: (
            original(host, *args, **kwargs)
            if host in {"127.0.0.1", "::1"}
            else [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        ),
    )


def test_research_queries_are_bounded_deduplicated_cited_and_no_page_credentials(
    tmp_path, monkeypatch, public_dns
):
    calls = []

    def request(method, url, headers, body, text):
        calls.append((url, headers, body))
        if text:
            assert not headers
            return {
                "content_type": "text/html",
                "text": "<h1>Policy</h1><script>steal()</script><p>Thirty days.</p>",
            }
        assert headers == {"Authorization": "Bearer fixture-key"}
        return {
            "results": [
                {"url": "https://example.org/policy", "title": "Returns", "content": "Policy"}
            ]
        }

    monkeypatch.setenv("TAVILY_TEST_KEY", "fixture-key")
    studio = StudioService(
        tmp_path,
        research_options={
            "config": {"search": {"provider": "tavily", "token_env": "TAVILY_TEST_KEY"}},
            "request_client": request,
        },
    )
    try:
        run = {
            "id": "research-fixture",
            "status": "running",
            "owner_id": None,
            "kind": "tool",
            "provider": "local-tool",
            "created_at": datetime.now(UTC).isoformat(),
        }
        studio._write(studio._path("runs", run["id"]), run)
        token = current_run_id.set(run["id"])
        try:
            result = asyncio.run(
                studio.research.execute(
                    "research", {"queries": ["returns", "refunds"], "fetch_limit": 2}
                )
            )
        finally:
            current_run_id.reset(token)
        assert len(result["sources"]) == 1
        assert (
            "Thirty days" in result["sources"][0]["text"]
            and "steal" not in result["sources"][0]["text"]
        )
        assert len(calls) == 3
        citation = studio.run(run["id"])["citations"][0]
        assert citation["kind"] == "web" and citation["sha256"] and citation["retrieved_at"]
        assert result["sources"][0]["citation_ref"] == "[source:1]"
        with pytest.raises(ValueError, match="1–4"):
            asyncio.run(studio.research.execute("research", {"queries": ["q"] * 5}))
    finally:
        studio.close()


def test_search_never_chooses_an_unconfigured_provider_or_falls_back(tmp_path, public_dns):
    calls = []
    studio = StudioService(
        tmp_path, research_options={"request_client": lambda *args: calls.append(args)}
    )
    try:
        with pytest.raises(ValueError, match="explicitly"):
            asyncio.run(studio.research.execute("web_search", {"query": "test"}))
        with pytest.raises(ValueError, match="enable"):
            asyncio.run(studio.research.execute("ip_location", {}))
        with pytest.raises(ValueError, match="enable"):
            asyncio.run(studio.research.execute("finance", {"ticker": "AAPL"}))
        assert not calls
    finally:
        studio.close()


def test_public_fetch_blocks_credentials_local_networks_and_unsafe_ports():
    for url in (
        "https://127.0.0.1/admin",
        "http://[::1]/",
        "https://user:secret@example.org",
        "file:///etc/passwd",
        "https://example.org:8443/",
    ):
        with pytest.raises(ValueError):
            public_url(url)


def test_finance_ip_and_image_search_adapters_have_working_contracts(
    tmp_path, monkeypatch, public_dns
):
    calls = []

    def request(method, url, headers, body, text):
        calls.append((url, headers))
        if "ipapi.co" in url:
            return {"ip": "8.8.8.8", "city": "Fixture", "latitude": 1, "longitude": 2}
        return {
            "results": [
                {
                    "url": "https://example.org/photo",
                    "original": "https://example.org/photo.jpg",
                    "title": "Photo",
                }
            ]
        }

    monkeypatch.setenv("BRAVE_TEST", "fixture-key")
    studio = StudioService(
        tmp_path,
        research_options={
            "config": {
                "search": {"provider": "brave", "token_env": "BRAVE_TEST"},
                "finance": True,
                "ip_location": True,
            },
            "request_client": request,
            "finance_client": lambda ticker, period: {
                "ticker": ticker,
                "period": period,
                "rows": [{"Close": 123}],
            },
        },
    )
    try:
        assert (
            asyncio.run(studio.research.execute("finance", {"ticker": "AAPL"}))["rows"][0]["Close"]
            == 123
        )
        assert (
            asyncio.run(studio.research.execute("ip_location", {"ip": "8.8.8.8"}))["city"]
            == "Fixture"
        )
        assert asyncio.run(studio.research.execute("image_search", {"query": "trees"}))["items"][0][
            "url"
        ].endswith(".jpg")
        assert calls[0][1] == {} and calls[1][1] == {"X-Subscription-Token": "fixture-key"}
        with pytest.raises(ValueError, match="public IP"):
            asyncio.run(studio.research.execute("ip_location", {"ip": "10.0.0.1"}))
        with pytest.raises(ValueError, match="ticker"):
            asyncio.run(studio.research.execute("finance", {"ticker": "https://internal"}))
    finally:
        studio.close()


def test_all_community_entries_have_setup_and_mcp_secrets_stay_references(
    tmp_path, monkeypatch, public_dns
):
    calls = []

    async def remote(url, *args, **kwargs):
        calls.append((url, kwargs))
        return [{"name": "search", "inputSchema": {"type": "object"}}]

    monkeypatch.setattr("nexus.tools.mcp_remote.remote_mcp", remote)
    studio = StudioService(tmp_path)
    try:
        rows = studio.recipes.list()["items"]
        assert len(rows) == 12 and len({r["id"] for r in rows}) == 12
        assert len(studio.recipes.list()["official_items"]) == 15
        assert len(studio.recipes.list()["excluded"]) == 4
        for identity in ("tavily", "bright-data", "zapier"):
            monkeypatch.setenv("CATALOG_TEST_TOKEN", "fixture+/secret")
            record = studio.recipes.configure(identity, {"token_env": "CATALOG_TEST_TOKEN"}, None)
            assert not record["data"]["enabled"] and "fixture+/secret" not in str(record)
            result = asyncio.run(
                studio.connections.execute(record["id"], {"action": "discover"}, probe=True)
            )
            assert result[0]["name"] == "search"
        assert "fixture%2B%2Fsecret" in calls[1][0] and "fixture%2B%2Fsecret" in calls[2][0]
        assert calls[0][1]["headers"]["Authorization"] == "Bearer fixture+/secret"
        mail = studio.recipes.configure("send-grid", {}, None)
        assert not mail["data"]["enabled"] and mail["data"]["operations"] == ["sendMail"]
        ops = asyncio.run(
            studio.connections.execute(mail["id"], {"action": "list_operations"}, probe=True)
        )
        assert "sendMail" in str(ops)
        fs = studio.recipes.configure("filesystem", {"command": "installed-filesystem-mcp"}, None)
        assert fs["data"]["args"] == [str(tmp_path)] and not fs["data"]["enabled"]
        for row in rows:
            if row["mode"] == "tool":
                configured = studio.recipes.configure(row["id"], {}, None)
                assert configured["tool_id"] and "setup" in configured
        assert "nexus chat" in studio.recipes.configure("cli-entrypoint", {}, None)["command"]
    finally:
        studio.close()

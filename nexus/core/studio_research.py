"""Bounded, opt-in research adapters shared by agents, workflows and Studio.

Search queries go only to the selected provider. Page fetches send no account
credentials, reject private destinations and never follow redirects. Sources
are recorded on the current run, including retrieval time and content digest.
"""

import asyncio
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urlparse

import requests

from nexus.core.studio_connections import env_name
from nexus.core.studio_data_tools import current_run_id
from nexus.core.studio_remote import validate_destination, validate_remote

# Shared CLI workers whose own web search and fetch follow the "web" tool level.
NATIVE_WEB_PROVIDERS = frozenset({"codex", "claude-code", "opencode"})


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript", "svg"}:
            self.hidden += 1
        elif tag in {"p", "div", "li", "br", "h1", "h2", "h3", "tr"} and not self.hidden:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template", "noscript", "svg"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


def public_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("Supply a public HTTP(S) URL up to 2000 characters")
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Supply a public HTTP(S) URL without credentials or fragment")
    if parsed.port not in {None, 80, 443} or any(c in value for c in "\r\n\x00"):
        raise ValueError("Page fetches use standard web ports only")
    validate_destination(value)
    return value


class StudioResearch:
    def __init__(
        self,
        studio,
        config=None,
        request_client=None,
        finance_client=None,
        finance_info_client=None,
    ):
        self.studio = studio
        self.config = config or {}
        if not isinstance(self.config, dict) or set(self.config) - {
            "search",
            "finance",
            "ip_location",
            "geographic",
        }:
            raise ValueError(
                "Research configuration uses search, finance, ip_location and geographic"
            )
        for key in ("finance", "ip_location", "geographic"):
            if type(self.config.get(key, False)) is not bool:
                raise ValueError(f"Research {key} must be explicitly true or false")
        search = self.config.get("search", {})
        if not isinstance(search, dict) or set(search) - {
            "provider",
            "url",
            "token_env",
            "allow_private",
        }:
            raise ValueError("Search uses provider, URL and credential environment reference")
        self.search = dict(search)
        if search:
            provider = search.get("provider")
            if provider not in {"tavily", "brave", "searxng"}:
                raise ValueError(
                    "Choose tavily, brave or an explicitly configured searxng search service"
                )
            if search.get("token_env"):
                env_name(search["token_env"])
            if provider == "searxng":
                self.search.update(
                    validate_remote(
                        {
                            "url": search.get("url"),
                            "allow_private": search.get("allow_private", False),
                        }
                    )
                )
            elif set(search) - {"provider", "token_env"}:
                raise ValueError("Hosted search adapters use their fixed provider endpoint")
        self.request_client = request_client
        self.finance_client = finance_client
        self.finance_info_client = finance_info_client
        self.names = {}
        namespace = hashlib.sha256(str(studio.state).encode()).hexdigest()[:12]
        from nexus.tools import tool_registry

        descriptions = {
            "web_search": "Search using the explicitly configured search account; arguments: query, optional limit (1–10). Returns source URLs and excerpts.",
            "image_search": "Search for image source URLs with the configured Tavily or Brave account; arguments: query, optional limit (1–10). Does not fetch images.",
            "web_fetch": "Fetch a public URL without credentials or redirects; arguments: url. Returns bounded untrusted page text and source citation.",
            "research": "Collect a cited research bundle using up to four supplied queries and four page fetches; arguments: queries (list), optional fetch_limit (0–4). No automatic provider fallback.",
            "ip_location": "Look up a public IP with explicitly enabled ipapi.co; arguments: optional ip. An empty IP looks up the Studio server's public location.",
            "finance": "Read explicitly enabled yfinance data. Arguments: ticker, operation (history/default, price, fundamentals). Only history accepts period (1mo, 3mo, 6mo, 1y). Quotes may be delayed; metrics retain source units. No trades.",
        }
        descriptions.update(
            {
                "city_to_coordinates": "Find city coordinates and location evidence using explicitly enabled Open-Meteo. Arguments: city, optional country_code, limit (1–5).",
                "city_to_timezone": "Find a city timezone, current local time, UTC offset and DST using explicitly enabled Open-Meteo. Arguments: city, optional country_code, limit (1–5).",
                "get_weather": "Get current weather and a 1–7 day forecast from explicitly enabled Open-Meteo. Arguments: city, optional country_code, forecast_days. Returns selected location, units and source evidence.",
            }
        )
        for action, description in descriptions.items():
            name = f"studio:{namespace}:{action}"

            def bind(selected):
                async def invoke(**kwargs):
                    return await self.execute(selected, kwargs)

                return invoke

            function = bind(action)
            tool_registry.register(name, function, description=description, category="Research")
            self.names[name] = function

    def native_search(self, provider_name):
        """Whether a shared CLI worker's own web search stands in for research.search."""
        return bool(self.studio.team) and not self.search and provider_name in NATIVE_WEB_PROVIDERS

    def native_web(self, provider_name, tool_ids):
        """Without a search account, let a CLI worker search the web itself.

        Returns the tool IDs left for the Studio tool loop and whether the worker's
        built-in web search and fetch replace the agent's search tools.
        """
        if self.search or provider_name not in NATIVE_WEB_PROVIDERS:
            return list(tool_ids), False
        web = {
            name: name.rsplit(":", 1)[1]
            for name in self.names
            if name.rsplit(":", 1)[1] in {"web_search", "research", "web_fetch"}
        }
        if not any(web.get(name) in {"web_search", "research"} for name in tool_ids):
            return list(tool_ids), False
        return [name for name in tool_ids if name not in web], True

    def close(self):
        from nexus.tools import tool_registry

        for name, function in self.names.items():
            tool_registry.unregister(name, func=function)

    def inventory(self):
        ready = bool(self.search)
        sdk = importlib.util.find_spec("yfinance") is not None
        finance_operations = {
            "history": bool(
                self.config.get("finance") and (self.finance_client is not None or sdk)
            ),
            "price": bool(
                self.config.get("finance") and (self.finance_info_client is not None or sdk)
            ),
            "fundamentals": bool(
                self.config.get("finance") and (self.finance_info_client is not None or sdk)
            ),
        }
        finance = any(finance_operations.values())
        return {
            "items": [
                {
                    "id": name,
                    **({"operations": dict(finance_operations)} if action == "finance" else {}),
                    "configured": self.config.get("geographic", False)
                    if action in {"city_to_coordinates", "city_to_timezone", "get_weather"}
                    else ready
                    if action in {"web_search", "research"}
                    else ready and self.search["provider"] != "searxng"
                    if action == "image_search"
                    else finance
                    if action == "finance"
                    else self.config.get("ip_location", False)
                    if action == "ip_location"
                    else True,
                    "billing": "Open-Meteo public API; personal/non-commercial terms"
                    if action in {"city_to_coordinates", "city_to_timezone", "get_weather"}
                    else "Selected search API account"
                    if action in {"web_search", "image_search", "research"}
                    and self.search.get("provider") != "searxng"
                    else "Selected public data service"
                    if action in {"finance", "ip_location"}
                    else "Local processing; selected source requests",
                    "setup": "Explicitly enable research.geographic for Open-Meteo personal/non-commercial requests. No API key; provider terms apply."
                    if action in {"city_to_coordinates", "city_to_timezone", "get_weather"}
                    else "Configure research.search in private runtime settings. No search provider or API account is chosen automatically. Until then, agents on shared Codex, Claude Code or OpenCode workers use the worker's built-in web search for web_search and research."
                    if action in {"web_search", "research"}
                    else "Configure research.search with Tavily or Brave in private runtime settings. No search provider or API account is chosen automatically."
                    if action == "image_search"
                    else "Explicitly enable research.finance and install the studio-research extra."
                    if action == "finance"
                    else "Explicitly enable research.ip_location to use ipapi.co."
                    if action == "ip_location"
                    else "Public HTTP(S) pages only; bounded text, no redirects or credentials.",
                }
                for name in self.names
                for action in [name.rsplit(":", 1)[1]]
            ]
        }

    def request(
        self,
        method,
        url,
        headers=None,
        body=None,
        *,
        private=False,
        text=False,
        binary=False,
        response_limit=512 * 1024,
        timeout=None,
    ):
        if type(binary) is not bool or binary and text:
            raise ValueError("Choose either binary or text response mode")
        if type(response_limit) is not int or not 1 <= response_limit <= 24 * 1024 * 1024:
            raise ValueError("Response limit exceeds supported bounds")
        if timeout is not None and (type(timeout) not in (int, float) or not 1 <= timeout <= 180):
            raise ValueError("Request timeout must be from 1 to 180 seconds")
        validate_destination(url, private)
        if self.request_client:
            return self.request_client(method, url, headers or {}, body, text)
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.request(
                    method,
                    url,
                    headers={"User-Agent": "NexusStudio/1.0", **(headers or {})},
                    json=body,
                    timeout=(5, timeout or 15),
                    allow_redirects=False,
                    stream=True,
                ) as response:
                    if not 200 <= response.status_code < 300:
                        raise ValueError("Research service rejected the request")
                    if binary and response.status_code != 200:
                        raise ValueError("Binary downloads require a complete response")
                    raw = bytearray()
                    start = time.monotonic()
                    for part in response.iter_content(16384):
                        raw.extend(part)
                        if len(raw) > response_limit or time.monotonic() - start > (timeout or 20):
                            raise ValueError("Research response exceeded its size or time limit")
                    if binary:
                        return {
                            "content": bytes(raw),
                            "content_type": response.headers.get(
                                "Content-Type", "application/octet-stream"
                            )
                            .split(";", 1)[0]
                            .lower(),
                        }
                    if text:
                        kind = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                        if kind not in {
                            "text/html",
                            "text/plain",
                            "text/markdown",
                            "application/xhtml+xml",
                            "application/json",
                        }:
                            raise ValueError("Fetch supports text, HTML, Markdown and JSON pages")
                        return {"text": raw.decode("utf-8", errors="replace"), "content_type": kind}
                    return json.loads(raw)
        except Exception:
            raise ValueError(
                "Research request failed; review the selected service, account or public URL. No other provider was used."
            ) from None

    def token(self, actor):
        values = os.environ
        if self.studio.team:
            values, error = self.studio.team.environment_resolver(actor["nexus_id"])
            if error:
                raise PermissionError("Search account credentials are unavailable")
        name = self.search.get("token_env") or {
            "tavily": "TAVILY_API_KEY",
            "brave": "BRAVE_SEARCH_API_KEY",
        }.get(self.search.get("provider"))
        value = values.get(name, "")
        if not value or any(c in value for c in "\r\n"):
            raise PermissionError("Configure credentials for the selected search provider")
        return value

    def search_pages(self, query, limit, images, actor):
        if (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 600
            or type(limit) is not int
            or not 1 <= limit <= 10
        ):
            raise ValueError("Search needs a query up to 600 characters and limit from 1 to 10")
        provider = self.search.get("provider")
        if not provider:
            raise ValueError("Configure a search provider explicitly before using search")
        if provider == "tavily":
            result = self.request(
                "POST",
                "https://api.tavily.com/search",
                {"Authorization": "Bearer " + self.token(actor)},
                {
                    "query": query,
                    "max_results": limit,
                    "include_images": images,
                    "include_image_descriptions": images,
                    "include_answer": False,
                },
            )
            rows = result.get("images", []) if images else result.get("results", [])
        elif provider == "brave":
            path = "images" if images else "web"
            result = self.request(
                "GET",
                f"https://api.search.brave.com/res/v1/{path}/search?"
                + urlencode({"q": query, "count": limit}),
                {"X-Subscription-Token": self.token(actor)},
            )
            rows = result.get("results", []) if images else result.get("web", {}).get("results", [])
        else:
            if images:
                raise ValueError(
                    "Image search needs the explicitly selected Tavily or Brave adapter"
                )
            result = self.request(
                "GET",
                self.search["url"] + "/search?" + urlencode({"q": query, "format": "json"}),
                private=self.search["allow_private"],
            )
            rows = result.get("results", [])
        output = []
        for row in rows[:limit]:
            if isinstance(row, str):
                row = {"url": row}
            url = row.get("original", row.get("url", ""))
            parsed = urlparse(str(url))
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                continue
            output.append(
                {
                    "url": str(url)[:2000],
                    "title": str(row.get("title", row.get("description", url)))[:500],
                    "text": str(row.get("content", row.get("description", "")))[:4000],
                    "source_url": row.get("url") if images else url,
                }
            )
        return {"provider": provider, "items": output, "untrusted": True}

    def fetch(self, url):
        url = public_url(url)
        response = self.request("GET", url, text=True)
        text = response["text"]
        if response["content_type"] in {"text/html", "application/xhtml+xml"}:
            parser = PageText()
            parser.feed(text)
            text = "\n".join(
                " ".join(line.split()) for line in "".join(parser.text).splitlines() if line.strip()
            )
        text = text[:16000]
        return {
            "url": url,
            "title": urlparse(url).hostname,
            "text": text,
            "untrusted": True,
            "retrieved_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        }

    def cite(self, rows, actor):
        run_id = current_run_id.get()
        if not run_id:
            return
        with self.studio.lock:
            run = self.studio.collaboration.owned(self.studio.run(run_id), actor)
            citations = list(run.get("citations", []))
            known = {c.get("url") for c in citations if c.get("url")}
            for row in rows:
                if row["url"] not in known and len(citations) < 40:
                    citations.append(
                        {
                            "kind": "web",
                            "url": row["url"],
                            "title": row.get("title", row["url"]),
                            "excerpt": row.get("text", "")[:4000],
                            "retrieved_at": row.get("retrieved_at", datetime.now(UTC).isoformat()),
                            "sha256": row.get(
                                "sha256", hashlib.sha256(row.get("text", "").encode()).hexdigest()
                            ),
                        }
                    )
                    known.add(row["url"])
                if row["url"] in known:
                    index = next(
                        i
                        for i, source in enumerate(citations, 1)
                        if source.get("url") == row["url"]
                    )
                    row["citation_ref"] = f"[source:{index}]"
            self.studio._update_run(run_id, citations=citations)

    def finance(self, args):
        if not self.config.get("finance"):
            raise ValueError("Explicitly enable the finance adapter in private runtime settings")
        operation = args.get("operation", "history")
        if not isinstance(operation, str) or operation not in {"history", "price", "fundamentals"}:
            raise ValueError("Choose finance operation history, price or fundamentals")
        if operation != "history" and "period" in args:
            raise ValueError("Finance period is used only for history")
        ticker, period = args.get("ticker"), args.get("period", "1mo")
        if (
            not isinstance(ticker, str)
            or not re.fullmatch(r"[A-Za-z0-9^=.-]{1,24}", ticker)
            or not isinstance(period, str)
            or period not in {"1mo", "3mo", "6mo", "1y"}
        ):
            raise ValueError("Choose a ticker and period: 1mo, 3mo, 6mo or 1y")
        from nexus.core.studio_finance import stock_result

        if operation == "history" and self.finance_client:
            return self.finance_client(ticker, period)
        if operation != "history" and self.finance_info_client:
            try:
                info = self.finance_info_client(ticker.upper())
            except Exception:
                raise ValueError(
                    "Finance service failed; no trading action was performed"
                ) from None
            return stock_result(ticker, operation, info)
        if importlib.util.find_spec("yfinance") is None:
            raise ValueError("Install the studio-research extra to use yfinance")
        with tempfile.TemporaryDirectory(prefix="nexus-finance-") as directory:
            try:
                from nexus.core.studio_desktop_sidecar import worker_command

                process = subprocess.run(
                    worker_command(
                        "nexus.core.studio_finance_worker",
                        ticker,
                        period,
                        operation,
                        executable=sys.executable,
                    ),
                    cwd=directory,
                    env={"PATH": os.defpath, "HOME": directory},
                    capture_output=True,
                    timeout=40,
                    check=True,
                )
                if len(process.stdout) > 256 * 1024:
                    raise ValueError
                result = json.loads(process.stdout)
                return (
                    result
                    if operation == "history"
                    else stock_result(ticker, operation, result["info"])
                )
            except Exception:
                raise ValueError(
                    "Finance service failed; no trading action was performed"
                ) from None

    async def execute(self, action, arguments):
        actor = self.studio.data_tools.actor()
        from nexus.core.studio_geography import ACTIONS, StudioGeography

        if action in ACTIONS:
            return await asyncio.to_thread(StudioGeography(self).execute, action, arguments, actor)
        if action in {"web_search", "image_search"}:
            result = await asyncio.to_thread(
                self.search_pages,
                arguments.get("query"),
                arguments.get("limit", 5),
                action == "image_search",
                actor,
            )
            if action == "web_search":
                self.cite(result["items"], actor)
            return result
        if action == "web_fetch":
            result = await asyncio.to_thread(self.fetch, arguments.get("url"))
            self.cite([result], actor)
            return result
        if action == "research":
            queries, maximum = arguments.get("queries"), arguments.get("fetch_limit", 2)
            if (
                not isinstance(queries, list)
                or not 1 <= len(queries) <= 4
                or type(maximum) is not int
                or not 0 <= maximum <= 4
            ):
                raise ValueError("Research supports 1–4 queries and 0–4 fetched pages")
            rows, seen, errors = [], set(), []
            for query in queries:
                results = await asyncio.to_thread(self.search_pages, query, 5, False, actor)
                for row in results["items"]:
                    if row["url"] not in seen:
                        seen.add(row["url"])
                        rows.append(row)
            for row in rows[:maximum]:
                try:
                    row.update(await asyncio.to_thread(self.fetch, row["url"]))
                except ValueError:
                    errors.append(
                        {
                            "url": row["url"],
                            "error": "Page unavailable or disallowed; search excerpt retained",
                        }
                    )
            self.cite(rows, actor)
            return {"queries": queries, "sources": rows, "errors": errors, "untrusted": True}
        if action == "finance":
            return await asyncio.to_thread(self.finance, arguments)
        if action == "ip_location":
            if not self.config.get("ip_location"):
                raise ValueError(
                    "Explicitly enable IP/location lookups in private runtime settings"
                )
            value = arguments.get("ip", "")
            if value:
                address = ipaddress.ip_address(value)
                if not address.is_global:
                    raise ValueError("Look up a public IP address")
                value = str(address) + "/"
            result = await asyncio.to_thread(
                self.request, "GET", "https://ipapi.co/" + value + "json/"
            )
            if result.get("error"):
                raise ValueError("IP/location service rejected the lookup")
            return {
                key: result.get(key)
                for key in (
                    "ip",
                    "city",
                    "region",
                    "country_name",
                    "latitude",
                    "longitude",
                    "timezone",
                    "org",
                )
            }
        raise ValueError("Unknown research tool")

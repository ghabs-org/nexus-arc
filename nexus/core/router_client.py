"""nexus-router client shared by the ARC runtime, the coordinator and Studio.

The router URL and token are server-owned settings (environment or the Studio
runtime file), never values supplied by a browser, model or chat message.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlparse

# ARC runtime tools (nexus.plugins.builtin.ai_runtime_plugin.AIProvider values)
# for each router provider id, and back.
ARC_TOOL_FOR_ROUTER = {
    "openai-codex": "codex",
    "anthropic": "claude",
    "github-copilot": "copilot",
    "opencode": "opencode",
}
ROUTER_FOR_ARC_TOOL = {tool: router for router, tool in ARC_TOOL_FOR_ROUTER.items()}


class RuntimeServiceUnavailableError(ValueError):
    """Selected endpoint could not be reached or returned a server failure."""


def endpoint(config):
    url = config.get("url", "")
    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Runtime service needs an HTTP(S) URL without credentials")
    if parsed.scheme != "https" and config.get("allow_private") is not True:
        raise ValueError("HTTP runtime services require explicit allow_private")
    return url.rstrip("/")


def request_json(method, url, *, headers=None, params=None, body=None, timeout=10):
    import requests

    try:
        with requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=body,
            allow_redirects=False,
            timeout=timeout,
            stream=True,
        ) as response:
            if response.status_code >= 500:
                raise RuntimeServiceUnavailableError
            if response.status_code == 401:
                raise PermissionError("The runtime service rejected the configured token")
            if response.status_code not in {200, 204}:
                raise ValueError
            raw = bytearray()
            for part in response.iter_content(65536):
                raw.extend(part)
                if len(raw) > 1024 * 1024:
                    raise ValueError
            return json.loads(raw) if raw else {}
    except PermissionError:
        raise
    except requests.exceptions.SSLError:
        raise ValueError("Configured runtime service TLS verification failed") from None
    except (requests.ConnectionError, requests.Timeout, RuntimeServiceUnavailableError):
        raise RuntimeServiceUnavailableError("Configured runtime service is unavailable") from None
    except Exception:
        raise ValueError(
            "Configured runtime service failed; check its availability and access settings"
        ) from None


class RouterClient:
    """Calls nexus-router. Methods take the acting identity first (Studio team mode uses it)."""

    def __init__(self, url: str, token: str | None = None, *, timeout: float = 10):
        self.url = url.rstrip("/")
        self.token = token or None
        self.timeout = timeout

    @classmethod
    def from_env(cls, default_url: str | None = None, *, timeout: float = 10):
        """NEXUS_ROUTER_URL (+ NEXUS_ROUTER_TOKEN); None when no router is configured."""
        url = (os.getenv("NEXUS_ROUTER_URL") or default_url or "").strip()
        if not url:
            return None
        # An operator-set environment value: plain HTTP is allowed (loopback, compose network).
        url = endpoint({"url": url, "allow_private": True})
        return cls(url, (os.getenv("NEXUS_ROUTER_TOKEN") or "").strip() or None, timeout=timeout)

    def _headers(self, identity) -> dict:
        return {"Authorization": "Bearer " + self.token} if self.token else {}

    def _call(self, method, path, identity=None, **kwargs):
        return request_json(
            method, self.url + path, headers=self._headers(identity), timeout=self.timeout, **kwargs
        )

    def route(self, identity, task, **fields):
        """POST /route; ``fields`` are /route body keys (None values are dropped)."""
        body = {"message": task, **{k: v for k, v in fields.items() if v is not None}}
        data = self._call("POST", "/route", identity, body=body)
        # nexus-router answers selected_model; model/provider_model are older router shapes.
        model = data.get("selected_model") or data.get("model") or data.get("provider_model")
        if not isinstance(model, str) or not model or len(model) > 200:
            raise ValueError("Model router returned no usable model")
        return {**data, "selected_model": model}

    def outcome(self, identity, decision_id, **fields):
        return self._call("POST", "/outcome", identity, body={"decision_id": decision_id, **fields})

    def feedback(self, identity, payload):
        return self._call("POST", "/feedback", identity, body=payload)

    def observe(self, identity, provider, http_status=None, error_type=None, **fields):
        body = {
            "provider": provider,
            "http_status": http_status,
            "error_type": error_type,
            **fields,
        }
        return self._call(
            "POST", "/observe", identity, body={k: v for k, v in body.items() if v is not None}
        )

    def route_info(self, identity, decision_id):
        return self._call("GET", "/route-info", identity, params={"id": decision_id})

    def decisions(self, identity=None, **params):
        return self._call(
            "GET", "/decisions", identity, params={k: v for k, v in params.items() if v}
        )

    def diagnostics(self, identity=None):
        return self._call("GET", "/diagnostics", identity)

    def cli_status(self, identity=None):
        return self._call("GET", "/v1/nexus/cli-status", identity)

    def health(self, identity=None):
        return self._call("GET", "/health", identity)

    def stats(self, identity=None):
        return self._call("GET", "/stats", identity)

    def pricing(self, identity=None):
        return self._call("GET", "/pricing", identity)

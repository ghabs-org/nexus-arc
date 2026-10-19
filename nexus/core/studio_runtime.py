"""Server-owned runtime wiring for state, model routing, memory and audit queries."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from nexus.core.studio_connections import env_name


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


def request_json(method, url, *, headers=None, params=None, body=None):
    import requests

    try:
        with requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=body,
            allow_redirects=False,
            timeout=10,
            stream=True,
        ) as response:
            if response.status_code >= 500:
                raise RuntimeServiceUnavailableError
            if response.status_code not in {200, 204}:
                raise ValueError
            raw = bytearray()
            for part in response.iter_content(65536):
                raw.extend(part)
                if len(raw) > 1024 * 1024:
                    raise ValueError
            return json.loads(raw) if raw else {}
    except requests.exceptions.SSLError:
        raise ValueError("Configured runtime service TLS verification failed") from None
    except (requests.ConnectionError, requests.Timeout, RuntimeServiceUnavailableError):
        raise RuntimeServiceUnavailableError("Configured runtime service is unavailable") from None
    except Exception:
        raise ValueError(
            "Configured runtime service failed; check its availability and access settings"
        ) from None


class StudioLokiQuery:
    def __init__(self, config, namespace):
        self.url = endpoint(config)
        self.config = config
        self.namespace = namespace
        if config.get("token_env"):
            env_name(config["token_env"])

    def get_events(self, since_hours):
        headers = {}
        if self.config.get("token_env"):
            token = os.getenv(self.config["token_env"])
            if not token:
                raise ValueError("Loki credential environment reference is not configured")
            headers["Authorization"] = "Bearer " + token
        # A project label is mandatory; no client-supplied LogQL is accepted.
        data = request_json(
            "GET",
            self.url + "/loki/api/v1/query_range",
            headers=headers,
            params={
                "query": '{app="nexus-studio",namespace=' + json.dumps(self.namespace) + "}",
                "start": str(int((time.time() - since_hours * 3600) * 1e9)),
                "end": str(time.time_ns()),
                "limit": "5000",
                "direction": "backward",
            },
        )
        if data.get("status") != "success":
            raise ValueError("Loki did not return a successful audit query")
        events = []
        for stream in data.get("data", {}).get("result", []):
            for _, line in stream.get("values", []):
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("event_type", "").startswith(
                        "studio.run."
                    ):
                        events.append(event)
                except (ValueError, TypeError):
                    continue
        return events


class StudioModelRouter:
    def __init__(self, config):
        self.url, self.config = endpoint(config), config
        self.studio = None
        if config.get("token_env"):
            env_name(config["token_env"])

    def __call__(self, identity, task, selected_agent):
        headers = {}
        name = self.config.get("token_env")
        if name:
            if self.studio.team:
                self.studio.team.assert_execution(identity)
                values, error = self.studio.team.environment_resolver(identity)
                token = values.get(name) if not error else None
            else:
                token = os.getenv(name)
            if not token:
                raise PermissionError("Configure your model-router account credential")
            headers["Authorization"] = "Bearer " + token
        data = request_json(
            "POST",
            self.url + "/route",
            headers=headers,
            body={"message": task, "task_type": "general_chat"},
        )
        model = data.get("model") or data.get("provider_model")
        if not isinstance(model, str) or not model or len(model) > 200:
            raise ValueError("Model router returned no usable model")
        return model


def load_runtime_configuration(path, root):
    source = Path(path).resolve()
    if source.is_relative_to(Path(root).resolve()):
        raise ValueError("Runtime configuration must live outside the shared project")
    if source.stat().st_mode & 0o077:
        raise ValueError("Runtime configuration must have mode 0600")
    data = json.loads(source.read_text())
    if not isinstance(data, dict) or set(data) - {
        "state",
        "memory_sources",
        "router",
        "loki",
        "embeddings",
        "speech",
        "analysis_workers",
        "research",
        "workflow_limits",
    }:
        raise ValueError("Runtime configuration contains unsupported settings")
    state = data.get("state", {})
    if not isinstance(state, dict) or set(state) - {"url_env", "namespace", "workers"}:
        raise ValueError("State configuration uses url_env, namespace and workers")
    workbench = {"memory_sources": data.get("memory_sources", {})}
    if data.get("router"):
        workbench["router_client"] = StudioModelRouter(data["router"])
    if data.get("loki"):
        namespace = state.get("namespace") or data["loki"].get("namespace")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("Loki requires an explicit project namespace")
        workbench["audit_provider"] = StudioLokiQuery(data["loki"], namespace)
    options = {"state_options": state, "workbench_options": workbench}
    if data.get("embeddings"):
        from nexus.core.studio_embeddings import StudioEmbeddings

        options["knowledge_options"] = {"embeddings": StudioEmbeddings(data["embeddings"])}
    if data.get("speech"):
        if not isinstance(data["speech"], dict):
            raise ValueError("Speech profiles must be a named profile mapping")
        options["speech_options"] = {"profiles": data["speech"]}
    if data.get("analysis_workers"):
        if not isinstance(data["analysis_workers"], dict):
            raise ValueError("Analysis worker profiles must be a named mapping")
        options["data_tool_options"] = {"profiles": data["analysis_workers"]}
    if data.get("research"):
        options["research_options"] = {"config": data["research"]}
    if "workflow_limits" in data:
        from nexus.core.studio_workflow_policy import workflow_limits

        options["workflow_limits"] = workflow_limits(data["workflow_limits"])
    return options

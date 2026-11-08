"""Server-owned runtime wiring for state, model routing, memory and audit queries."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from nexus.core.router_client import (  # noqa: F401  (re-exported: existing importers)
    RouterClient,
    RuntimeServiceUnavailableError,
    endpoint,
    request_json,
)
from nexus.core.studio_connections import env_name


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


class StudioModelRouter(RouterClient):
    """The router for Studio. URL and credential come only from the server-owned runtime file."""

    def __init__(self, config):
        super().__init__(endpoint(config))
        self.config = config
        self.studio = None
        if config.get("token_env"):
            env_name(config["token_env"])

    def _headers(self, identity):
        headers = {}
        name = self.config.get("token_env")
        token = self.config.get("token")  # set in-process only (the desktop's built-in router)
        if name:
            if self.studio.team:
                self.studio.team.assert_execution(identity)
                values, error = self.studio.team.environment_resolver(identity)
                token = values.get(name) if not error else None
            else:
                token = os.getenv(name)
            if not token:
                raise PermissionError("Configure your model-router account credential")
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    def route(self, identity, task, allowed_providers=None, experiment_key=None, **fields):
        """POST /route as Studio; returns the decision with a validated selected_model."""
        return super().route(
            identity,
            task,
            source_type="studio",
            source_tag=fields.pop("source_tag", None) or "nexus-studio",
            allowed_providers=sorted(allowed_providers) if allowed_providers else None,
            experiment_key=experiment_key or None,
            **fields,
        )

    def __call__(self, identity, task, selected_agent):
        return self.route(identity, task)["selected_model"]


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
        "media",
        "workflow_limits",
        "workspace_files",
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
    if "media" in data:
        if not isinstance(data["media"], dict):
            raise ValueError("Media profiles must be a named mapping")
        options["media_options"] = {"profiles": data["media"]}
    if data.get("research"):
        options["research_options"] = {"config": data["research"]}
    if "workflow_limits" in data:
        from nexus.core.studio_workflow_policy import workflow_limits

        options["workflow_limits"] = workflow_limits(data["workflow_limits"])
    if "workspace_files" in data:
        options["workspace_file_options"] = {"roots": data["workspace_files"]}
    return options

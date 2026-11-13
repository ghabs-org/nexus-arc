"""Explicit OpenAPI health selection, real HTTP, privacy and release attribution."""

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_openapi import validate_openapi
from nexus.core.studio_resource_health import StudioResourceHealth, probe_action
from tests.test_studio_build_lifecycle import proposal, seed
from tests.test_studio_resource_health import request


def data(url="https://example.test"):
    return {
        "type": "openapi",
        "url": url,
        "spec": {
            "openapi": "3.0.3",
            "paths": {
                "/health": {"get": {"operationId": "health"}, "post": {"operationId": "mutate"}},
                "/details": {
                    "get": {
                        "operationId": "details",
                        "parameters": [
                            {
                                "name": "id",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "string"},
                            }
                        ],
                    }
                },
            },
        },
        "operations": ["health", "mutate", "details"],
        "actions": ["read", "list_operations", "invoke"],
        "health_operation": "health",
    }


@pytest.mark.parametrize(
    "mode",
    ["unknown", "unselected", "write", "required", "path", "body", "permission", "type", "length"],
)
def test_invalid_health_selection_rejects_before_saving(tmp_path, mode):
    value = data()
    if mode == "unknown":
        value["health_operation"] = "missing"
    if mode == "unselected":
        value["operations"] = ["details"]
    if mode == "write":
        value["health_operation"] = "mutate"
    if mode == "required":
        value["health_operation"] = "details"
    if mode == "path":
        value["spec"]["paths"]["/health"]["parameters"] = [{"name": "id", "in": "path"}]
    if mode == "body":
        value["spec"]["paths"]["/health"]["get"]["requestBody"] = {"required": False}
    if mode == "permission":
        value["actions"] = ["invoke"]
    if mode == "type":
        value["health_operation"] = True
    if mode == "length":
        value["health_operation"] = "a" * 201
    studio = StudioService(tmp_path)
    try:
        with pytest.raises(ValueError, match="[Hh]ealth"):
            studio.catalog.save("connectors", {"name": "Rejected", "data": value})
        assert not studio.catalog.list("connectors")
    finally:
        studio.close()


@pytest.mark.parametrize("method", ["get", "head", "options"])
def test_read_health_normalization_is_additive(method):
    value = data()
    value["spec"]["paths"]["/health"] = {method: {"operationId": "health"}}
    value["operations"] = ["health"]
    assert validate_openapi(value)["health_operation"] == "health"
    value.pop("health_operation")
    old = validate_openapi(value)
    assert "health_operation" not in old
    assert validate_openapi({**value, "health_operation": ""}) == old
    assert probe_action("connectors:sample", {"data": old}) is None


@pytest.mark.parametrize("mode", ["success", "failure", "redirect", "oversized"])
def test_actual_http_health_discards_bodies_and_never_activates(tmp_path, monkeypatch, mode):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            body = b'{"private":"must-not-persist"}'
            if mode == "oversized":
                body = b"x" * 262145
            self.send_response(503 if mode == "failure" else 302 if mode == "redirect" else 200)
            if mode == "redirect":
                self.send_header("Location", "/mutate")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    studio = StudioService(tmp_path)
    monkeypatch.setenv("HEALTH_API_TOKEN", "synthetic-health-secret")
    try:
        value = data(f"http://127.0.0.1:{server.server_port}")
        value.update(allow_private=True, auth={"type": "bearer", "token_env": "HEALTH_API_TOKEN"})
        build = seed(
            studio,
            proposal(
                resources=[
                    {"kind": "connectors", "id": "service", "name": "Health service", "data": value}
                ]
            ),
        )
        health = StudioResourceHealth(studio.resource_lifecycle)
        key = next(
            c["key"]
            for c in studio.resource_lifecycle.inspect_build(build["id"])["components"]
            if c["key"].startswith("connectors:")
        )
        # Review, import and passive inspection never call the service.
        assert not seen
        before = copy.deepcopy(studio.resource_lifecycle.current(key))
        result = health.check(
            build["id"],
            request(build, key)
            | {"operation_id": "mutate", "arguments": {"operation_id": "mutate"}},
        )
        assert result["status"] == ("passed" if mode == "success" else "failed")
        assert result["action"] == "read"
        assert seen == [("/health", "Bearer synthetic-health-secret")]
        saved = json.dumps(studio.records("resource-health"))
        assert "must-not-persist" not in saved and "synthetic-health-secret" not in saved
        assert studio.resource_lifecycle.current(key) == before
        assert studio.resource_lifecycle.marker(key)["active"] is False
        # Changing health selection invalidates earlier evidence.
        record = studio.catalog.get("connectors", key.split(":")[1])
        record["data"].pop("health_operation")
        studio._write(studio.catalog._path("connectors", record["id"]), record)
        assert health.inspect(build, {"key": key, "managed": True})["status"] == "stale"
    finally:
        studio.close()
        server.shutdown()
        server.server_close()

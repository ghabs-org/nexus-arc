"""Catalog discovery stays honest, private and independent of optional adapters."""

import builtins
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from nexus.core.studio import StudioService
from nexus.core.studio_integrations import arc_capabilities, integration_catalog
from nexus.core.studio_team import NexusTeamPolicy
from nexus.studio import make_studio_handler


def test_discovery_does_not_import_adapters_or_resolve_secrets(monkeypatch):
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(
            ("nexus.core.auth", "nexus.adapters", "nexus.plugins", "redis", "telegram", "discord")
        ):
            pytest.fail("Catalog discovery imported an execution/auth dependency: " + name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setenv("GITHUB_TOKEN", "never-return-this-token")
    items = integration_catalog()
    names = {item["id"]: item for item in items}
    assert {
        "github",
        "gitlab",
        "telegram",
        "discord",
        "linkedin",
        "slack",
        "mcp",
        "s3",
        "gcs",
        "azure",
    } <= names.keys()
    assert len(items) == len(names) == 19
    assert names["github"]["status"] == "Ready to configure"
    assert names["github"]["studio_support"] == "studio"
    assert names["http"]["status"] == "Ready to configure"
    assert names["linkedin"]["status"] == "Campaign controls available"
    assert names["linkedin"]["studio_area"] == "campaigns"
    assert all(not item["active_transport"] and item["connection_count"] == 0 for item in items)
    assert "never-return-this-token" not in json.dumps(items)
    assert {item["id"] for item in arc_capabilities()} == {
        "memory",
        "checkpoints",
        "agents",
        "campaigns",
        "analytics",
        "transcription",
        "decisions",
        "budgets",
    }
    names["github"]["capabilities"].clear()
    assert integration_catalog()[0]["capabilities"]  # callers cannot mutate shared catalog state


def test_counts_reflect_saved_flags_without_claiming_network_connectivity(tmp_path):
    service = StudioService(tmp_path)
    try:
        saved = service.catalog.save(
            "connectors",
            {
                "name": "Local endpoint",
                "data": {"type": "http", "url": "https://example.test", "enabled": False},
            },
        )
        items = {item["id"]: item for item in integration_catalog(service)}
        assert items["http"]["connection_count"] == 1 and items["http"]["enabled_count"] == 0
        assert items["github"]["connection_count"] == 0
        assert service.operations.tools == {}
        service.catalog.archive("connectors", saved["id"], {"revision": saved["revision"]})
        assert (
            next(item for item in integration_catalog(service) if item["id"] == "http")[
                "connection_count"
            ]
            == 0
        )
    finally:
        service.close()


@pytest.mark.parametrize("shared", [False, True])
def test_inventory_http_routes_obey_existing_shared_auth_and_assets(tmp_path, shared):
    policy = None
    if shared:

        class NoSessions:
            def get_session_and_setup_status(self, session):
                return {"exists": False}

            def has_project_access(self, identity, project):
                return False

        policy = NexusTeamPolicy(
            project_key="sample",
            public_url="https://studio.example.test",
            login_url="https://studio.example.test/login",
            bindings={"owner": "owner"},
            manager=NoSessions(),
            environment_resolver=lambda identity: ({}, None),
        )
    service = StudioService(
        tmp_path / "project", team_policy=policy, state_dir=tmp_path / "private" if shared else None
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service, "test-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = {"Host": "studio.example.test" if shared else f"127.0.0.1:{server.server_port}"}
        for route in ["/api/integrations", "/api/arc-capabilities"]:
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}" + route, headers=headers
            )
            if shared:
                with pytest.raises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=3)
                assert error.value.code == 401
            else:
                with urllib.request.urlopen(request, timeout=3) as response:
                    assert json.load(response)["items"]
        asset = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/integrations.js", headers=headers
        )
        with urllib.request.urlopen(asset, timeout=3) as response:
            assert b"refreshIntegrationCatalog" in response.read()
        assert service.records("runs") == [] and service.catalog.list("connectors") == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        service.close()


def test_inventory_sources_are_shipped_framework_files():
    root = Path(__file__).resolve().parents[1]
    for item in integration_catalog() + arc_capabilities():
        for source in item["sources"]:
            assert source.startswith("nexus/") and (root / source).is_file()

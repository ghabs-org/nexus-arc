"""Authenticated discovery advertises routing metadata, never invented transports."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def metadata():
    return {
        "description": "Review supplied evidence",
        "version": "2.1.0",
        "documentationUrl": "https://example.invalid/docs",
        "provider": {"organization": "Local owner", "url": "https://example.invalid"},
        "skills": [
            {
                "id": "review",
                "name": "Review",
                "description": "Check evidence",
                "tags": ["evidence"],
                "examples": ["Review this claim"],
            }
        ],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
    }


@pytest.mark.parametrize("version", ["0.3", "1.0"])
def test_real_http_discovery_preserves_auth_and_protocol_capabilities(
    tmp_path, monkeypatch, version
):
    monkeypatch.setenv("DISCOVERY_FIXTURE_TOKEN", "synthetic-discovery-token-local-only")
    studio = StudioService(tmp_path)
    server = None
    try:
        agent = studio.save_definition(
            "agents",
            {
                "name": "Reviewer",
                "instructions": "Review evidence",
                "agent_card": metadata(),
            },
        )
        entry = studio.catalog.save(
            "entrypoints",
            {
                "name": "Private reviewer",
                "data": {
                    "target_kind": "agent",
                    "target_id": agent["id"],
                    "token_env": "DISCOVERY_FIXTURE_TOKEN",
                    "enabled": True,
                    "a2a_enabled": True,
                },
            },
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(studio, "csrf-fixture"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/api/a2a/{entry['id']}/agent-card.json"
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(url, timeout=5)
        assert denied.value.code == 403
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": "Bearer synthetic-discovery-token-local-only",
                "A2A-Version": version,
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            card = json.load(response)
        assert card["skills"] == metadata()["skills"]
        assert card["description"] == metadata()["description"]
        assert card["provider"] == metadata()["provider"]
        assert card["version"] == "2.1.0"
        assert card["name"] == "Private reviewer"
        assert card["capabilities"]["streaming"] is True
        assert card["capabilities"]["pushNotifications"] is False
        assert "securitySchemes" in card
        assert "text/plain" in card["defaultInputModes"]
        assert card["defaultInputModes"] != ["text"]
        assert studio.records("runs") == []
        assert studio.records("external-runs") == []
    finally:
        if server:
            server.shutdown()
            server.server_close()
        studio.close()


@pytest.mark.parametrize(
    "change",
    [
        {"documentationUrl": "https://user:secret@example.invalid"},
        {"documentationUrl": "javascript:alert(1)"},
        {"provider": {"organization": "Owner", "url": "http://example.invalid"}},
        {"version": {}},
        {"description": []},
        {"capabilities": {"pushNotifications": True}},
        {"skills": metadata()["skills"] * 2},
    ],
)
def test_invalid_routing_metadata_rejected_before_save(tmp_path, change):
    studio = StudioService(tmp_path)
    try:
        with pytest.raises(ValueError):
            studio.save_definition(
                "agents", {"name": "Bad card", "agent_card": metadata() | change}
            )
        assert studio.definitions("agents") == []
    finally:
        studio.close()

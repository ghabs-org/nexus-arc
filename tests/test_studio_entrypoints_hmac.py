import hashlib
import hmac
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def save(studio, kind, data, name=None):
    return studio.catalog.save(kind, {"name": name or f"Test {kind}", "data": data})


def test_hmac_entrypoint_validation_and_env_interpolation(tmp_path):
    service = StudioService(tmp_path)
    service.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    service.save_definition(
        "workflows",
        {
            "name": "Triage",
            "graph": {
                "nodes": [{"id": "n1", "name": "Reviewer", "agent_id": "reviewer", "prompt": "{task}"}],
                "edges": [{"source": "START", "target": "n1"}],
            },
        },
    )

    # Valid HMAC configuration with ${ENV_VAR} syntax
    entry = save(
        service,
        "entrypoints",
        {
            "target_kind": "workflow",
            "target_id": "triage",
            "auth_mode": "hmac",
            "hmacSecret": "${ACME_HELPDESK_WEBHOOK_SECRET}",
            "slug": "acme-helpdesk",
            "route_path": "/tickets",
            "hmac_header": "X-Hub-Signature-256",
            "hmac_algorithm": "sha256",
            "hmac_prefix": "sha256=",
            "hmac_encoding": "hex",
            "enabled": True,
        },
    )
    assert entry["data"]["token_env"] == "ACME_HELPDESK_WEBHOOK_SECRET"
    assert entry["data"]["slug"] == "acme-helpdesk"
    assert entry["data"]["route_path"] == "/tickets"
    assert entry["data"]["auth_mode"] == "hmac"

    # Reject invalid route path
    with pytest.raises(ValueError, match="Route path must start with '/'"):
        save(
            service,
            "entrypoints",
            {
                "target_kind": "workflow",
                "target_id": "triage",
                "auth_mode": "hmac",
                "token_env": "SECRET",
                "route_path": "tickets",
            },
        )

    # Reject invalid slug
    with pytest.raises(ValueError, match="Slug must be 1-64 characters"):
        save(
            service,
            "entrypoints",
            {
                "target_kind": "workflow",
                "target_id": "triage",
                "auth_mode": "hmac",
                "token_env": "SECRET",
                "slug": "Invalid Slug!",
            },
        )

    # Reject unsupported algorithm
    with pytest.raises(ValueError, match="Supported HMAC algorithms"):
        save(
            service,
            "entrypoints",
            {
                "target_kind": "workflow",
                "target_id": "triage",
                "auth_mode": "hmac",
                "token_env": "SECRET",
                "hmac_algorithm": "md5",
            },
        )


def test_hmac_entrypoint_authorize_unit(tmp_path, monkeypatch):
    service = StudioService(tmp_path)
    service.save_definition("agents", {"name": "Reviewer", "instructions": "Review"})
    service.save_definition(
        "workflows",
        {
            "name": "Triage",
            "graph": {
                "nodes": [{"id": "n1", "name": "Reviewer", "agent_id": "reviewer", "prompt": "{task}"}],
                "edges": [{"source": "START", "target": "n1"}],
            },
        },
    )

    entry = save(
        service,
        "entrypoints",
        {
            "target_kind": "workflow",
            "target_id": "triage",
            "auth_mode": "hmac",
            "token_env": "TEST_HMAC_KEY",
            "slug": "acme-helpdesk",
            "route_path": "/tickets",
            "hmac_header": "X-Hub-Signature-256",
            "hmac_algorithm": "sha256",
            "hmac_prefix": "sha256=",
            "hmac_encoding": "hex",
            "enabled": True,
        },
    )

    secret = "super-secret-hmac-key-for-testing-12345"
    monkeypatch.setenv("TEST_HMAC_KEY", secret)

    body = b'{"ticket_id": "ACME-1001", "summary": "Outage"}'
    valid_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    # Success with exact header
    assert service.operations.authorize_entrypoint(
        entry["id"],
        headers={"X-Hub-Signature-256": valid_sig},
        raw_body=body,
    )

    # Case-insensitive header lookup
    assert service.operations.authorize_entrypoint(
        entry["id"],
        headers={"x-hub-signature-256": valid_sig},
        raw_body=body,
    )

    # Wrong signature fails
    assert not service.operations.authorize_entrypoint(
        entry["id"],
        headers={"x-hub-signature-256": "sha256=wrongsignature"},
        raw_body=body,
    )

    # Missing header fails
    assert not service.operations.authorize_entrypoint(
        entry["id"],
        headers={"Authorization": "Bearer something"},
        raw_body=body,
    )

    # Corrupted body fails
    assert not service.operations.authorize_entrypoint(
        entry["id"],
        headers={"x-hub-signature-256": valid_sig},
        raw_body=body + b"extra",
    )


def test_hmac_entrypoint_http_gateway_invocation(tmp_path, monkeypatch):
    service = StudioService(tmp_path)
    service.save_definition("agents", {"name": "Reviewer", "instructions": "Helpful triage"})

    secret = "acme-webhook-secret-key-32-chars-long!"
    monkeypatch.setenv("ACME_SECRET_ENV", secret)

    entry = save(
        service,
        "entrypoints",
        {
            "target_kind": "agent",
            "target_id": "reviewer",
            "auth_mode": "hmac",
            "token_env": "ACME_SECRET_ENV",
            "slug": "acme-tickets",
            "route_path": "/tickets",
            "hmac_header": "X-Hub-Signature-256",
            "hmac_algorithm": "sha256",
            "hmac_prefix": "sha256=",
            "hmac_encoding": "hex",
            "enabled": True,
        },
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_studio_handler(service, "test-csrf"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    host, port = server.server_address

    def request(path, payload, headers=None):
        conn = HTTPConnection(host, port)
        body = json.dumps(payload).encode() if isinstance(payload, dict) else payload
        h = {"Host": f"{host}:{port}", "Content-Type": "application/json"}
        if headers:
            h.update(headers)
        conn.request("POST", path, body, h)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, res.headers, data

    try:
        body = json.dumps({"ticket_id": "ACME-4040", "priority": "P1"}).encode()
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        # 1. Successful POST to /gw/<slug>/<route_path>
        status, _, resp_bytes = request(
            "/gw/acme-tickets/tickets",
            body,
            {"X-Hub-Signature-256": sig},
        )
        assert status == 202, resp_bytes
        resp_json = json.loads(resp_bytes)
        assert resp_json.get("status") in {"queued", "running", "completed"}
        assert resp_json.get("kind") == "chat"
        assert resp_json.get("id")

        # 2. Successful POST to /api/webhooks/<identity>/tickets
        status2, _, resp_bytes2 = request(
            f"/api/webhooks/{entry['id']}/tickets",
            body,
            {"X-Hub-Signature-256": sig},
        )
        assert status2 == 202, resp_bytes2

        # 3. Invalid signature returns 403
        status_bad, _, _ = request(
            "/gw/acme-tickets/tickets",
            body,
            {"X-Hub-Signature-256": "sha256=invalidsig"},
        )
        assert status_bad == 403

        # 4. Missing signature returns 403
        status_missing, _, _ = request(
            "/gw/acme-tickets/tickets",
            body,
            {},
        )
        assert status_missing == 403

        # 5. Wrong path suffix returns 404
        status_404, _, _ = request(
            "/gw/acme-tickets/other",
            body,
            {"X-Hub-Signature-256": sig},
        )
        assert status_404 == 404

        # 6. Unknown slug returns 404
        status_unknown, _, _ = request(
            "/gw/unknown-slug/tickets",
            body,
            {"X-Hub-Signature-256": sig},
        )
        assert status_unknown == 404

    finally:
        server.shutdown()
        server.server_close()
        thread.join()

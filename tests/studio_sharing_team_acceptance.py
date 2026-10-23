"""Full Studio sharing acceptance with disposable users, state and files.

Run: PYTHONPATH=. python tests/studio_sharing_team_acceptance.py
Visit /acceptance for fixture sign-in and expiry controls. Studio and shared.html
use production scripts and API routes. The loopback test front door substitutes
the configured HTTPS Host/Origin, as the existing HTTP team fixtures do. It does
not prove TLS, secure-cookie deployment or a real identity provider.
"""

import base64
import html
import json
import tempfile
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from nexus.core.studio import StudioService
from nexus.core.studio_team import NexusTeamPolicy
from nexus.studio import make_studio_handler
from tests.test_studio_team import SESSIONS, NexusIdentity


def create_fixture(root):
    manager = NexusIdentity()
    policy = NexusTeamPolicy(
        project_key="sample",
        public_url="https://studio.example.test",
        login_url="https://studio.example.test/acceptance",
        bindings={"alice": "builder", "bob": "operator", "viewer": "viewer", "owner": "owner"},
        manager=manager,
        environment_resolver=lambda _: ({}, None),
    )
    studio = StudioService(
        Path(root) / "workspace", state_dir=Path(root) / "private-state", team_policy=policy
    )
    timestamp = datetime.now(UTC).isoformat()
    studio._write(
        studio._path("sessions", "sharing-demo"),
        {
            "id": "sharing-demo",
            "owner_id": "alice",
            "title": "Sharing acceptance",
            "provider": "codex",
            "created_at": timestamp,
            "updated_at": timestamp,
            "messages": [
                {"role": "user", "content": "Summarize the synthetic policy."},
                {
                    "role": "assistant",
                    "content": "## Policy review\n\n**Approved** for testing.\n\n| Item | Status |\n|---|---|\n| Sample | Ready |\n\n```text\nsynthetic evidence\n```\n\n<script>window.snapshotInjected=true</script>[unsafe](javascript:alert(1))",
                },
            ],
        },
    )
    for identity, owner in [("selected", "alice"), ("omitted", "alice"), ("foreign", "bob")]:
        studio.catalog.save(
            "artifacts",
            {
                "id": identity,
                "name": identity.title(),
                "requester_nexus_id": owner,
                "data": {
                    "filename": identity + ".txt",
                    "content_base64": base64.b64encode(
                        b"Pinned original evidence" if identity == "selected" else b"Never shared"
                    ).decode(),
                },
            },
        )
    alice = {"nexus_id": "alice", "role": "builder"}
    private = studio.collaboration.share(
        {"session_id": "sharing-demo", "recipients": ["bob"], "artifact_ids": ["selected"]}, alice
    )
    expired = studio.collaboration.share(
        {"session_id": "sharing-demo", "recipients": ["bob"]}, alice
    )
    expired["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    studio._write(studio._path("conversation-shares", expired["id"]), expired)
    revoked = studio.collaboration.share(
        {"session_id": "sharing-demo", "recipients": ["bob"]}, alice
    )
    studio.collaboration.revoke(revoked["id"], alice)
    fixtures = {"private": private["id"], "expired": expired["id"], "revoked": revoked["id"]}

    class Handler(make_studio_handler(studio, "synthetic-sharing")):
        def fixture_origin(self):
            # Confined to this loopback-only synthetic fixture. Production origin
            # checks are not relaxed or overridden in the application handler.
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != urlparse(origin).netloc:
                self.send_payload(403, {"error": "Use the fixture's loopback origin"})
                return False
            if self.headers.get("Origin") not in {None, origin}:
                self.send_payload(403, {"error": "Cross-origin fixture request denied"})
                return False
            self.headers.replace_header("Host", policy.host)
            if self.headers.get("Origin"):
                self.headers.replace_header("Origin", policy.public_url)
            return True

        def do_GET(self):  # noqa: N802 - HTTP handler protocol
            if not self.fixture_origin():
                return
            route = urlparse(self.path).path
            if route == "/acceptance/state":
                return self.send_payload(200, fixtures)
            if route.startswith("/acceptance/as/"):
                user = route.rsplit("/", 1)[1]
                if user == "anonymous":
                    cookie = "nexus_web_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict"
                elif user in policy.bindings:
                    session = next(key for key, identity in SESSIONS.items() if identity == user)
                    cookie = f"nexus_web_session={session}; Path=/; HttpOnly; SameSite=Strict"
                else:
                    return self.send_payload(404, {"error": "Unknown synthetic user"})
                return self.send_bytes(
                    303, b"", "text/plain", headers={"Location": "/", "Set-Cookie": cookie}
                )
            if route == "/acceptance":
                accounts = " ".join(
                    f'<a href="/acceptance/as/{name}">Sign in as {name}</a>'
                    for name in [*policy.bindings, "anonymous"]
                )
                links = "".join(
                    f'<p><a href="/shared.html#private/{identity}">{name} fixture</a></p>'
                    for name, identity in fixtures.items()
                )
                snapshots = "".join(
                    f'<form method="post" action="/acceptance/expire/{record["id"]}"><button>Expire {html.escape(record["id"])}</button></form>'
                    for record in studio.records("conversation-shares", limit=None)
                )
                body = f'<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sharing acceptance controls</title><link rel="stylesheet" href="/studio.css"></head><body class="shared-page"><main><h1>Synthetic sharing controls</h1><p>{accounts}</p>{links}<h2>Advance a snapshot past expiry</h2>{snapshots}</main></body></html>'
                return self.send_bytes(200, body.encode(), "text/html; charset=utf-8")
            return super().do_GET()

        def do_POST(self):  # noqa: N802 - HTTP handler protocol
            if not self.fixture_origin():
                return
            route = urlparse(self.path).path
            if route.startswith("/acceptance/expire/"):
                identity = route.rsplit("/", 1)[1]
                with studio.lock:
                    path = studio._path("conversation-shares", identity)
                    record = studio._read(path)
                    record["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
                    studio._write(path, record)
                return self.send_bytes(303, b"", "text/plain", headers={"Location": "/acceptance"})
            return super().do_POST()

    return studio, Handler, fixtures


def main():
    with tempfile.TemporaryDirectory(prefix="nexus-sharing-team-") as root:
        studio, handler, fixtures = create_fixture(root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        print(
            json.dumps({"url": f"http://127.0.0.1:{server.server_port}/acceptance", **fixtures}),
            flush=True,
        )
        try:
            server.serve_forever()
        finally:
            server.server_close()
            studio.close()


if __name__ == "__main__":
    main()

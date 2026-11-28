"""Capture signed Slack files privately using only a local HTTP contract peer."""

import argparse
import hashlib
import hmac
import json
import os
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def exercise(base):
    parsed = urlsplit(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Use only the local Slack contract simulation")
    original_session = requests.Session

    class Transport(HTTPAdapter):
        def send(self, request, **kwargs):
            target = urlsplit(request.url)
            if target.hostname not in {"slack.com", "files.slack.com"}:
                raise AssertionError("Unexpected remote request")
            request.url = base + target.path + ("?" + target.query if target.query else "")
            return super().send(request, **kwargs)

    class LocalSession(original_session):
        def __init__(self):
            super().__init__()
            self.mount("https://", Transport())

    observations = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            observations.append(context.prompt)
            return AgentResult(success=True, output="Private evidence inspected")

    with (
        tempfile.TemporaryDirectory(prefix="nexus-slack-file-example-") as root,
        patch.dict(
            os.environ, {"EVENT_SECRET": "s" * 32, "SLACK_FILES_TOKEN": "synthetic-slack-token"}
        ),
    ):
        studio = StudioService(root, provider_factory=lambda _: Provider())
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_studio_handler(studio, "local-files-csrf")
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        local = original_session()
        local.trust_env = False
        try:
            studio.save_definition(
                "agents", {"name": "Reviewer", "instructions": "Inspect attached source files."}
            )
            studio.catalog.save(
                "connectors",
                {
                    "name": "Reader",
                    "data": {
                        "type": "slack",
                        "channel": "CSELECTED",
                        "token_env": "SLACK_FILES_TOKEN",
                        "enabled": True,
                        "actions": ["status"],
                    },
                },
            )
            studio.catalog.save(
                "entrypoints",
                {
                    "name": "Incoming",
                    "data": {
                        "type": "slack",
                        "target_kind": "agent",
                        "target_id": "reviewer",
                        "token_env": "EVENT_SECRET",
                        "enabled": True,
                        "identity_map": {"alice": "desktop", "bob": "desktop"},
                        "allowed_channels": ["CSELECTED"],
                        "threaded_conversations": True,
                        "capture_slack_files": True,
                        "notification_connector_id": "reader",
                    },
                },
            )

            def calls():
                return local.get(base + "/fixture/calls", timeout=5).json()["calls"]

            def send(number, file_id, sender="alice"):
                raw = json.dumps(
                    {
                        "type": "event_callback",
                        "event_id": f"file-{number}",
                        "team_id": "TSELECTED",
                        "event": {
                            "type": "message",
                            "subtype": "file_share",
                            "user": sender,
                            "channel": "CSELECTED",
                            "ts": f"17000000{number:02}.000001",
                            "files": [
                                {"id": file_id, "url_private_download": "https://evil.invalid/leak"}
                            ],
                        },
                    }
                ).encode()
                stamp = str(int(time.time()))
                sig = (
                    "v0="
                    + hmac.new(
                        b"s" * 32, b"v0:" + stamp.encode() + b":" + raw, hashlib.sha256
                    ).hexdigest()
                )
                response = local.post(
                    f"http://127.0.0.1:{server.server_port}/api/channel-events/incoming",
                    data=raw,
                    headers={
                        "X-Slack-Request-Timestamp": stamp,
                        "X-Slack-Signature": sig,
                        "Content-Type": "application/json",
                    },
                    timeout=5,
                )
                assert response.status_code == 200, response.text

            before = len(calls())
            with patch("nexus.core.studio_slack_files.requests.Session", LocalSession):
                send(1, "FFILE1")
                assert len(calls()) == before
                studio.messaging.tick()
                event = studio.records("channel-events")[0]
                assert event["status"] == "slack_files_ready", event
                artifact = studio.catalog.get("artifacts", event["slack_artifact_refs"][0]["id"])
                assert studio.artifacts.get(artifact) == b"private Slack evidence"
                studio.messaging.tick()
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    run = studio.records("runs")[0]
                    if run["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.01)
                assert run["status"] == "completed", run
                assert (
                    run["artifact_refs"] == event["slack_artifact_refs"]
                    and "evidence.txt" in observations[0]
                )
                send(1, "FFILE1")
                studio.messaging.tick()
                assert len(calls()) == before + 2
                send(2, "FIMAGE1", "bob")
                studio.messaging.tick()
                assert any(
                    studio.artifacts.get(a).startswith(b"\x89PNG")
                    for a in studio.catalog.list("artifacts")
                )
                # Keep the image private in Library; provider-specific vision acceptance is separate.
                image_event = next(
                    e
                    for e in studio.records("channel-events")
                    if e.get("slack_files", {}).get("ids") == ["FIMAGE1"]
                )
                image_event["status"] = "completed"
                studio._write(studio._path("channel-events", image_event["id"]), image_event)
                for number, file_id in enumerate(
                    ["FREDIRECT", "FERROR", "FHTML", "FBIG", "FTRUNC"], 3
                ):
                    send(number, file_id)
                    studio.messaging.tick()
                    failed = next(
                        e
                        for e in studio.records("channel-events")
                        if e.get("slack_files", {}).get("ids") == [file_id]
                    )
                    assert failed["status"] == "failed", failed
                    assert "PRIVATE_PROVIDER_DETAIL" not in json.dumps(failed)
                assert (
                    len(studio.records("runs")) == 1
                    and len(
                        [
                            a
                            for a in studio.catalog.list("artifacts")
                            if a["id"].startswith("slack-file-")
                        ]
                    )
                    == 2
                ), {
                    "runs": [(r["task"], r["status"]) for r in studio.records("runs")],
                    "files": [a["name"] for a in studio.catalog.list("artifacts")],
                }
            traffic = calls()[before:]
            assert len(traffic) == 11, traffic
            assert all(
                item["method"] == "/api/files.info" or item["method"].startswith("/files-pri/")
                for item in traffic
            )
            return {
                "http_requests": 11,
                "private_artifacts": 2,
                "pinned_runs": 1,
                "duplicates_suppressed": True,
                "redirects_blocked": True,
                "provider_errors_sanitized": True,
                "live_verified": False,
            }
        finally:
            local.close()
            server.shutdown()
            server.server_close()
            worker.join(2)
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    print(json.dumps(exercise(parser.parse_args().base), indent=2))

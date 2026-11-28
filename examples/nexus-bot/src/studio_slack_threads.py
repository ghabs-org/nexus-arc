"""Send signed local Slack events through Studio and return real SDK thread replies.

Start postman/simulations/studio-slack.sim.yaml separately and pass its local
member URL with --base. Uses only synthetic credentials and disposable state.
"""

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
from urllib.parse import urlparse

import requests
from slack_sdk import WebClient

from nexus.core.models import AgentResult
from nexus.core.studio import StudioService
from nexus.studio import make_studio_handler


def exercise(base):
    peer = urlparse(base)
    if peer.scheme != "http" or peer.hostname != "127.0.0.1" or peer.username or peer.password:
        raise ValueError("Use only the local Slack contract simulation")
    prompts = []

    class Provider:
        async def check_availability(self):
            return True

        async def execute_agent(self, context):
            prompts.append(context.prompt)
            return AgentResult(success=True, output=f"Reviewed answer {len(prompts)}")

    def client(**kwargs):
        assert kwargs["retry_handlers"] == []
        return WebClient(base_url=base + "/api/", **kwargs)

    def calls():
        response = requests.get(base + "/fixture/calls", timeout=5)
        response.raise_for_status()
        return response.json()["calls"]

    with (
        tempfile.TemporaryDirectory(prefix="nexus-slack-threads-") as root,
        patch.dict(
            os.environ,
            {
                "SLACK_THREAD_SIGNING": "s" * 32,
                "SLACK_THREAD_TOKEN": "synthetic-slack-token",
                "HTTPS_PROXY": "",
                "HTTP_PROXY": "",
                "https_proxy": "",
                "http_proxy": "",
                "ALL_PROXY": "",
            },
        ),
        patch("nexus.adapters.notifications.slack.WebClient", client),
    ):
        studio = StudioService(root, provider_factory=lambda _: Provider())
        server = None
        try:
            studio.save_definition(
                "agents", {"name": "Reviewer", "instructions": "Review the provided evidence."}
            )
            studio.catalog.save(
                "connectors",
                {
                    "name": "Reply",
                    "data": {
                        "type": "slack",
                        "channel": "CSELECTED",
                        "token_env": "SLACK_THREAD_TOKEN",
                        "actions": ["send"],
                        "enabled": True,
                    },
                },
            )
            record = studio.catalog.save(
                "entrypoints",
                {
                    "name": "Incoming",
                    "data": {
                        "type": "slack",
                        "target_kind": "agent",
                        "target_id": "reviewer",
                        "token_env": "SLACK_THREAD_SIGNING",
                        "identity_map": {"alice": "desktop", "bob": "desktop"},
                        "allowed_channels": ["CSELECTED"],
                        "notification_connector_id": "reply",
                        "auto_reply": True,
                        "threaded_conversations": True,
                        "enabled": True,
                    },
                },
            )
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), make_studio_handler(studio, "thread-example-local-csrf")
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"

            def send(number, sender="alice", tampered=False):
                event = {
                    "type": "message",
                    "user": sender,
                    "channel": "CSELECTED",
                    "ts": f"17000000{number:02}.000001",
                    "text": f"Private Alice question {number}"
                    if sender == "alice"
                    else "Separate Bob question",
                }
                if number > 1:
                    event["thread_ts"] = "1700000001.000001"
                raw = json.dumps(
                    {
                        "type": "event_callback",
                        "team_id": "TSELECTED",
                        "event_id": f"synthetic-{number}",
                        "event": event,
                    }
                ).encode()
                timestamp = str(int(time.time()))
                signature = (
                    "v0="
                    + hmac.new(
                        b"s" * 32, b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256
                    ).hexdigest()
                )
                response = requests.post(
                    origin + "/api/channel-events/incoming",
                    data=raw + (b" " if tampered else b""),
                    headers={
                        "Content-Type": "application/json",
                        "X-Slack-Request-Timestamp": timestamp,
                        "X-Slack-Signature": signature,
                    },
                    timeout=5,
                )
                assert response.status_code == (403 if tampered else 200), response.text

            def finish(count):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    studio.messaging.tick()
                    runs = studio.records("runs")
                    if (
                        len(runs) == count
                        and all(r["status"] == "completed" for r in runs)
                        and all(
                            e["status"] == "completed" for e in studio.records("channel-events")
                        )
                    ):
                        return runs[0]
                    time.sleep(0.01)
                raise AssertionError("Threaded run did not complete")

            before = len(calls())
            send(1)
            first = finish(1)
            send(1)
            studio.messaging.tick()
            assert len(calls()) == before + 1
            send(2)
            second = finish(2)
            assert first["session_id"] == second["session_id"]
            assert "Private Alice question 1" in prompts[1] and "Reviewed answer 1" in prompts[1]
            send(3, sender="bob")
            third = finish(3)
            assert (
                third["session_id"] != first["session_id"]
                and "Private Alice question" not in prompts[2]
            )
            send(4, tampered=True)
            assert len(studio.records("channel-events")) == 3
            send(4)
            record = studio.catalog.get("entrypoints", record["id"])
            studio.catalog.save(
                "entrypoints",
                {**record, "data": {**record["data"], "identity_map": {"bob": "desktop"}}},
            )
            studio.messaging.tick()
            assert len(studio.records("runs")) == 3
            delivered = calls()[before:]
            assert len(delivered) == 3
            assert all(
                item["channel"] == "CSELECTED"
                and item["thread_ts"] == "1700000001.000001"
                and item["reply_broadcast"] is False
                for item in delivered
            )
            assert [item["text"] for item in delivered] == [
                "Reviewed answer 1",
                "Reviewed answer 2",
                "Reviewed answer 3",
            ]
            return {
                "sdk_replies": 3,
                "model_calls": 3,
                "continued_history": True,
                "separate_user_history": True,
                "duplicate_suppressed": True,
                "stale_mapping_blocked": True,
                "live_verified": False,
            }
        finally:
            if server:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    print(json.dumps(exercise(parser.parse_args().base), indent=2))

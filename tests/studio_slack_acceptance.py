"""Real optional SDK/HTTP acceptance. Missing prerequisites fail, never skip."""

import asyncio
import json
import os
import runpy
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from slack_sdk import WebClient

from nexus.core.studio import StudioService


def test_reviewed_selected_slack_messages_use_real_sdk_http():
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_slack.py")
    )
    assert example["exercise"](os.environ["NEXUS_ACCEPTANCE_SLACK_URL"]) == {
        "requests": 10,
        "review_required": True,
        "selected_channel_only": True,
        "long_message_sections": 3,
        "failures_not_retried": True,
        "live_verified": False,
    }


def test_signed_thread_events_continue_private_history_and_use_real_sdk_replies():
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_slack_threads.py")
    )
    assert example["exercise"](os.environ["NEXUS_ACCEPTANCE_SLACK_URL"]) == {
        "sdk_replies": 3,
        "model_calls": 3,
        "continued_history": True,
        "separate_user_history": True,
        "duplicate_suppressed": True,
        "stale_mapping_blocked": True,
        "live_verified": False,
    }


def test_lost_response_after_effect_is_not_replayed(tmp_path):
    # A proxy may turn a dropped upstream socket into HTTP 502, so this case
    # exercises an actual connection close directly at the SDK's HTTP peer.
    effects = []

    class DropAfterEffect(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            effects.append(json.loads(self.rfile.read(size)))
            self.close_connection = True
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DropAfterEffect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    studio = StudioService(tmp_path)
    try:
        studio.catalog.save(
            "connectors",
            {
                "name": "Slack",
                "data": {
                    "type": "slack",
                    "channel": "CSELECTED",
                    "token_env": "SLACK_DROP_TOKEN",
                    "enabled": True,
                    "actions": ["send"],
                },
            },
        )

        def client(**kwargs):
            return WebClient(base_url=f"http://127.0.0.1:{server.server_port}/api/", **kwargs)

        with (
            patch.dict(
                os.environ,
                {
                    "SLACK_DROP_TOKEN": "synthetic-drop-key",
                    "HTTPS_PROXY": "",
                    "HTTP_PROXY": "",
                    "https_proxy": "",
                    "http_proxy": "",
                    "ALL_PROXY": "",
                },
            ),
            patch("nexus.adapters.notifications.slack.WebClient", client),
        ):
            try:
                asyncio.run(
                    studio.connections.execute(
                        "slack",
                        {
                            "action": "send",
                            "confirm": True,
                            "arguments": {"text": "Apply once, then lose the response"},
                        },
                    )
                )
            except ValueError as error:
                assert "recorded remote outcome" in str(error)
            else:
                raise AssertionError("A lost response must report an uncertain failure")
        assert len(effects) == 1 and effects[0]["channel"] == "CSELECTED"
        assert not studio.records("runs")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        studio.close()

"""Verify reviewed Slack sends/updates against the local contract simulation.

Start postman/simulations/studio-slack.sim.yaml separately. This consumer uses
disposable state, synthetic credentials and the real installed Slack SDK.
"""

import argparse
import asyncio
import json
import os
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

import requests
from slack_sdk import WebClient

from nexus.core.studio import StudioService


def exercise(base):
    url = urlparse(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("This example accepts only the local Slack simulation")

    def calls():
        response = requests.get(base + "/fixture/calls", timeout=5)
        response.raise_for_status()
        return response.json()["calls"]

    def client(**kwargs):
        # Only the destination changes. Encoding, response parsing and retry
        # behavior use the actual SDK selected by the framework constructor.
        assert kwargs["retry_handlers"] == []
        return WebClient(base_url=base + "/api/", **kwargs)

    with (
        tempfile.TemporaryDirectory(prefix="nexus-slack-") as directory,
        patch.dict(
            os.environ,
            {
                "SLACK_EXAMPLE_TOKEN": "synthetic-slack-token",
                "HTTPS_PROXY": "",
                "HTTP_PROXY": "",
                "https_proxy": "",
                "http_proxy": "",
                "ALL_PROXY": "",
            },
        ),
        patch("nexus.adapters.notifications.slack.WebClient", client),
    ):
        studio = StudioService(directory)
        try:
            start = len(calls())
            row = studio.catalog.save(
                "connectors",
                {
                    "name": "Slack evidence",
                    "data": {
                        "type": "slack",
                        "channel": "CSELECTED",
                        "token_env": "SLACK_EXAMPLE_TOKEN",
                        "enabled": False,
                        "actions": ["send", "update_message"],
                    },
                },
            )

            def invoke(action="send", *, confirm=True, **arguments):
                return asyncio.run(
                    studio.connections.execute(
                        row["id"], {"action": action, "confirm": confirm, "arguments": arguments}
                    )
                )

            def denied(operation, error):
                try:
                    operation()
                except error as failure:
                    assert "PRIVATE_PROVIDER_DETAIL" not in str(failure)
                    assert "synthetic-slack-token" not in str(failure)
                else:
                    raise AssertionError("An unpermitted Slack action succeeded")

            denied(lambda: invoke(text="Disabled"), ValueError)
            row = studio.catalog.save(
                "connectors", {**row, "data": {**row["data"], "enabled": True}}
            )
            denied(lambda: invoke(text="Unreviewed", confirm=False), ValueError)
            denied(
                lambda: asyncio.run(
                    studio.connections.tool(row["id"])(
                        action="send", arguments={"text": "No self-approval"}, confirm=True
                    )
                ),
                PermissionError,
            )
            assert len(calls()) == start
            timestamp = invoke(
                text="Reviewed message",
                buttons=[{"label": "Review", "callback_data": "review-selected"}],
            )
            assert timestamp == "1700000000.000001"
            invoke("update_message", message_id=timestamp, text="Reviewed update")
            invoke(
                "update_message", message_id="CSELECTED:" + timestamp, text="Same selected channel"
            )
            before = len(calls())
            denied(
                lambda: invoke(
                    "update_message", message_id="COTHER:" + timestamp, text="Forbidden destination"
                ),
                PermissionError,
            )
            denied(
                lambda: invoke(
                    "update_message", message_id="not-a-timestamp", text="Invalid update"
                ),
                ValueError,
            )
            assert len(calls()) == before
            text = "Evidence " * 1000
            assert invoke(text=text) == timestamp
            for action in ["send", "update_message"]:
                for case in ["reject", "rate-limit", "server-error"]:
                    before = len(calls())
                    arguments = {"text": "fixture:" + case}
                    if action == "update_message":
                        arguments["message_id"] = timestamp
                    denied(
                        lambda action=action, arguments=arguments: invoke(action, **arguments),
                        ValueError,
                    )
                    assert len(calls()) == before + 1
            recorded = calls()[start:]
            assert len(recorded) == 10 and all(item["channel"] == "CSELECTED" for item in recorded)
            assert [item["method"] for item in recorded[:3]] == [
                "/api/chat.postMessage",
                "/api/chat.update",
                "/api/chat.update",
            ]
            sections = recorded[3]["blocks"]
            assert "".join(section["text"]["text"] for section in sections) == text
            assert (
                len(sections) == 3
                and max(len(section["text"]["text"]) for section in sections) <= 3000
            )
            assert recorded[0]["blocks"][-1]["elements"][0]["value"] == "review-selected"
            assert studio.catalog.get("connectors", row["id"]) == row
            return {
                "requests": 10,
                "review_required": True,
                "selected_channel_only": True,
                "long_message_sections": 3,
                "failures_not_retried": True,
                "live_verified": False,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    print(json.dumps(exercise(parser.parse_args().base), indent=2))

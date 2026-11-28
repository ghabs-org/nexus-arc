"""Exercise the catalog's reviewed SendGrid connector against a local simulation.

Start postman/simulations/studio-sendgrid.sim.yaml on port 49871 first.
The example refuses non-loopback destinations and uses only a synthetic API key.
"""

import argparse
import asyncio
import os
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

import requests

from nexus.core.studio import StudioService


def exercise(base="http://127.0.0.1:49871/mail"):
    url = urlparse(base)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or url.username or url.password:
        raise ValueError("This example accepts only its local HTTP simulation")
    with (
        tempfile.TemporaryDirectory(prefix="nexus-sendgrid-") as directory,
        patch.dict(os.environ, {"EXAMPLE_SENDGRID_KEY": "synthetic-sendgrid-key"}),
    ):
        studio = StudioService(directory)
        try:
            record = studio.recipes.configure(
                "send-grid", {"token_env": "EXAMPLE_SENDGRID_KEY"}, None
            )
            assert not record["data"]["enabled"]
            # Enable this disposable connector only, after selecting its exact operation.
            record = studio.catalog.save(
                "connectors",
                {
                    **record,
                    "data": {**record["data"], "url": base, "allow_private": True, "enabled": True},
                },
            )
            message = {
                "personalizations": [{"to": [{"email": "recipient@example.invalid"}]}],
                "from": {"email": "sender@example.invalid"},
                "subject": "Synthetic reviewed message",
                "content": [{"type": "text/html", "value": "<p>Local fixture only.</p>"}],
            }

            def attempts():
                response = requests.get(base + "/fixture/attempts", timeout=5)
                response.raise_for_status()
                return response.json()["attempts"]

            def invoke(body, confirm=False):
                return asyncio.run(
                    studio.connections.execute(
                        record["id"],
                        {
                            "action": "invoke",
                            "confirm": confirm,
                            "arguments": {"operation_id": "sendMail", "body": body},
                        },
                    )
                )

            before = attempts()
            try:
                invoke(message)
            except ValueError as error:
                assert "confirm" in str(error)
            else:
                raise AssertionError("Send bypassed review")
            assert attempts() == before
            try:
                asyncio.run(
                    studio.connections.tool(record["id"])(
                        action="invoke",
                        arguments={"operation_id": "sendMail", "body": message},
                        confirm=True,
                    )
                )
            except PermissionError:
                pass
            else:
                raise AssertionError("Model self-approved sending")
            assert attempts() == before
            malformed = {**message, "personalizations": [{"to": []}]}
            try:
                invoke(malformed, confirm=True)
            except ValueError:
                pass
            else:
                raise AssertionError("Malformed recipient reached provider")
            assert attempts() == before
            assert invoke(message, confirm=True) == {}
            template = {
                "from": message["from"],
                "template_id": "d-fixture",
                "personalizations": [
                    {**message["personalizations"][0], "dynamic_template_data": {"name": "Fixture"}}
                ],
            }
            assert invoke(template, confirm=True) == {}
            for subject in ("fixture:reject", "fixture:redirect"):
                before_failure = attempts()
                try:
                    invoke({**message, "subject": subject}, confirm=True)
                except ValueError as error:
                    assert "recorded remote outcome before retrying" in str(error)
                    assert "private" not in str(error)
                else:
                    raise AssertionError("Provider failure was reported as accepted")
                assert attempts() == before_failure + 1, "Mutation was retried"
            assert "synthetic-sendgrid-key" not in str(
                studio.catalog.get("connectors", record["id"])
            )
            return {
                "review_required": True,
                "html_accepted": True,
                "template_accepted": True,
                "failures_not_retried": True,
                "delivery_verified": False,
                "requests": attempts() - before,
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:49871/mail")
    print(exercise(parser.parse_args().base))

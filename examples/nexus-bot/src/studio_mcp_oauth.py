"""Consume delegated MCP configuration and exercise token I/O using a local fixture.

Run a separate Postman studio-oauth simulation first. This consumer uses only
synthetic credentials and a temporary workspace. Real sign-in happens in Studio's
connector editor with a pre-registered provider client and existing Nexus key.
"""

import argparse
import json
import tempfile
from pathlib import Path

from nexus.core import studio_oauth_client as client
from nexus.core.studio import StudioService
from nexus.core.studio_oauth import StudioOAuth


def prepare(studio, base):
    return studio.catalog.save(
        "connectors",
        {
            "name": "Delegated MCP example",
            "data": {
                "type": "mcp",
                "transport": "streamable-http",
                "url": base + "/mcp",
                "allow_private": True,
                "auth": {
                    "type": "oauth",
                    "issuer": base,
                    "client_id": "synthetic-client",
                    "scopes": ["read"],
                },
            },
        },
    )


def exercise(base, *, sql=False):
    with tempfile.TemporaryDirectory(prefix="nexus-oauth-example-") as directory:
        options = (
            {
                "state_options": {
                    "url": "sqlite:///" + str(Path(directory) / "state.db"),
                    "namespace": "example",
                }
            }
            if sql
            else {}
        )
        studio = StudioService(Path(directory) / "project", **options)
        try:
            connector = prepare(studio, base)
            assert studio.oauth.status(connector["id"])["status"] == "disconnected"
            assert not connector["data"]["enabled"]
            data = connector["data"]
            metadata = {"token_endpoint": base + "/token"}
            form = {
                "grant_type": "authorization_code",
                "code": "synthetic-code",
                "code_verifier": "synthetic-pkce-verifier",
                "redirect_uri": "http://localhost/callback",
            }
            response = client.exchange(data, metadata, form)
            tokens = StudioOAuth.tokens(response, data, metadata)
            refreshed = client.exchange(
                data,
                metadata,
                {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
            )
            assert (
                StudioOAuth.tokens(refreshed, data, metadata, previous=tokens)["refresh_token"]
                != tokens["refresh_token"]
            )
            for code in ("failure", "html", "redirect", "oversized"):
                try:
                    client.exchange(data, metadata, {**form, "code": code})
                except ValueError as error:
                    assert "DO_NOT_LEAK" not in str(error)
                else:
                    raise AssertionError("Expected bounded, sanitized fixture failure")
            return {
                "configuration": "private and disabled",
                "token_checks": 6,
                "storage": "sqlite" if sql else "files",
                "live_provider": "not called",
            }
        finally:
            studio.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-url", default="http://127.0.0.1:49647/oauth")
    parser.add_argument("--sql", action="store_true")
    args = parser.parse_args()
    print(json.dumps(exercise(args.fixture_url, sql=args.sql)))

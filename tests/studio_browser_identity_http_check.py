"""Run social identity clients against the local Postman HTTP simulation.

Usage: python tests/studio_browser_identity_http_check.py --base-url http://127.0.0.1:PORT/identity
Start postman/simulations/nexus-browser-identity.sim.yaml with --no-history first.
This explicit acceptance command is separate from tests requiring no Postman CLI.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests
import sqlalchemy as sa

from nexus.core.auth import browser_login as login
from nexus.core.auth import credential_store as store
from nexus.core.auth import oauth_onboarding_domain as auth


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    target = urlsplit(args.base_url)
    if target.scheme != "http" or target.hostname not in {"localhost", "127.0.0.1"}:
        parser.error("Only a local HTTP simulation is allowed.")
    providers = ("linkedin", "x", "meta")
    env = {"NEXUS_CREDENTIALS_MASTER_KEY": base64.b64encode(b"k" * 32).decode(),
           "NEXUS_PUBLIC_BASE_URL": "http://localhost"}
    for provider in providers:
        env[f"NEXUS_{provider.upper()}_CLIENT_ID"] = provider + "-app"
        env[f"NEXUS_{provider.upper()}_CLIENT_SECRET"] = "synthetic-secret"
    env.update(NEXUS_META_APP_ID="meta-app", NEXUS_META_APP_SECRET="synthetic-secret")
    original = requests.Session.request
    calls = []

    def request(session, method, url, **kwargs):
        parsed = urlsplit(url)
        if parsed.hostname not in {"www.linkedin.com", "api.linkedin.com", "api.twitter.com", "graph.facebook.com"}:
            raise AssertionError("Unexpected upstream operation")
        session.trust_env = False
        calls.append((method, parsed.hostname, parsed.path))
        return original(session, method, args.base_url.rstrip("/") + parsed.path, **kwargs)

    with tempfile.TemporaryDirectory(prefix="nexus-identity-http-") as directory, patch.dict(os.environ, env):
        engine = sa.create_engine(f"sqlite:///{Path(directory) / 'accounts.db'}")
        store._AuthBase.metadata.create_all(engine)
        with patch.object(store, "_get_engine", return_value=engine), patch.object(auth, "_now_utc", lambda: datetime.now(UTC).replace(tzinfo=None)), patch.object(requests.Session, "request", request):
            store.upsert_github_credentials(nexus_id="canonical", github_user_id=123, github_login="owner",
                                            github_token_enc="unchanged-git-token", org_verified=True)
            for provider in providers:
                # Represents a completed, provider-verified connection from Account setup.
                auth._resolve_social_login_identity(
                    type("Session", (), {"nexus_id": "canonical", "chat_platform": "web-connect"})(),
                    provider, "social-123",
                )
                for code in ("linked", "unknown", "missing", "denied"):
                    attempt = login.start_browser_login(provider=provider)
                    state = parse_qs(urlsplit(attempt["authorization_url"]).query)["state"][0]
                    try:
                        result = login.complete_browser_login(context=attempt["context"], provider=provider, state=state, code=code)
                    except (ValueError, RuntimeError, requests.HTTPError):
                        if code == "linked":
                            raise
                    else:
                        assert code == "linked", "Unlinked or malformed identity was accepted"
                        assert result["nexus_id"] == "canonical"
                    credential = store.get_user_credentials("canonical")
                    assert credential.github_token_enc == "unchanged-git-token"
                    assert not credential.linkedin_token_enc and not credential.x_bearer_token_enc and not credential.meta_page_token_enc
        engine.dispose()
    assert len(calls) == 21, calls
    print(json.dumps({"providers": list(providers), "cases_passed": 12, "real_http_requests": len(calls),
                      "live_accounts_used": False, "publishing_credentials_unchanged": True}))


if __name__ == "__main__":
    main()

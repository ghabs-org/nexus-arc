"""Browser-initiated identity login using the existing Nexus account store.

Git providers establish project identity. Social identities must be linked from
an existing Git-backed account; their tokens never establish project membership.
The encrypted, short-lived context binds callbacks to the initiating browser;
the database's existing one-time OAuth state and expiry checks still apply.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from nexus.core.auth import oauth_onboarding_domain as onboarding
from nexus.core.auth.credential_crypto import decrypt_secret, encrypt_secret

COOKIE_NAME = "nexus_browser_login"
TTL_SECONDS = 900
PROVIDERS = {"github": "GitHub", "gitlab": "GitLab", "linkedin": "LinkedIn", "x": "X", "meta": "Meta"}


def _normalized_origin(value: str, *, header: bool = False) -> tuple[str, str, int] | None:
    """Normalize HTTP origins without trusting proxy headers or URL credentials."""
    try:
        if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value):
            return None
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or (header and (parsed.path or parsed.query or parsed.fragment))):
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if port <= 0:
            return None
        return parsed.scheme, parsed.hostname.lower(), port
    except ValueError:
        return None


def browser_callback_origin(provider: str) -> tuple[str, str, int]:
    """Use the same trusted callback configuration as the selected OAuth flow."""
    if provider not in PROVIDERS:
        raise ValueError("Choose a configured Nexus identity provider.")
    origin = _normalized_origin(onboarding._oauth_callback_url(provider))
    if origin is None:
        raise ValueError("The public sign-in callback address is not configured correctly.")
    return origin


def browser_login_origin_allowed(*, provider: str, origin: str | None, fetch_site: str | None) -> bool:
    expected = browser_callback_origin(provider)
    if fetch_site == "cross-site":
        return False
    return origin is None or _normalized_origin(origin, header=True) == expected


def browser_cookie_requires_https(host: str) -> bool:
    """Retain Secure on later requests proxied to HTTP from a configured HTTPS site."""
    requested = _normalized_origin("https://" + host, header=True)
    if requested is None:
        return False
    for provider in PROVIDERS:
        try:
            if browser_callback_origin(provider) == requested:
                return True
        except ValueError:
            continue
    return False


def provider_client_id(provider: str) -> str:
    key = "NEXUS_META_APP_ID" if provider == "meta" else f"NEXUS_{provider.upper()}_CLIENT_ID"
    return str(os.getenv(key, "")).strip()


def configured_identity_providers() -> list[dict[str, str]]:
    """Return configured identity providers without exposing client credentials."""
    return [
        {"id": provider, "label": label}
        for provider, label in PROVIDERS.items()
        if provider_client_id(provider) and str(os.getenv(
            "NEXUS_META_APP_SECRET" if provider == "meta" else f"NEXUS_{provider.upper()}_CLIENT_SECRET", ""
        )).strip()
    ]


def safe_return_path(value: str | None, default: str = "/") -> str:
    """Accept only local paths, including after percent decoding."""
    candidate = str(value or "").strip()
    decoded = candidate
    for _ in range(3):
        decoded = unquote(decoded)
    if (not candidate.startswith("/") or decoded.startswith("//")
            or "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
            or urlsplit(decoded).netloc):
        return default
    return candidate


def start_browser_login(*, provider: str, next_path: str = "/") -> dict[str, str | int]:
    """Create a fresh anonymous auth attempt, never reuse a supplied session."""
    return _start_attempt(provider=provider, next_path=next_path, nexus_id=str(uuid4()), intent="login")


def start_browser_connection(*, session_id: str, provider: str, next_path: str) -> dict[str, str | int]:
    """Connect from a verified browser account using a separate protected attempt."""
    record = onboarding.get_auth_session(onboarding.resolve_login_session_id(session_id))
    if (not record or record.chat_platform not in {"web-login", "web-connect"}
            or record.status not in {"oauth_done", "completed"}
            or record.expires_at <= onboarding._now_utc()):
        raise ValueError("Sign in before connecting another account.")
    credentials = onboarding.get_user_credentials(record.nexus_id)
    if not credentials or not (credentials.github_user_id or credentials.gitlab_user_id):
        raise ValueError("A Git identity is required before connecting another account.")
    return _start_attempt(provider=provider, next_path=next_path, nexus_id=record.nexus_id, intent="connect")


def _start_attempt(*, provider: str, next_path: str, nexus_id: str, intent: str) -> dict[str, str | int]:
    if provider not in {item["id"] for item in configured_identity_providers()}:
        raise ValueError("This identity provider is not configured.")
    session_id = onboarding.create_login_session_for_user(
        nexus_id=nexus_id, discord_user_id="", discord_username=None,
        chat_platform=f"web-{intent}",
    )
    try:
        url, state = onboarding.start_oauth_flow(session_id, provider=provider)
        payload = json.dumps({
            "purpose": "nexus-browser-login-v1", "session_id": session_id,
            "provider": provider, "state": state, "intent": intent,
            "next": safe_return_path(next_path), "expires": time.time() + TTL_SECONDS,
        })
        context = base64.urlsafe_b64encode(encrypt_secret(payload).encode()).decode()
    except Exception:
        onboarding.update_auth_session(session_id=session_id, status="expired", oauth_state_hash="")
        raise
    return {"authorization_url": url, "context": context, "max_age": TTL_SECONDS}


def validate_browser_callback(*, context: str, provider: str, state: str) -> dict:
    """Reject foreign browsers, tampering, stale attempts and provider mixups."""
    try:
        if not context or len(context) > 4096 or not state:
            raise ValueError
        payload = json.loads(decrypt_secret(base64.urlsafe_b64decode(context).decode()))
        if (payload["purpose"] != "nexus-browser-login-v1"
                or payload["provider"] != provider or provider not in PROVIDERS
                or float(payload["expires"]) <= time.time()
                or not secrets.compare_digest(str(payload["state"]), state)):
            raise ValueError
        record = onboarding._assert_valid_callback_session(state, provider)
        if (payload["intent"] not in {"login", "connect"}
                or record.chat_platform != f"web-{payload['intent']}"
                or record.session_id != payload["session_id"]):
            raise ValueError
    except Exception as exc:
        raise ValueError("Sign-in expired or belongs to another browser. Please start again.") from exc
    payload["next"] = safe_return_path(payload.get("next"))
    return payload


def complete_browser_login(*, context: str, provider: str, state: str, code: str) -> dict:
    """Reuse verified Git identity resolution, encrypted credentials and ACL sync."""
    payload = validate_browser_callback(context=context, provider=provider, state=state)
    if not str(code or "").strip():
        raise ValueError("Missing authorization code.")
    complete = {
        "github": onboarding.complete_github_oauth, "gitlab": onboarding.complete_gitlab_oauth,
        "linkedin": onboarding.complete_linkedin_oauth, "x": onboarding.complete_x_oauth,
        "meta": onboarding.complete_meta_oauth,
    }[provider]
    result = complete(code=code, state=state)
    return {**result, "next": payload["next"], "intent": payload["intent"]}

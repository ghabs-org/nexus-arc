"""Subscription account discovery and authorization for browser consumers.

Subscription credentials belong to an existing Nexus identity. This module never
creates users, grants project access, probes credentials or launches a provider.
Actual login continues through the existing per-user CLI account service.
"""

from __future__ import annotations

import shutil
from urllib.parse import parse_qs, urlencode, urlsplit

from nexus.core.auth import oauth_onboarding_domain as onboarding
from nexus.core.auth.browser_login import safe_return_path

PROVIDERS = {"codex": "Codex", "claude": "Claude", "agy": "Antigravity"}


def normalize_subscription_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    value = {"claude-code": "claude", "antigravity": "agy"}.get(value, value)
    if value not in PROVIDERS:
        raise ValueError("Choose Codex, Claude or Antigravity.")
    return value


def subscription_accounts() -> list[dict]:
    """Discover server CLI readiness without starting commands or reading tokens."""
    accounts = []
    for provider, label in PROVIDERS.items():
        available = False
        reason = ""
        try:
            command = onboarding._provider_account_login_command(provider)
            available = bool(shutil.which(command[0]))
            if not available:
                reason = f"Install the {label} CLI on the Studio server to connect this subscription."
        except ValueError:
            reason = f"The server's {label} login command needs configuration."
        if not onboarding._cli_account_auth_mode_enabled():
            available = False
            reason = "Subscription connections require account or auto authentication mode on the server."
        accounts.append({"id": provider, "label": label, "billing": "subscription",
                         "available": available, "reason": reason})
    return accounts


def subscription_destination(provider: str, next_path: str = "/") -> str:
    provider = normalize_subscription_provider(provider)
    return "/auth/subscription?" + urlencode({"provider": provider, "next": safe_return_path(next_path)})


def is_subscription_destination(path: str) -> bool:
    """Identify the guarded connection page when AI setup is not yet ready."""
    candidate = safe_return_path(path, default="")
    return bool(candidate and urlsplit(candidate).path == "/auth/subscription")


def subscription_return_path(path: str) -> str:
    """Changing the selected provider keeps the original workspace destination."""
    candidate = safe_return_path(path)
    for _ in range(4):
        if not is_subscription_destination(candidate):
            return candidate
        candidate = safe_return_path(parse_qs(urlsplit(candidate).query).get("next", ["/"])[0])
    return "/"


def subscription_account_context(*, session_id: str, provider: str) -> dict:
    """Resolve only an existing, current Git-backed Nexus account for this page."""
    provider = normalize_subscription_provider(provider)
    if not str(session_id or "").strip():
        raise PermissionError("Sign in to Nexus before connecting a subscription.")
    record = onboarding.get_auth_session(onboarding.resolve_login_session_id(session_id))
    if (not record or record.status not in {"oauth_done", "completed"}
            or record.expires_at <= onboarding._now_utc()):
        raise PermissionError("Sign in to Nexus before connecting a subscription.")
    credentials = onboarding.get_user_credentials(record.nexus_id)
    if not credentials or not (credentials.github_user_id or credentials.gitlab_user_id):
        raise PermissionError("A Nexus identity is required before connecting a subscription.")
    account = next(item for item in subscription_accounts() if item["id"] == provider)
    return {**account, "session_id": record.session_id,
            "session_ref": onboarding.format_login_session_ref(record.session_id),
            "connected": bool(getattr(credentials, f"{provider}_account_enabled", False))}

"""Browser-to-native PKCE bridge backed by the existing Nexus auth database.

Device credentials identify an existing Nexus user; they never expose upstream
tokens or confer new project access. All secrets are random and stored hashed.
Only the browser confirmation context uses the existing encrypted secret store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from nexus.core.auth import credential_store as store
from nexus.core.auth import subscription_accounts
from nexus.core.auth.browser_login import browser_callback_origin
from nexus.core.auth.credential_crypto import decrypt_secret, encrypt_secret

ATTEMPT_TTL = 600
CODE_TTL = 60
DEVICE_TTL = 30 * 86400
COOKIE_NAME = "nexus_desktop_approval"
CALLBACK_PATH = "/nexus/desktop/callback"
_B64 = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")

if store._SA_AVAILABLE:
    sa = store.sa

    class DesktopAttempt(store._AuthBase):
        __tablename__ = "nexus_desktop_login_attempts"
        id = sa.Column(sa.String(64), primary_key=True)
        challenge = sa.Column(sa.String(43), nullable=False)
        state = sa.Column(sa.String(128), nullable=False)
        redirect_uri = sa.Column(sa.String(255), nullable=False)
        device_name = sa.Column(sa.String(80), nullable=False)
        expires = sa.Column(sa.Float, nullable=False, index=True)
        status = sa.Column(sa.String(16), nullable=False)
        nexus_id = sa.Column(sa.String(64))
        code_hash = sa.Column(sa.String(64))
        code_expires = sa.Column(sa.Float)

    class DesktopDevice(store._AuthBase):
        __tablename__ = "nexus_desktop_devices"
        id = sa.Column(sa.String(64), primary_key=True)
        token_hash = sa.Column(sa.String(64), nullable=False, unique=True)
        nexus_id = sa.Column(sa.String(64), nullable=False, index=True)
        device_name = sa.Column(sa.String(80), nullable=False)
        expires = sa.Column(sa.Float, nullable=False)
        revoked = sa.Column(sa.Boolean, nullable=False, default=False)


def _engine():
    store._require_sqlalchemy()
    engine = store._get_engine()
    # Additive and idempotent, including a process with an already-open auth engine.
    store._AuthBase.metadata.create_all(
        engine, tables=[DesktopAttempt.__table__, DesktopDevice.__table__]
    )
    return engine


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    if not isinstance(verifier, str) or not _B64.fullmatch(verifier):
        raise ValueError("Invalid desktop verifier.")
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def validate_redirect(uri: str) -> str:
    try:
        p = urlsplit(uri)
        if (
            p.scheme != "http"
            or p.hostname != "127.0.0.1"
            or p.username
            or p.password
            or not p.port
            or not 1024 <= p.port <= 65535
            or p.path != CALLBACK_PATH
            or p.query
            or p.fragment
            or p.netloc != f"127.0.0.1:{p.port}"
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Desktop callback must use the dedicated loopback listener.") from None
    return uri


def public_origin() -> str:
    scheme, host, port = browser_callback_origin("github")
    if scheme != "https":
        raise ValueError("Desktop account sign-in requires a public HTTPS callback.")
    return f"https://{host}" + (f":{port}" if port != 443 else "")


def authorization_path(attempt_id: str) -> str:
    return "/auth/desktop/authorize?" + urlencode({"attempt_id": attempt_id})


def is_authorization_destination(path: str) -> bool:
    return urlsplit(path).path == "/auth/desktop/authorize"


def start(*, redirect_uri: str, code_challenge: str, state: str, device_name: str) -> dict:
    redirect_uri = validate_redirect(redirect_uri)
    origin = public_origin()
    if (
        not isinstance(code_challenge, str)
        or len(code_challenge) != 43
        or not _B64.fullmatch(code_challenge)
        or not isinstance(state, str)
        or not _B64.fullmatch(state)
        or not isinstance(device_name, str)
        or not 1 <= len(device_name.strip()) <= 80
        or any(not c.isprintable() for c in device_name)
    ):
        raise ValueError("Invalid desktop login request.")
    now = time.time()
    identity = secrets.token_urlsafe(32)
    with store.Session(_engine()) as db, db.begin():
        db.query(DesktopAttempt).filter(DesktopAttempt.expires < now).delete()
        if db.query(DesktopAttempt).count() >= 1000:
            raise OverflowError("Too many pending desktop sign-ins. Try again later.")
        db.add(
            DesktopAttempt(
                id=identity,
                challenge=code_challenge,
                state=state,
                redirect_uri=redirect_uri,
                device_name=device_name.strip(),
                expires=now + ATTEMPT_TTL,
                status="pending",
            )
        )
    return {
        "attempt_id": identity,
        "authorization_url": origin + authorization_path(identity),
        "expires_in": ATTEMPT_TTL,
    }


def _pending(db, identity):
    row = db.get(DesktopAttempt, str(identity))
    if not row or row.status != "pending" or row.expires <= time.time():
        raise ValueError("Desktop sign-in expired or was already handled. Start again in Studio.")
    return row


def confirmation(*, attempt_id: str, session_id: str) -> dict:
    record, credentials = subscription_accounts._existing_account(session_id)
    with store.Session(_engine()) as db:
        row = _pending(db, attempt_id)
        csrf = secrets.token_urlsafe(32)
        context = encrypt_secret(
            json.dumps(
                {
                    "purpose": "desktop-approval-v1",
                    "attempt": row.id,
                    "session": record.session_id,
                    "csrf": csrf,
                    "expires": time.time() + ATTEMPT_TTL,
                }
            )
        )
        return {
            "device_name": row.device_name,
            "attempt_id": row.id,
            "csrf": csrf,
            "context": context,
            "account": credentials.github_login or credentials.gitlab_username or record.nexus_id,
        }


def approve(*, attempt_id: str, session_id: str, context: str, csrf: str, allow: bool) -> str:
    record, _ = subscription_accounts._existing_account(session_id)
    try:
        data = json.loads(decrypt_secret(context))
        if (
            data["purpose"] != "desktop-approval-v1"
            or data["attempt"] != attempt_id
            or data["session"] != record.session_id
            or data["expires"] <= time.time()
            or not csrf
            or not secrets.compare_digest(data["csrf"], csrf)
        ):
            raise ValueError
    except Exception:
        raise PermissionError(
            "Desktop approval does not match this browser. Start again."
        ) from None
    now = time.time()
    code = secrets.token_urlsafe(32)
    with store.Session(_engine()) as db, db.begin():
        row = _pending(db, attempt_id)
        destination, state = row.redirect_uri, row.state
        count = (
            db.query(DesktopAttempt)
            .filter_by(id=row.id, status="pending")
            .filter(DesktopAttempt.expires > now)
            .update(
                {
                    "status": "approved" if allow else "denied",
                    "nexus_id": record.nexus_id,
                    "code_hash": _hash(code) if allow else None,
                    "code_expires": now + CODE_TTL,
                },
                synchronize_session=False,
            )
        )
        if count != 1:
            raise ValueError("Desktop sign-in was already handled.")
    return (
        destination
        + "?"
        + urlencode({"state": state, **({"code": code} if allow else {"error": "access_denied"})})
    )


def exchange(*, attempt_id: str, code: str, code_verifier: str, redirect_uri: str) -> dict:
    challenge = pkce_challenge(code_verifier)
    validate_redirect(redirect_uri)
    now = time.time()
    if not isinstance(code, str) or not _B64.fullmatch(code):
        raise ValueError("Invalid desktop authorization code.")
    token, device_id = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    with store.Session(_engine()) as db, db.begin():
        row = db.get(DesktopAttempt, str(attempt_id))
        if (
            not row
            or row.status != "approved"
            or row.expires <= now
            or row.code_expires <= now
            or row.redirect_uri != redirect_uri
            or not secrets.compare_digest(row.challenge, challenge)
            or not secrets.compare_digest(row.code_hash or "", _hash(code))
        ):
            raise ValueError("Desktop authorization expired or does not match. Start again.")
        if (
            db.query(DesktopAttempt)
            .filter_by(id=row.id, status="approved")
            .update({"status": "consumed", "code_hash": None}, synchronize_session=False)
            != 1
        ):
            raise ValueError("Desktop authorization was already used.")
        db.add(
            DesktopDevice(
                id=device_id,
                token_hash=_hash(token),
                nexus_id=row.nexus_id,
                device_name=row.device_name,
                expires=now + DEVICE_TTL,
                revoked=False,
            )
        )
        nexus_id = row.nexus_id
    return {
        "token": token,
        "device_id": device_id,
        "nexus_id": nexus_id,
        "expires_at": now + DEVICE_TTL,
    }


def device_session(token: str, *, revoke: bool = False) -> dict:
    if not isinstance(token, str) or not _B64.fullmatch(token):
        raise PermissionError("Sign in to Nexus again.")
    with store.Session(_engine()) as db, db.begin():
        row = db.query(DesktopDevice).filter_by(token_hash=_hash(token), revoked=False).first()
        if not row or row.expires <= time.time():
            raise PermissionError("Nexus desktop session expired or was revoked.")
        credentials = store.get_user_credentials(row.nexus_id)
        if not credentials or not (credentials.github_user_id or credentials.gitlab_user_id):
            raise PermissionError("Nexus identity is no longer available.")
        result = {
            "device_id": row.id,
            "nexus_id": row.nexus_id,
            "device_name": row.device_name,
            "account": credentials.github_login or credentials.gitlab_username or row.nexus_id,
            "expires_at": row.expires,
            "projects": [
                grant.project_key for grant in store.get_user_project_access(row.nexus_id)
            ],
        }
        if revoke:
            row.revoked = True
        return result

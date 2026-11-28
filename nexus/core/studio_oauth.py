"""Explicit, user/session-bound MCP delegation on existing Studio persistence.

No tool call initiates consent or retries an external effect. Uncertain exchanges
and refreshes require new consent. All private payloads use Nexus credential crypto.
"""

import base64
import hashlib
import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from nexus.core import studio_oauth_client as client
from nexus.core.auth.delegated_credentials import scope_key, seal, unseal


def fingerprint(data):
    return scope_key({k: data.get(k) for k in ("url", "transport", "auth", "allow_private")})


def subject(actor):
    return actor["nexus_id"] if isinstance(actor, dict) else actor or "desktop"


def session(actor):
    return scope_key(actor.get("session_id", "")) if isinstance(actor, dict) else "desktop"


class StudioOAuth:
    def __init__(self, studio):
        self.studio = studio

    def connector(self, identity, actor):
        record = self.studio.catalog.get("connectors", identity)
        if self.studio.team:
            self.studio.team.assert_execution(subject(actor))
            principal = {
                "nexus_id": subject(actor),
                "role": self.studio.team.bindings.get(subject(actor)),
            }
            if not self.studio.team.visible(record, principal):
                raise PermissionError("Connector access denied")
        if record["data"].get("auth", {}).get("type") != "oauth":
            raise ValueError("Connector does not use delegated OAuth")
        return record

    def key(self, identity, actor):
        workspace = self.studio.team.project_key if self.studio.team else str(self.studio.root)
        return scope_key("mcp", workspace, subject(actor), identity)

    def read(self, collection, key):
        path = self.studio._path(collection, key)
        return self.studio._read(path) if path.exists() else None

    def write(self, collection, key, record):
        self.studio._write(self.studio._path(collection, key), record)

    def status(self, identity, actor=None):
        record = self.connector(identity, actor)
        key = self.key(identity, actor)
        with self.studio.lock:
            grant = self.read("delegated-credentials", key)
            status = "disconnected"
            expiry = None
            if grant:
                status = grant["status"]
                if grant["binding"] != fingerprint(record["data"]):
                    status = "configuration_changed"
                elif status == "connected":
                    try:
                        value = unseal(key, grant["envelope"])
                        expiry = value.get("expires_at")
                        if (
                            expiry is not None
                            and expiry <= time.time() + 30
                            and not value.get("refresh_token")
                        ):
                            status = "expired"
                    except PermissionError:
                        status = "reconnect_required"
            return {
                "status": status,
                "connected": status == "connected",
                "expires_at": expiry,
                "scope": record["data"]["auth"]["scopes"],
            }

    def secret(self, data, actor):
        name = data["auth"].get("client_secret_env")
        if not name:
            return None
        values = os.environ
        if self.studio.team:
            values, error = self.studio.team.environment_resolver(subject(actor))
            if error:
                raise PermissionError("OAuth client credentials unavailable")
        value = values.get(name)
        if not value:
            raise PermissionError("Configure OAuth client secret environment reference")
        return value

    def start(self, identity, actor, origin):
        data = self.connector(identity, actor)["data"]
        client.endpoint(origin, True)
        if client.origin(origin) != origin:
            raise ValueError("Callback origin cannot contain a path")
        key = self.key(identity, actor)
        # Fail before discovery if the existing credential key is not configured.
        try:
            seal(key, {})
        except RuntimeError:
            raise ValueError(
                "Install Nexus credential encryption support before connecting accounts"
            ) from None
        self.secret(data, actor)
        metadata = client.discover(data)
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        flow = hashlib.sha256(state.encode()).hexdigest()
        redirect = origin + "/api/studio/oauth/callback"
        encrypted = seal(flow, {"verifier": verifier, "metadata": metadata, "redirect": redirect})
        with self.studio.lock:
            current = self.connector(identity, actor)["data"]
            if fingerprint(current) != fingerprint(data):
                raise ValueError("Connector changed during discovery; start again")
            self.write(
                "oauth-requests",
                flow,
                {
                    "version": 1,
                    "id": flow,
                    "connector_id": identity,
                    "scope_key": key,
                    "session": session(actor),
                    "cookie": scope_key(nonce),
                    "expires_at": time.time() + 600,
                    "binding": fingerprint(data),
                    "status": "pending",
                    "envelope": encrypted,
                },
            )
        params = {
            "response_type": "code",
            "client_id": data["auth"]["client_id"],
            "redirect_uri": redirect,
            "state": state,
            "resource": data["auth"]["resource"],
            "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode(),
        }
        if data["auth"]["scopes"]:
            params["scope"] = " ".join(data["auth"]["scopes"])
        cookie = SimpleCookie()
        cookie["nexus_mcp_" + flow] = nonce
        morsel = cookie["nexus_mcp_" + flow]
        morsel["httponly"], morsel["samesite"], morsel["path"], morsel["max-age"] = (
            True,
            "Lax",
            "/api/studio/oauth/callback",
            600,
        )
        morsel["secure"] = urlsplit(origin).scheme == "https"
        return {
            "authorization_url": client.authorization_url(metadata, params),
            "flow_id": flow,
        }, morsel.OutputString()

    @staticmethod
    def tokens(response, data, metadata, *, previous=None):
        def token(name, required=False):
            value = response.get(name)
            if value is None and not required:
                return None
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 16384
                or any(ord(c) < 33 or ord(c) > 126 for c in value)
            ):
                raise ValueError("OAuth service returned an invalid token")
            return value

        if str(response.get("token_type", "")).lower() != "bearer":
            raise ValueError("OAuth service must return Bearer tokens")
        access, refresh = token("access_token", True), token("refresh_token")
        expires = response.get("expires_in")
        if expires is not None and (
            type(expires) not in {int, float} or not 0 < expires <= 31536000
        ):
            raise ValueError("Invalid OAuth token lifetime")
        scopes = response.get("scope", " ".join(data["auth"]["scopes"]))
        if not isinstance(scopes, str) or set(scopes.split()) != set(data["auth"]["scopes"]):
            raise ValueError("OAuth token scopes differ from reviewed scopes")
        if (
            previous
            and not data["auth"].get("client_secret_env")
            and (not refresh or refresh == previous.get("refresh_token"))
        ):
            raise ValueError("Public OAuth clients require refresh token rotation; reconnect")
        return {
            "access_token": access,
            "refresh_token": refresh or (previous or {}).get("refresh_token"),
            "expires_at": time.time() + expires if expires is not None else None,
            "metadata": metadata,
        }

    def callback(self, query, cookies, actor=None):
        def value(name, required=False):
            values = query.get(name, [])
            if (
                len(values) > 1
                or (required and not values)
                or any(not isinstance(v, str) or not 1 <= len(v) <= 4096 for v in values)
            ):
                raise ValueError("Invalid OAuth callback parameters")
            return values[0] if values else None

        state, code, issuer, error = (
            value("state", True),
            value("code"),
            value("iss"),
            value("error"),
        )
        flow = hashlib.sha256(state.encode()).hexdigest()
        cookie = SimpleCookie()
        try:
            cookie.load(cookies or "")
        except Exception:
            raise PermissionError("OAuth browser binding required") from None
        nonce = cookie.get("nexus_mcp_" + flow)
        with self.studio.lock:
            pending = self.read("oauth-requests", flow)
            if (
                not pending
                or pending["status"] != "pending"
                or pending["expires_at"] <= time.time()
            ):
                raise ValueError("OAuth request expired or already used; start again")
            identity = pending["connector_id"]
            if pending["scope_key"] != self.key(identity, actor) or pending["session"] != session(
                actor
            ):
                raise PermissionError("OAuth Nexus session mismatch")
            if not nonce or not secrets.compare_digest(pending["cookie"], scope_key(nonce.value)):
                raise PermissionError("OAuth browser binding required")
            data = self.connector(identity, actor)["data"]
            saved = unseal(flow, pending["envelope"])
            if pending["binding"] != fingerprint(data):
                raise ValueError("Connector changed; start consent again")
            if (saved["metadata"]["issuer_parameter"] and issuer is None) or (
                issuer is not None and issuer != data["auth"]["issuer"]
            ):
                raise ValueError("OAuth callback issuer mismatch")
            pending.update(status="exchanging", envelope=None)
            self.write("oauth-requests", flow, pending)
        try:
            if error or not code:
                raise ValueError("OAuth authorization was not granted; start again")
            result = client.exchange(
                data,
                saved["metadata"],
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": saved["verifier"],
                    "redirect_uri": saved["redirect"],
                },
                self.secret(data, actor),
            )
            tokens = self.tokens(result, data, saved["metadata"])
            with self.studio.lock:
                current = self.read("oauth-requests", flow)
                if (
                    current["status"] != "exchanging"
                    or fingerprint(self.connector(identity, actor)["data"]) != pending["binding"]
                ):
                    raise ValueError("OAuth request invalidated; reconnect")
                self.write(
                    "delegated-credentials",
                    pending["scope_key"],
                    {
                        "version": 1,
                        "id": pending["scope_key"],
                        "binding": pending["binding"],
                        "status": "connected",
                        "envelope": seal(pending["scope_key"], tokens),
                    },
                )
                current["status"] = "completed"
                self.write("oauth-requests", flow, current)
            return {"status": "connected", "message": "Account connected. You can close this tab."}
        except Exception:
            with self.studio.lock:
                current = self.read("oauth-requests", flow)
                if current["status"] == "exchanging":
                    current["status"] = "failed"
                    self.write("oauth-requests", flow, current)
            raise

    def headers(self, identity, actor=None, *, expected=None):
        deadline = time.monotonic() + 12
        while True:
            with self.studio.lock:
                data = self.connector(identity, actor)["data"]
                if expected is not None and data != expected:
                    raise PermissionError("Connector account configuration changed")
                key = self.key(identity, actor)
                grant = self.read("delegated-credentials", key)
                if not grant or grant["binding"] != fingerprint(data):
                    raise PermissionError("Connect your MCP account before running tools")
                waiting = grant["status"] == "refreshing"
                if not waiting:
                    if grant["status"] != "connected":
                        raise PermissionError("Connect your MCP account before running tools")
                    tokens = unseal(key, grant["envelope"])
                    if tokens["expires_at"] is None or tokens["expires_at"] > time.time() + 30:
                        return {"Authorization": "Bearer " + tokens["access_token"]}
                    if not tokens.get("refresh_token"):
                        raise PermissionError("MCP account expired; reconnect")
                    receipt = secrets.token_hex(16)
                    grant.update(status="refreshing", receipt=receipt)
                    self.write("delegated-credentials", key, grant)
            if not waiting:
                break
            if time.monotonic() >= deadline:
                raise PermissionError("MCP account refresh unavailable; reconnect if interrupted")
            time.sleep(0.05)
        # Commit claim before I/O, including SQL mode. A crash/uncertain rotation
        # leaves refreshing and never automatically reuses the old refresh token.
        failure = None
        try:
            result = client.exchange(
                data,
                tokens["metadata"],
                {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                },
                self.secret(data, actor),
            )
            tokens = self.tokens(result, data, tokens["metadata"], previous=tokens)
            envelope = seal(key, tokens)
        except Exception:
            failure = "MCP account refresh failed; reconnect without repeating the tool"
            envelope = None
        with self.studio.lock:
            current = self.read("delegated-credentials", key)
            current_data = self.connector(identity, actor)["data"]
            if (
                not current
                or current["status"] != "refreshing"
                or current.get("receipt") != receipt
                or current["binding"] != fingerprint(current_data)
            ):
                raise PermissionError("MCP account changed during refresh; reconnect")
            current.update(
                status="reconnect_required" if failure else "connected", envelope=envelope
            )
            self.write("delegated-credentials", key, current)
        if failure:
            raise PermissionError(failure)
        if expected is not None and current_data != expected:
            raise PermissionError(
                "Connector permissions changed during refresh; submit a new request"
            )
        return {"Authorization": "Bearer " + tokens["access_token"]}

    def disconnect(self, identity, actor=None):
        self.connector(identity, actor)
        key = self.key(identity, actor)
        with self.studio.lock:
            grant = self.read("delegated-credentials", key)
            if grant:
                grant.update(status="disconnected", envelope=None)
                self.write("delegated-credentials", key, grant)
            for pending in self.studio.records("oauth-requests", limit=None):
                if pending["scope_key"] == key and pending["status"] in {"pending", "exchanging"}:
                    pending.update(status="revoked", envelope=None)
                    self.write("oauth-requests", pending["id"], pending)
        return {"status": "disconnected"}

"""Shared remote destination policy and identity-scoped credential headers."""

import base64
import ipaddress
import os
import re
import socket
from urllib.parse import urlparse


def validate_remote(data):
    from nexus.core.studio_connections import env_name

    url = data.get("url")
    if not isinstance(url, str) or len(url) > 2000:
        raise ValueError("Remote service URL is required")
    parsed = urlparse(url)
    private = data.get("allow_private", False)
    if type(private) is not bool:
        raise ValueError("Allow private must be true or false")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTP(S) URL without credentials, query or fragment")
    if parsed.scheme != "https" and not private:
        raise ValueError("Remote services require HTTPS; private HTTP needs explicit allowance")
    auth = data.get("auth", {})
    if isinstance(auth, dict) and auth.get("type") == "oauth":
        from nexus.core.studio_oauth_client import configuration

        return {"url": url.rstrip("/"), "allow_private": private, "auth": configuration(auth, data)}
    if not isinstance(auth, dict) or set(auth) - {
        "type",
        "token_env",
        "username_env",
        "password_env",
        "header",
        "parameter",
    }:
        raise ValueError("Authentication uses credential environment references only")
    kind = auth.get("type", "bearer" if data.get("token_env") else "none")
    if kind not in {"none", "bearer", "api-key", "basic", "query", "url-token"}:
        raise ValueError("Unsupported remote authentication type")
    normalized = {"type": kind}
    for key in ("token_env", "username_env", "password_env"):
        if auth.get(key):
            normalized[key] = env_name(auth[key])
    if kind in {"bearer", "api-key", "query", "url-token"} and not (
        normalized.get("token_env") or data.get("token_env")
    ):
        raise ValueError("Select a credential environment reference")
    if kind == "basic" and not all(k in normalized for k in ("username_env", "password_env")):
        raise ValueError("Basic authentication requires username and password references")
    header = auth.get("header", "X-API-Key")
    if (
        not isinstance(header, str)
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,63}", header)
        or header.lower() in {"host", "cookie", "content-length", "connection"}
    ):
        raise ValueError("Choose an allowed API-key header name")
    normalized["header"] = header
    if kind == "query":
        parameter = auth.get("parameter", "token")
        if not isinstance(parameter, str) or not re.fullmatch(r"[A-Za-z][\w-]{0,63}", parameter):
            raise ValueError("Use an API-key query parameter name")
        normalized["parameter"] = parameter
    if kind == "url-token" and url.count("{token}") != 1:
        raise ValueError("Secret URL authentication requires one {token} path placeholder")
    return {"url": url.rstrip("/"), "allow_private": private, "auth": normalized}


def validate_destination(url, allow_private=False):
    parsed = urlparse(url)
    if not allow_private:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any(
            not ipaddress.ip_address(item[4][0]).is_global for item in addresses
        ):
            raise ValueError("Private destination requires explicit owner allowance")


def remote_headers(studio, data, identity, *, connector_id=None):
    if data.get("auth", {}).get("type") == "oauth":
        if not connector_id:
            raise PermissionError("Delegated OAuth requires a saved MCP connector")
        return studio.oauth.headers(connector_id, identity, expected=data)
    values = os.environ
    if studio.team:
        studio.team.assert_execution(identity)
        values, error = studio.team.environment_resolver(identity)
        if error:
            raise PermissionError("Remote service account is not configured")
    auth = data.get("auth", {})
    kind = auth.get("type", "bearer" if data.get("token_env") else "none")
    if kind in {"none", "query", "url-token"}:
        return {}

    def secret(name):
        result = values.get(name or "")
        if not result or "\n" in result or "\r" in result:
            raise PermissionError("Configure remote service account credentials")
        return result

    if kind == "basic":
        value = secret(auth.get("username_env")) + ":" + secret(auth.get("password_env"))
        return {"Authorization": "Basic " + base64.b64encode(value.encode()).decode()}
    token = secret(auth.get("token_env") or data.get("token_env"))
    return (
        {auth.get("header", "X-API-Key"): token}
        if kind == "api-key"
        else {"Authorization": "Bearer " + token}
    )


def remote_request_url(studio, data, identity):
    from urllib.parse import quote, urlencode

    kind = data.get("auth", {}).get("type")
    if kind not in {"query", "url-token"}:
        return data["url"]
    values = os.environ
    if studio.team:
        studio.team.assert_execution(identity)
        values, error = studio.team.environment_resolver(identity)
        if error:
            raise PermissionError("Remote account credentials unavailable")
    token = values.get(data["auth"].get("token_env") or data.get("token_env", ""))
    if not token or "\r" in token or "\n" in token:
        raise PermissionError("Configure remote account credentials")
    if kind == "query":
        return data["url"] + "?" + urlencode({data["auth"]["parameter"]: token})
    return data["url"].replace("{token}", quote(token, safe=""))

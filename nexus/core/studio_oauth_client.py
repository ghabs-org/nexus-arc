"""Bounded OAuth/OIDC discovery and token exchange for explicit MCP consent."""

import base64
import ipaddress
import json
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

from nexus.core.studio_remote import validate_destination


def endpoint(value, private=False):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("OAuth endpoint URL is required")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("Invalid OAuth endpoint URL") from None
    try:
        local = parts.hostname == "localhost" or ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError:
        local = False
    if (
        not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.scheme not in {"https", "http"}
        or (parts.scheme == "http" and not (private and local))
        or (port is not None and not 0 < port < 65536)
    ):
        raise ValueError("OAuth requires HTTPS; explicit private allowance permits loopback HTTP")
    return value


def origin(value):
    parts = urlsplit(value)
    return parts.scheme + "://" + parts.netloc


def request(url, *, private=False, form=None, headers=None, missing=False):
    import httpx

    endpoint(url, private)
    validate_destination(url, private)
    deadline = time.monotonic() + 10
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "POST" if form is not None else "GET",
                url,
                headers={"Accept": "application/json", **(headers or {})},
                data=form,
            ) as response:
                if missing and response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise ValueError(
                        "OAuth service rejected the request; reconnect or check configuration"
                    )
                raw = bytearray()
                for part in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise ValueError("OAuth service response timed out")
                    raw.extend(part)
                    if len(raw) > 65536:
                        raise ValueError("OAuth service response exceeds 64 KiB")
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("Invalid OAuth service response")
                return value
    except (httpx.HTTPError, UnicodeError, json.JSONDecodeError):
        raise ValueError("OAuth service unavailable or returned invalid JSON") from None


def discover(data):
    auth, private = data["auth"], data["allow_private"]
    resource, issuer = auth["resource"], auth["issuer"]
    resource_parts, issuer_parts = urlsplit(data["url"]), urlsplit(issuer)
    protected = request(
        origin(data["url"]) + "/.well-known/oauth-protected-resource" + resource_parts.path,
        private=private,
        missing=True,
    )
    if protected is None and resource_parts.path:
        protected = request(
            origin(data["url"]) + "/.well-known/oauth-protected-resource", private=private
        )
    if (
        not protected
        or protected.get("resource") != resource
        or issuer not in protected.get("authorization_servers", [])
    ):
        raise ValueError("MCP resource metadata does not authorize the selected issuer/resource")
    metadata = request(
        origin(issuer) + "/.well-known/oauth-authorization-server" + issuer_parts.path,
        private=private,
        missing=True,
    )
    if metadata is None:
        metadata = request(
            issuer.rstrip("/") + "/.well-known/openid-configuration", private=private
        )
    if metadata.get("issuer") != issuer:
        raise ValueError("OAuth metadata issuer mismatch")
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise ValueError("OAuth server must support S256 PKCE")
    if "code" not in metadata.get("response_types_supported", []):
        raise ValueError("OAuth server must support authorization codes")
    method = "client_secret_basic" if auth.get("client_secret_env") else "none"
    if method not in metadata.get("token_endpoint_auth_methods_supported", ["client_secret_basic"]):
        raise ValueError("OAuth client authentication method is unsupported")
    allowed = {origin(issuer), *auth.get("endpoint_origins", [])}
    for field in ("authorization_endpoint", "token_endpoint"):
        endpoint(metadata.get(field), private)
        if origin(metadata[field]) not in allowed:
            raise ValueError("OAuth endpoint origin requires explicit configuration")
    return {
        "authorization_endpoint": metadata["authorization_endpoint"],
        "token_endpoint": metadata["token_endpoint"],
        "issuer_parameter": metadata.get("authorization_response_iss_parameter_supported") is True,
    }


def exchange(data, metadata, form, secret=None):
    auth = data["auth"]
    headers = {}
    form = {**form, "resource": auth["resource"]}
    if auth.get("client_secret_env"):
        if not secret or "\r" in secret or "\n" in secret:
            raise PermissionError("Configure OAuth client secret environment reference")
        # RFC 6749 2.3.1: form-encode both components before Basic encoding.
        from urllib.parse import quote_plus

        pair = quote_plus(auth["client_id"]) + ":" + quote_plus(secret)
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode()
    else:
        form["client_id"] = auth["client_id"]
    return request(
        metadata["token_endpoint"], private=data["allow_private"], form=form, headers=headers
    )


def authorization_url(metadata, params):
    parts = urlsplit(metadata["authorization_endpoint"])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


def configuration(auth, data):
    """Public configuration only; secrets are environment references."""
    from nexus.core.studio_connections import env_name

    if data.get("type") != "mcp" or data.get("transport", "stdio") == "stdio":
        raise ValueError("Delegated OAuth supports remote MCP connectors only")
    if set(auth) - {
        "type",
        "issuer",
        "resource",
        "client_id",
        "scopes",
        "client_secret_env",
        "endpoint_origins",
    }:
        raise ValueError("Unsupported OAuth configuration field")
    private = data.get("allow_private", False)
    issuer = endpoint(auth.get("issuer"), private)
    resource = endpoint(auth.get("resource") or data["url"].rstrip("/"), private)
    if origin(resource) != origin(data["url"]):
        raise ValueError("OAuth resource must belong to the selected MCP server origin")
    client_id = auth.get("client_id")
    if (
        not isinstance(client_id, str)
        or not 1 <= len(client_id) <= 512
        or any(ord(c) < 32 for c in client_id)
    ):
        raise ValueError("Pre-registered OAuth client ID is required")
    scopes = auth.get("scopes", [])
    if (
        not isinstance(scopes, list)
        or len(scopes) > 32
        or len(set(map(str, scopes))) != len(scopes)
        or any(
            not isinstance(s, str)
            or not 1 <= len(s) <= 128
            or any(ord(c) < 33 or ord(c) > 126 or c in '\\"' for c in s)
            for s in scopes
        )
    ):
        raise ValueError("Use a list of distinct OAuth scope tokens")
    origins = auth.get("endpoint_origins", [])
    if not isinstance(origins, list) or len(origins) > 8:
        raise ValueError("Use at most eight explicit OAuth endpoint origins")
    for item in origins:
        endpoint(item, private)
        if origin(item) != item:
            raise ValueError("OAuth endpoint origins cannot include a path")
    result = {
        "type": "oauth",
        "issuer": issuer,
        "resource": resource,
        "client_id": client_id,
        "scopes": scopes,
        "endpoint_origins": origins,
    }
    if auth.get("client_secret_env"):
        result["client_secret_env"] = env_name(auth["client_secret_env"])
    return result

"""Read-only HTTPS deployment verification with redacted, portable results."""

from __future__ import annotations

import json
import ssl
import struct
import urllib.error
import urllib.request
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse


def verify(origin, *, session=None, ca_file=None):
    """Verify a hosted Studio without sending mutations or following redirects.

    An existing Nexus session is sent only to fixed same-origin private routes.
    Session values, user identifiers, response bodies and login query strings are
    never included in the returned report. This does not complete OAuth or test
    physical devices, remote accounts, brokers or backup restoration.
    """
    url = urlparse(origin)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("Use an HTTPS origin without credentials, path or query")
    origin = origin.rstrip("/")
    context = ssl.create_default_context(cafile=ca_file)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=context))
    results = []

    def get(path, authenticated=False):
        target = urlparse(urljoin(origin + "/", path))
        if (
            (target.scheme, target.netloc) != (url.scheme, url.netloc)
            or target.username
            or target.password
        ):
            raise ValueError("Verification requests must stay on the configured origin")
        headers = {"Cookie": "nexus_web_session=" + session} if authenticated and session else {}
        request = urllib.request.Request(target.geturl(), headers=headers)
        try:
            response = opener.open(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError("Verification response exceeds two MiB")
            return response.code, response.headers, raw

    def check(name, passed, detail):
        results.append({"check": name, "passed": bool(passed), "detail": detail})

    def media(headers):
        return headers.get("Content-Type", "").split(";")[0].strip().lower()

    def document(body):
        try:
            value = json.loads(body)
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    status, headers, body = get("/")
    check(
        "HTTPS shell",
        status == 200 and media(headers) == "text/html" and b"Nexus Studio" in body,
        "Certificate and hostname verified; Studio HTML served",
    )
    check(
        "Browser isolation",
        "frame-ancestors 'none'" in headers.get("Content-Security-Policy", "")
        and headers.get("X-Content-Type-Options", "").lower() == "nosniff",
        "Browser response policy checked",
    )
    status, headers, body = get("/api/auth/status")
    auth = document(body)
    check(
        "Hosted authentication",
        status == 200
        and media(headers) == "application/json"
        and auth.get("mode") == "team"
        and auth.get("authenticated") is False,
        "Hosted mode must require an existing Nexus identity",
    )
    login = auth.get("login_url")
    parsed = urlparse(urljoin(origin + "/", login)) if isinstance(login, str) else None
    safe = bool(
        parsed
        and (parsed.scheme, parsed.netloc) == (url.scheme, url.netloc)
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and parsed.path not in {"", "/"}
    )
    if safe:
        status, headers, body = get(parsed.geturl())
        redirect = urlparse(urljoin(origin + "/", headers.get("Location", "")))
        reachable = (status == 200 and media(headers) == "text/html" and bool(body)) or (
            status in {301, 302, 303, 307, 308}
            and bool(headers.get("Location"))
            and redirect.scheme == "https"
            and not redirect.username
            and not redirect.password
        )
        check(
            "Login route",
            reachable,
            "Login must serve HTML or an HTTPS sign-in redirect; interactive OAuth remains a separate check",
        )
    else:
        check("Login route", False, "Login URL must use the same HTTPS origin")
    for path in (
        "/api/bootstrap",
        "/api/runs",
        "/api/sessions",
        "/api/catalog/artifacts",
        "/api/workbench/inbox",
        "/api/export",
    ):
        status, _, _ = get(path)
        check(
            "Anonymous access: " + path,
            status == 401,
            "Private route must reject anonymous requests without redirecting",
        )
    status, headers, body = get("/manifest.webmanifest")
    manifest = document(body)
    valid = (
        status == 200
        and media(headers) == "application/manifest+json"
        and manifest.get("display") == "standalone"
        and manifest.get("start_url") == "/"
        and manifest.get("scope") == "/"
    )
    icons = manifest.get("icons", [])
    valid = (
        valid
        and isinstance(icons, list)
        and all(
            any(
                isinstance(icon, dict)
                and icon.get("src") == f"/icon-{size}.png"
                and icon.get("sizes") == f"{size}x{size}"
                for icon in icons
            )
            for size in (192, 512)
        )
    )
    check("Install manifest", valid, "Standalone app metadata and both icon declarations checked")
    for size in (192, 512):
        status, headers, body = get(f"/icon-{size}.png")
        png = (
            len(body) >= 33
            and body.startswith(b"\x89PNG\r\n\x1a\n")
            and body[12:16] == b"IHDR"
            and struct.unpack("!II", body[16:24]) == (size, size)
        )
        check(
            f"Icon {size}",
            status == 200 and media(headers) == "image/png" and png,
            "PNG signature and expected dimensions checked",
        )
    for path, signature in (
        ("/service-worker.js", b"SHELL_CACHE"),
        ("/experience.js", b"registerStudioApp"),
    ):
        status, headers, body = get(path)
        check(
            path,
            status == 200
            and media(headers) in {"text/javascript", "application/javascript"}
            and signature in body
            and b"<html" not in body.lower(),
            "Expected Studio JavaScript served, rather than an HTML fallback",
        )
    if session:
        for path in ("/api/bootstrap", "/api/workbench/inbox"):
            status, headers, body = get(path, True)
            data = document(body)
            valid = (
                data.get("mode") == "team" and bool(data.get("user"))
                if path == "/api/bootstrap"
                else isinstance(data.get("items"), list)
            )
            check(
                "Nexus identity" if path == "/api/bootstrap" else "Approval inbox",
                status == 200 and media(headers) == "application/json" and valid,
                "Existing Nexus session accepted for this private route",
            )
            check(
                "Private response caching: " + path,
                "no-store" in headers.get("Cache-Control", "").lower(),
                "Private API data must not be cached",
            )
    else:
        check("Nexus identity", False, "Set NEXUS_STUDIO_CHECK_SESSION to test an existing account")
    return {
        "format": "nexus-studio-verification/v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "passed": all(item["passed"] for item in results),
        "checks": results,
    }

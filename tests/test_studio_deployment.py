"""Deployment verification rejects misleading assets and preserves session scope."""

import io
import json
from pathlib import Path

import pytest

from nexus.core.studio_deployment import verify

ORIGIN = "https://studio.example.test"
SESSION = "a" * 32


class Response(io.BytesIO):
    def __init__(self, status, headers, body):
        super().__init__(body)
        self.code, self.headers = status, headers


def fixture_responses():
    assets = Path(__file__).parents[1] / "nexus/studio_static"
    result = {
        "/": (
            200,
            {
                "Content-Type": "text/html",
                "Content-Security-Policy": "frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
            b"<html>Nexus Studio</html>",
        ),
        "/api/auth/status": (
            200,
            {"Content-Type": "application/json"},
            json.dumps(
                {"mode": "team", "authenticated": False, "login_url": ORIGIN + "/login?next=/"}
            ).encode(),
        ),
        "/login?next=/": (
            200,
            {"Content-Type": "text/html"},
            b"<html>Sign in through Nexus</html>",
        ),
    }
    for name, content_type in [
        ("manifest.webmanifest", "application/manifest+json"),
        ("icon-192.png", "image/png"),
        ("icon-512.png", "image/png"),
        ("service-worker.js", "text/javascript"),
        ("experience.js", "text/javascript"),
    ]:
        result["/" + name] = (200, {"Content-Type": content_type}, (assets / name).read_bytes())
    return result


def network(monkeypatch, responses, *, exposed=None, cache="no-store"):
    calls = []

    class Opener:
        def open(self, request, timeout):
            assert request.full_url.startswith(ORIGIN + "/")
            path = request.full_url[len(ORIGIN) :]
            cookie = request.get_header("Cookie")
            calls.append((path, bool(cookie)))
            if path in responses:
                return Response(*responses[path])
            if cookie:
                assert cookie == "nexus_web_session=" + SESSION
                data = (
                    {
                        "mode": "team",
                        "user": {"nexus_id": "private-identity"},
                        "token": "private-csrf",
                    }
                    if path == "/api/bootstrap"
                    else {"items": []}
                )
                return Response(
                    200,
                    {"Content-Type": "application/json", "Cache-Control": cache},
                    json.dumps(data).encode(),
                )
            return Response(
                200 if path == exposed else 401, {"Content-Type": "application/json"}, b"{}"
            )

    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    return calls


def test_verifier_is_read_only_and_never_sends_session_to_public_or_login_routes(monkeypatch):
    calls = network(monkeypatch, fixture_responses())
    report = verify(ORIGIN, session=SESSION)
    assert report["passed"]
    assert {path for path, cookie in calls if cookie} == {"/api/bootstrap", "/api/workbench/inbox"}
    assert SESSION not in json.dumps(report) and "private-identity" not in json.dumps(report)
    assert "private-csrf" not in json.dumps(report)


@pytest.mark.parametrize(
    "broken", ["login", "manifest", "icon", "script", "anonymous", "cache", "external-login"]
)
def test_verifier_rejects_missing_auth_route_html_fallbacks_and_private_cache(monkeypatch, broken):
    responses = fixture_responses()
    if broken == "login":
        responses["/login?next=/"] = (404, {"Content-Type": "text/html"}, b"Not found")
    elif broken == "manifest":
        responses["/manifest.webmanifest"] = (
            200,
            {"Content-Type": "application/manifest+json"},
            b"[]",
        )
    elif broken == "icon":
        responses["/icon-192.png"] = (200, {"Content-Type": "image/png"}, b"<html>Fallback</html>")
    elif broken == "script":
        responses["/experience.js"] = (200, {"Content-Type": "text/html"}, b"<html>Fallback</html>")
    elif broken == "external-login":
        responses["/api/auth/status"] = (
            200,
            {"Content-Type": "application/json"},
            json.dumps(
                {
                    "mode": "team",
                    "authenticated": False,
                    "login_url": "https://outside.invalid/login?state=private",
                }
            ).encode(),
        )
    calls = network(
        monkeypatch,
        responses,
        exposed="/api/export" if broken == "anonymous" else None,
        cache="public, max-age=3600" if broken == "cache" else "no-store",
    )
    report = verify(ORIGIN, session=SESSION)
    assert not report["passed"]
    assert all(path.startswith("/") for path, _ in calls)
    assert "state=private" not in json.dumps(report)


def test_https_login_redirect_is_reported_without_following_oauth(monkeypatch):
    responses = fixture_responses()
    responses["/login?next=/"] = (
        302,
        {"Location": "https://github.com/login/oauth/authorize?state=private"},
        b"",
    )
    calls = network(monkeypatch, responses)
    assert verify(ORIGIN, session=SESSION)["passed"]
    assert not any("github.com" in path for path, _ in calls)


def test_missing_session_does_not_certify_private_access(monkeypatch):
    network(monkeypatch, fixture_responses())
    report = verify(ORIGIN)
    assert not report["passed"]
    assert (
        next(item for item in report["checks"] if item["check"] == "Nexus identity")["passed"]
        is False
    )

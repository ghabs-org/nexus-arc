"""Authenticated client for the existing Studio HTTP boundary; never replays POSTs."""

import json
import os
from urllib.parse import urlparse

import requests


class StudioHTTPClient:
    def __init__(self, url, session_env=None):
        self.url = url.rstrip("/")
        parsed = urlparse(self.url)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Studio URL must be an origin without embedded credentials")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("Use HTTPS for remote Studio or HTTP on loopback")
        self.session = requests.Session()
        self.session.trust_env = False
        if session_env:
            from nexus.core.studio_connections import env_name

            token = os.getenv(env_name(session_env))
            if not token or any(c in token for c in "\r\n"):
                raise ValueError(
                    "Configured Nexus session environment reference is empty or invalid"
                )
            self.session.headers["X-Nexus-Session"] = token
        try:
            bootstrap = self.request("/api/bootstrap")
            self.session.headers["X-Nexus-Studio-Token"] = bootstrap["token"]
        except BaseException:
            self.close()
            raise

    def request(self, path, data=None):
        if not path.startswith("/api/"):
            raise ValueError("Studio client requests use the existing API")
        try:
            with self.session.request(
                "GET" if data is None else "POST",
                self.url + path,
                json=data,
                timeout=30,
                allow_redirects=False,
                stream=True,
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ValueError(
                        "Studio rejected the request; check login, access, current revision and server availability"
                    )
                raw = bytearray()
                for chunk in response.iter_content(16384):
                    raw.extend(chunk)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError("Studio response exceeds two MiB")
                return json.loads(raw)
        except requests.RequestException:
            raise ValueError(
                "Studio connection failed. Inspect Activity before resubmitting an action; its outcome may be uncertain."
            ) from None

    def close(self):
        self.session.close()

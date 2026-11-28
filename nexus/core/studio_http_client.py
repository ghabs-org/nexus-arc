"""Authenticated client for the existing Studio HTTP boundary; never replays POSTs."""

import hashlib
import json
import os
from pathlib import Path
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
            self.capabilities = bootstrap.get("capabilities", {})
            self.terminal_scope = {
                "origin": self.url,
                "workspace": bootstrap.get("workspace", {}).get("path"),
                "user": (bootstrap.get("user") or {}).get("nexus_id"),
                "mode": bootstrap.get("mode"),
            }
        except BaseException:
            self.close()
            raise

    def request(self, path, data=None):
        return json.loads(self._read(path, data, 2 * 1024 * 1024))

    def download(self, path, destination, *, sha256=None):
        """Read a bounded original artifact, then create a private new local file."""
        from nexus.core.studio_artifacts import ARTIFACT_LIMIT

        content = self._read(path, None, ARTIFACT_LIMIT)
        if sha256 and hashlib.sha256(content).hexdigest() != sha256:
            raise ValueError("Downloaded artifact failed integrity verification")
        write_private_file(destination, content)

    def _read(self, path, data, limit):
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
                    if len(raw) > limit:
                        raise ValueError(f"Studio response exceeds {limit} bytes")
                return bytes(raw)
        except requests.RequestException:
            raise ValueError(
                "Studio connection failed. Inspect Activity before resubmitting an action; its outcome may be uncertain."
            ) from None

    def close(self):
        self.session.close()


def write_private_file(destination, content):
    """Never overwrite an existing file or follow a destination symlink."""
    path = Path(destination).expanduser()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)

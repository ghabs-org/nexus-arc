"""Private daemon/project-bound ownership receipts for the filtering proxy.

This operational store must live outside every writable Studio mount. It grants
no account identity or task execution. SQLite transactions commit ownership before
acknowledgment; its exclusive connection lock prevents concurrent proxy writers.
The module remains standard-library-only for the standalone deployment.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import stat
from pathlib import Path

FORMAT = "nexus-docker-proxy-ownership/v1"
MAX_BYTES = 1024 * 1024


def _binding(daemon, project):
    if not isinstance(daemon, str) or not re.fullmatch(r"[A-Za-z0-9:_.-]{1,256}", daemon):
        raise ValueError("Docker daemon identity could not be verified")
    if not isinstance(project, str) or not 1 <= len(project) <= 256:
        raise ValueError("Persistent proxy ownership requires an existing Nexus project key")
    return hashlib.sha256(json.dumps([daemon, project]).encode()).hexdigest()


def _records(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError("Invalid proxy ownership inventory")
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"id", "name", "sources", "retired"}:
            raise ValueError("Invalid proxy ownership receipt")
        identity, name, sources = item["id"], item["name"], item["sources"]
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"[a-f0-9]{64}", identity)
            or identity in seen
            or not isinstance(name, str)
            or not re.fullmatch(r"nexus-(?:analysis|studio)-[a-f0-9]{32}", name)
            or type(item["retired"]) is not bool
            or not isinstance(sources, list)
            or len(sources) > 16
            or any(
                not isinstance(path, str)
                or not Path(path).is_absolute()
                or "\x00" in path
                or len(path) > 4096
                for path in sources
            )
        ):
            raise ValueError("Invalid proxy ownership receipt")
        seen.add(identity)
    return value


def _private(path, directory=False):
    info = path.lstat()
    ordinary = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not ordinary
        or info.st_mode & 0o077
        or (hasattr(os, "getuid") and info.st_uid != os.getuid())
        or (not directory and info.st_nlink != 1)
    ):
        raise ValueError("Proxy ownership storage must be private and owned by its service user")


class ProxyJournal:
    def __init__(self, directory, daemon, project, *, maximum=512):
        self.connection = None
        self.project = project
        self.binding = _binding(daemon, project)
        self.maximum = maximum
        root = Path(directory).absolute()
        if root != root.resolve(strict=True):
            raise ValueError("Proxy ownership storage cannot contain symbolic links")
        _private(root, directory=True)
        path = root / "ownership.sqlite"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        _private(path)
        connection = sqlite3.connect(path, timeout=0)
        try:
            connection.execute("PRAGMA locking_mode=EXCLUSIVE")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=EXTRA")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS ownership (singleton INTEGER PRIMARY KEY CHECK(singleton=1), format TEXT NOT NULL, binding TEXT NOT NULL, records TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT format,binding,records FROM ownership WHERE singleton=1"
            ).fetchone()
            if row:
                if row[0] != FORMAT or row[1] != self.binding:
                    raise ValueError("Proxy ownership belongs to another daemon/project or version")
                self._decode(row[2])
            else:
                connection.execute(
                    "INSERT INTO ownership VALUES (1,?,?,?)", (FORMAT, self.binding, "[]")
                )
            connection.commit()
            # Exclusive locking mode retains the kernel lock after each commit.
            self.connection = connection
        except BaseException:
            connection.close()
            raise

    def _decode(self, raw):
        if not isinstance(raw, str) or len(raw.encode()) > MAX_BYTES:
            raise ValueError("Invalid proxy ownership inventory")
        return _records(json.loads(raw), self.maximum)

    def load(self):
        row = self.connection.execute("SELECT records FROM ownership WHERE singleton=1").fetchone()
        return self._decode(row[0])

    def save(self, records):
        raw = json.dumps(_records(records, self.maximum), separators=(",", ":"))
        if len(raw.encode()) > MAX_BYTES:
            raise ValueError("Proxy ownership inventory exceeds its bound")
        with self.connection:
            self.connection.execute("UPDATE ownership SET records=? WHERE singleton=1", (raw,))

    def require_daemon(self, daemon):
        if _binding(daemon, self.project) != self.binding:
            raise ValueError("Docker daemon changed; proxy ownership remains unconfirmed")

    def require_project(self, project):
        if project != self.project:
            raise ValueError("Proxy project changed; ownership remains unconfirmed")

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None


async def daemon_identity(reader, writer, *, timeout=5):
    """Verify identity on the same keep-alive connection used for the next call."""

    async def receive():
        writer.write(b"GET /info HTTP/1.1\r\nHost: docker\r\nConnection: keep-alive\r\n\r\n")
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        if len(head) > 65536 or head.split(b"\r\n", 1)[0].split()[1] != b"200":
            raise ValueError("Docker daemon identity could not be verified")
        headers = {}
        for line in head.split(b"\r\n")[1:-2]:
            key, _, value = line.partition(b":")
            headers[key.strip().lower()] = value.strip().lower()
        if headers.get(b"connection") == b"close":
            raise ValueError("Docker identity connection is not reusable")
        body = bytearray()
        if headers.get(b"transfer-encoding") == b"chunked":
            while True:
                line = await reader.readline()
                size = int(line.split(b";", 1)[0].strip(), 16)
                if size < 0 or len(body) + size > MAX_BYTES:
                    raise ValueError("Docker identity response exceeds its bound")
                if not size:
                    trailers = 0
                    while True:
                        trailer = await reader.readline()
                        trailers += len(trailer)
                        if not trailer or trailers > 65536:
                            raise ValueError("Invalid Docker identity response")
                        if trailer == b"\r\n":
                            break
                    break
                body.extend(await reader.readexactly(size))
                if await reader.readexactly(2) != b"\r\n":
                    raise ValueError("Invalid Docker identity response")
        else:
            length = int(headers.get(b"content-length", b"-1"))
            if not 0 <= length <= MAX_BYTES:
                raise ValueError("Docker identity response is not bounded")
            body.extend(await reader.readexactly(length))
        value = json.loads(body)
        identity = value.get("ID") if isinstance(value, dict) else None
        _binding(identity, "identity-probe")
        return identity

    return await asyncio.wait_for(receive(), timeout)

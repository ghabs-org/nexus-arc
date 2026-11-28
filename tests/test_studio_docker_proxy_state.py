"""Private ownership commits, binding, crash-safe locking and wire boundaries."""

import asyncio
import json
import os
import sqlite3
from unittest.mock import AsyncMock

import pytest

from nexus.core.studio_docker_proxy_policy import ProxyContainers
from nexus.core.studio_docker_proxy_state import MAX_BYTES, ProxyJournal, daemon_identity

IDENTITY = "a" * 64
NAME = "nexus-analysis-" + "b" * 32


@pytest.fixture
def directory(tmp_path):
    folder = (tmp_path / "proxy-private").resolve()
    folder.mkdir(mode=0o700)
    return folder


def test_committed_ownership_returns_as_cleanup_only_after_restart(directory):
    journal = ProxyJournal(directory, "daemon-one", "project-one")
    tracker = ProxyContainers(journal=journal)
    tracker.add(IDENTITY, NAME, ["/configured/model.pt"])
    assert not tracker.cleanup_only(IDENTITY)
    tracker.retire(IDENTITY)
    journal.close()
    reopened = ProxyJournal(directory, "daemon-one", "project-one")
    try:
        copy = ProxyContainers(journal=reopened)
        assert copy.resolve(NAME) == IDENTITY and copy.cleanup_only(IDENTITY)
        assert copy.sources(IDENTITY) == ["/configured/model.pt"]
        assert IDENTITY in copy._retired
        assert reopened.connection.execute("PRAGMA synchronous").fetchone() == (3,)
        assert (directory / "ownership.sqlite").stat().st_mode & 0o777 == 0o600
        with pytest.raises(ValueError, match="daemon changed"):
            reopened.require_daemon("another-daemon")
        with pytest.raises(ValueError, match="project changed"):
            reopened.require_project("another-project")
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "daemon,project", [("other-daemon", "project-one"), ("daemon-one", "other-project")]
)
def test_wrong_daemon_or_project_cannot_take_over_receipts(directory, daemon, project):
    journal = ProxyJournal(directory, "daemon-one", "project-one")
    ProxyContainers(journal=journal).add(IDENTITY, NAME, [])
    journal.close()
    original = (directory / "ownership.sqlite").read_bytes()
    with pytest.raises(ValueError, match="another daemon/project"):
        ProxyJournal(directory, daemon, project)
    assert (directory / "ownership.sqlite").read_bytes() == original


def test_exclusive_writer_lock_survives_commits_until_connection_closes(directory):
    first = ProxyJournal(directory, "daemon", "project")
    ProxyContainers(journal=first).add(IDENTITY, NAME, [])
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        ProxyJournal(directory, "daemon", "project")
    first.close()
    second = ProxyJournal(directory, "daemon", "project")
    second.close()


@pytest.mark.parametrize("kind", ["directory_mode", "file_mode", "symlink", "hardlink"])
def test_unsafe_storage_is_rejected_before_granting_ownership(directory, tmp_path, kind):
    if kind == "directory_mode":
        directory.chmod(0o750)
    elif kind == "file_mode":
        (directory / "ownership.sqlite").write_bytes(b"")
        (directory / "ownership.sqlite").chmod(0o644)
    else:
        source = tmp_path / "unrelated"
        source.write_bytes(b"do not modify")
        source.chmod(0o600)
        target = directory / "ownership.sqlite"
        if kind == "symlink":
            target.symlink_to(source)
        else:
            os.link(source, target)
    with pytest.raises(ValueError, match="private"):
        ProxyJournal(directory, "daemon", "project")
    if kind in {"symlink", "hardlink"}:
        assert source.read_bytes() == b"do not modify"


@pytest.mark.parametrize("change", ["format", "invalid_records", "corrupt_database"])
def test_damaged_or_future_storage_never_becomes_an_empty_trusted_inventory(directory, change):
    journal = ProxyJournal(directory, "daemon", "project")
    ProxyContainers(journal=journal).add(IDENTITY, NAME, [])
    journal.close()
    if change == "corrupt_database":
        (directory / "ownership.sqlite").write_bytes(b"corrupt")
    else:
        with sqlite3.connect(directory / "ownership.sqlite") as connection:
            if change == "format":
                connection.execute("UPDATE ownership SET format='future/v2'")
            else:
                connection.execute('UPDATE ownership SET records=\'[{"id":"unowned"}]\'')
    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        ProxyJournal(directory, "daemon", "project")


def test_storage_failure_keeps_memory_authority_and_last_commit_for_review(directory, monkeypatch):
    journal = ProxyJournal(directory, "daemon", "project")
    tracker = ProxyContainers(journal=journal)
    tracker.add(IDENTITY, NAME, [])
    save = journal.save

    def full(records):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(journal, "save", full)
    other = "c" * 64
    with pytest.raises(sqlite3.OperationalError):
        tracker.add(other, "nexus-analysis-" + "c" * 32, [])
    assert tracker.storage_failed and tracker.resolve(other) == other
    assert [item["id"] for item in journal.load()] == [IDENTITY]
    monkeypatch.setattr(journal, "save", save)
    tracker.retire(other)
    assert not tracker.storage_failed
    assert len(journal.load()) == 2
    journal.close()


class Writer:
    def __init__(self):
        self.sent = bytearray()
        self.drain = AsyncMock()

    def write(self, value):
        self.sent.extend(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("chunked", [False, True])
async def test_identity_consumes_one_response_and_retains_same_connection(chunked):
    body = json.dumps({"ID": "daemon-one", "unrelated": "discarded metadata"}).encode()
    reader = asyncio.StreamReader()
    if chunked:
        response = (
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
            + f"{len(body):x}\r\n".encode()
            + body
            + b"\r\n0\r\nX-Trace: ignored\r\n\r\n"
        )
    else:
        response = f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
    reader.feed_data(response + b"next-response")
    writer = Writer()
    assert await daemon_identity(reader, writer) == "daemon-one"
    assert await reader.readexactly(13) == b"next-response"
    assert writer.sent == b"GET /info HTTP/1.1\r\nHost: docker\r\nConnection: keep-alive\r\n\r\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\n\r\n",
        f"HTTP/1.1 200 OK\r\nContent-Length: {MAX_BYTES + 1}\r\n\r\n".encode(),
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n200000\r\n",
    ],
)
async def test_unverified_or_unbounded_identity_response_fails_closed(response):
    reader = asyncio.StreamReader()
    reader.feed_data(response)
    reader.feed_eof()
    with pytest.raises(ValueError):
        await daemon_identity(reader, Writer())


@pytest.mark.asyncio
async def test_unresponsive_identity_has_one_bounded_request_without_retry():
    writer = Writer()
    with pytest.raises(TimeoutError):
        await daemon_identity(asyncio.StreamReader(), writer, timeout=0.02)
    assert writer.sent.count(b"GET /info") == 1

"""Parent EOF stops native HTTP service without changing browser/team launches."""

import io
import os
import threading

import pytest

from nexus.core.studio_desktop_parent import watch_parent_input
from nexus.studio import serve_studio


def test_parent_pipe_keeps_service_until_writer_closes():
    reader_fd, writer_fd = os.pipe()
    stopped = threading.Event()
    with os.fdopen(reader_fd, "rb", buffering=0) as reader:
        writer = os.fdopen(writer_fd, "wb", buffering=0)
        worker = watch_parent_input(reader, stopped.set)
        try:
            assert not stopped.wait(0.05)
            writer.close()
            assert stopped.wait(2)
            worker.join(2)
            assert not worker.is_alive()
        finally:
            writer.close()


@pytest.mark.parametrize("content", [b"", b"unexpected-after-credential"])
def test_eof_or_extra_bytes_end_the_launch_without_echo(content):
    stopped = []
    worker = watch_parent_input(io.BytesIO(content), lambda: stopped.append(True))
    worker.join(2)
    assert stopped == [True] and not worker.is_alive()


def test_broken_parent_input_stops_service():
    stream = io.BytesIO()
    stream.close()
    stopped = threading.Event()
    worker = watch_parent_input(stream, stopped.set)
    worker.join(2)
    assert stopped.is_set() and not worker.is_alive()


@pytest.mark.parametrize("worker_only", [False, True])
def test_parent_input_requires_native_http_launch(tmp_path, worker_only):
    with pytest.raises(ValueError, match="native desktop HTTP launch"):
        serve_studio(tmp_path, port=0, parent_input=io.BytesIO(), worker_only=worker_only)
    assert not (tmp_path / ".nexus-studio").exists()


def test_invalid_parent_stream_is_rejected_before_thread_start():
    with pytest.raises(ValueError, match="readable stream"):
        watch_parent_input(object(), lambda: pytest.fail("must not start a watcher"))

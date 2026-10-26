"""Service termination is cooperative and never replaces an embedder's policy."""

import signal
import threading
from types import SimpleNamespace

import pytest

from nexus.core.studio_termination import serve_until_stopped, termination_requested


def test_sigterm_requests_stop_without_raising_and_restores_prior_handler():
    previous = signal.getsignal(signal.SIGTERM)
    with termination_requested() as stop:
        handler = signal.getsignal(signal.SIGTERM)
        assert handler is not previous and not stop.is_set()
        handler(signal.SIGTERM, None)
        handler(signal.SIGTERM, None)
        assert stop.is_set()
    assert signal.getsignal(signal.SIGTERM) is previous


def test_exception_restores_signal_policy():
    previous = signal.getsignal(signal.SIGTERM)
    with pytest.raises(ValueError), termination_requested():
        raise ValueError("startup failed")
    assert signal.getsignal(signal.SIGTERM) is previous


def test_embedded_thread_does_not_install_signal_handler():
    previous = signal.getsignal(signal.SIGTERM)
    results = []

    def embedded():
        with termination_requested() as stop:
            results.append(signal.getsignal(signal.SIGTERM) is previous and not stop.is_set())

    worker = threading.Thread(target=embedded)
    worker.start()
    worker.join(2)
    assert results == [True] and signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.parametrize("request_stop", [False, True])
def test_http_shutdown_runs_outside_listener_thread_and_watcher_exits(request_stop):
    stop = threading.Event()
    finished = threading.Event()
    calls = []
    serving_thread = threading.get_ident()

    class Server:
        def serve_forever(self):
            if request_stop:
                stop.set()
                assert finished.wait(2)

        def shutdown(self):
            calls.append(threading.get_ident())
            finished.set()

    serve_until_stopped(Server(), stop)
    assert len(calls) == int(request_stop)
    assert all(identity != serving_thread for identity in calls)
    assert not any(thread.name == "nexus-studio-termination" for thread in threading.enumerate())


def test_stop_before_http_start_never_calls_shutdown_without_a_listener():
    stop = threading.Event()
    stop.set()
    serve_until_stopped(object(), stop)


@pytest.mark.parametrize("worker_only", [False, True])
def test_stop_during_startup_closes_service_before_dispatch_or_listening(
    tmp_path, monkeypatch, worker_only
):
    import nexus.studio as studio

    closed = []
    previous = signal.getsignal(signal.SIGTERM)

    def initialize(*args, **kwargs):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        return SimpleNamespace(close=lambda: closed.append(True))

    monkeypatch.setattr(studio, "StudioService", initialize)
    studio.serve_studio(tmp_path, port=0, worker_only=worker_only)
    assert closed == [True] and signal.getsignal(signal.SIGTERM) is previous
    assert not studio.WorkspaceLock(tmp_path).is_locked()

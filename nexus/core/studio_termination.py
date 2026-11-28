"""Cooperative service-manager termination without interrupting state writes.

Only a main-thread service launch owns SIGTERM. Embedded thread launches retain
the host application's signal policy. The handler requests shutdown; existing
Studio cleanup remains responsible for jobs, subprocesses, transports and locks.
"""

import signal
import threading
from contextlib import contextmanager


@contextmanager
def termination_requested():
    stop = threading.Event()
    owns_signal = threading.current_thread() is threading.main_thread()
    previous = None
    if owns_signal:
        previous = signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        yield stop
    finally:
        if owns_signal:
            signal.signal(signal.SIGTERM, previous)


def serve_until_stopped(server, stop):
    """Call HTTP shutdown outside serve_forever's thread, as its API requires."""
    if stop.is_set():
        return
    finished = threading.Event()

    def watch():
        while not finished.wait(0.05):
            if stop.is_set():
                server.shutdown()
                return

    watcher = threading.Thread(target=watch, name="nexus-studio-termination", daemon=True)
    watcher.start()
    try:
        server.serve_forever()
    finally:
        finished.set()
        watcher.join(timeout=1)

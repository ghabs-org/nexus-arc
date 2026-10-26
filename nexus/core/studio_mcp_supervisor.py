"""Isolated Linux stdio owner. Standard library only; never imports Studio/SDKs.

The process owns one MCP child tree. It does not scan other processes or share
subreaper state with the application, its worker threads or other MCP sessions.
"""

import ctypes
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


def _configure():
    if (
        sys.platform != "linux"
        or not hasattr(os, "pidfd_open")
        or not hasattr(signal, "pidfd_send_signal")
    ):
        raise OSError("Linux supervision unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError("Linux subreaper unavailable")
    descriptor = os.pidfd_open(os.getpid())
    try:
        signal.pidfd_send_signal(descriptor, 0)
    finally:
        os.close(descriptor)
    return Path(f"/proc/self/task/{os.getpid()}/children")


def _cleanup(children_path, *, seconds=1.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            return True
        try:
            with children_path.open("rb") as stream:
                children = stream.read(65536).split()[:512]
        except OSError:
            return False
        for child in children:
            descriptor = None
            try:
                pid = int(child)
                descriptor = os.pidfd_open(pid)
                # Opening the pidfd first fences PID reuse before the parent
                # check. Signal only a current direct/adopted child of this owner.
                with Path(f"/proc/{pid}/stat").open("rb") as stream:
                    record = stream.read(65536)
                parent = int(record.rsplit(b")", 1)[1].split()[1])
                if parent == os.getpid():
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except (OSError, ValueError, IndexError):
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        time.sleep(0.005)
    return False


def supervise(command):
    children_path = _configure()
    # Fail before starting the server if the required own-child view is hidden.
    with children_path.open("rb") as stream:
        stream.read(1)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    initial = select.poll()
    initial.register(0, 0)
    owner_gone = any(
        event & (select.POLLHUP | select.POLLERR | select.POLLNVAL) for _, event in initial.poll(0)
    )
    if stopping or owner_gone:
        return 0
    process = subprocess.Popen(command, stdin=subprocess.PIPE, bufsize=0, close_fds=True)
    pending = bytearray()
    result = 0
    try:
        os.set_blocking(0, False)
        target = process.stdin.fileno()
        os.set_blocking(target, False)
        poller = select.poll()
        poller.register(0, select.POLLIN)
        poller.register(target, 0)
        while not stopping and process.poll() is None:
            for descriptor, event in poller.poll(50):
                if event & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                    stopping = True
                    break
                if descriptor == 0 and not pending and event & select.POLLIN:
                    chunk = os.read(0, 65536)
                    if not chunk:
                        stopping = True
                        break
                    pending.extend(chunk)
                    poller.modify(0, 0)
                    poller.modify(target, select.POLLOUT)
                elif descriptor == target and pending and event & select.POLLOUT:
                    try:
                        sent = os.write(target, pending)
                    except BlockingIOError:
                        continue
                    del pending[:sent]
                    if not pending:
                        poller.modify(target, 0)
                        poller.modify(0, select.POLLIN)
        # poll reports HUP even when input is backpressured and POLLIN disabled.
        # Owner death therefore cannot leave a server waiting on a full pipe.
        result = process.poll() or 0
    except (BrokenPipeError, ConnectionError):
        result = 1
    finally:
        confirmed = _cleanup(children_path)
        process.stdin.close()
        if not confirmed:
            print("MCP descendant cleanup remains unconfirmed.", file=sys.stderr, flush=True)
            result = 125
    return result if result >= 0 else 1


def main():
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or not command[0]:
        print("Specify an MCP server command.", file=sys.stderr)
        raise SystemExit(2)
    try:
        result = supervise(command)
    except (OSError, ValueError):
        # Launch errors may contain private paths or server arguments.
        print("Linux MCP supervision could not initialize or launch the server.", file=sys.stderr)
        result = 125
    raise SystemExit(result)


if __name__ == "__main__":
    main()

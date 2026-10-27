"""Isolated Linux stdio owner. Standard library only; never imports Studio/SDKs.

The process owns one MCP child tree. It does not scan other processes or share
subreaper state with the application, its worker threads or other MCP sessions.
The private speech mode reuses this ownership for commands with finite input.
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
    if sys.platform == "linux":
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
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
    if sys.platform == "darwin":
        if os.name != "posix":
            raise OSError("Darwin supervision unavailable")
        try:
            libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
            fn = libproc.proc_listchildpids
            fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            fn.restype = ctypes.c_int
            buf = (ctypes.c_int * 1)()
            fn(os.getpid(), buf, ctypes.sizeof(buf))
            return fn
        except Exception:
            raise OSError("Darwin supervision unavailable")
    raise OSError("Supervision unavailable on this platform")


def _darwin_descendants(pid, fn):
    pids = [pid]
    idx = 0
    buf = (ctypes.c_int * 1024)()
    while idx < len(pids):
        curr = pids[idx]
        n = fn(curr, buf, ctypes.sizeof(buf))
        if n > 0:
            for i in range(n):
                cp = buf[i]
                if cp not in pids and cp > 0:
                    pids.append(cp)
        idx += 1
    return pids


def _cleanup_darwin(process, known, fn, *, seconds=1.0):
    if fn and process and getattr(process, "pid", None):
        try:
            known.update(_darwin_descendants(process.pid, fn))
        except Exception:
            pass
    if process and getattr(process, "pid", None):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
    for pid in list(known):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        alive = []
        for pid in list(known):
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except OSError:
                pass
        if not alive and (process is None or process.poll() is not None):
            return True
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        time.sleep(0.005)
    return False


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
    if not isinstance(children_path, Path):
        raise OSError("Linux supervision unavailable")
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
    speech = command[:1] == ["--speech"]
    if speech:
        command = command[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or not command[0]:
        print("Specify an MCP server command.", file=sys.stderr)
        raise SystemExit(2)
    try:
        result = supervise_speech(command) if speech else supervise(command)
    except (OSError, ValueError):
        # Launch errors may contain private paths or server arguments.
        print(
            "Linux speech supervision could not initialize or launch the worker."
            if speech and sys.platform == "linux"
            else (
                "Local speech supervision could not initialize or launch the worker."
                if speech
                else "Linux MCP supervision could not initialize or launch the server."
            ),
            file=sys.stderr,
        )
        result = 125
    raise SystemExit(result)


def supervise_speech(command):
    """Deliver finite speech input while keeping stdin as a separate owner lease."""
    import base64
    import json

    context = _configure()
    if isinstance(context, Path):
        with context.open("rb") as stream:
            stream.read(1)
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    header = sys.stdin.buffer.readline(1048577)
    if not header or stopping:
        return 0
    if len(header) > 1048576 or not header.endswith(b"\n"):
        raise ValueError("Invalid local speech frame")
    payload = json.loads(header)
    if not isinstance(payload, dict) or set(payload) != {"input"}:
        raise ValueError("Invalid local speech frame")
    content = payload["input"]
    if content is not None and not isinstance(content, str):
        raise ValueError("Invalid local speech frame")
    pending = bytearray(base64.b64decode(content, validate=True) if content is not None else b"")
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        bufsize=0,
        close_fds=True,
        start_new_session=True if os.name == "posix" else False,
    )
    proc_fn = context if not isinstance(context, Path) else None
    known_descendants = set([process.pid])
    result = 0
    try:
        poller = select.poll()
        poller.register(0, select.POLLIN)
        target = process.stdin.fileno()
        if pending:
            os.set_blocking(target, False)
            poller.register(target, select.POLLOUT)
        else:
            process.stdin.close()
        while not stopping and process.poll() is None:
            if proc_fn:
                try:
                    known_descendants.update(_darwin_descendants(process.pid, proc_fn))
                except Exception:
                    pass
            for descriptor, event in poller.poll(50):
                if descriptor == 0:
                    if event & (select.POLLHUP | select.POLLERR | select.POLLNVAL | select.POLLIN):
                        stopping = True
                        break
                elif event & select.POLLOUT:
                    try:
                        sent = os.write(target, pending)
                    except BlockingIOError:
                        continue
                    del pending[:sent]
                    if not pending:
                        poller.unregister(target)
                        process.stdin.close()
                elif event & (select.POLLHUP | select.POLLERR | select.POLLNVAL):
                    stopping = True
                    break
        result = process.poll() or 0
    except BrokenPipeError:
        result = 1
    finally:
        if isinstance(context, Path):
            confirmed = _cleanup(context)
        else:
            confirmed = _cleanup_darwin(process, known_descendants, proc_fn)
        process.stdin.close()
        if not confirmed:
            print(
                "Local speech descendant cleanup remains unconfirmed.", file=sys.stderr, flush=True
            )
            result = 125
    return result if result >= 0 else 1


if __name__ == "__main__":
    main()

"""Private Windows speech owner; standard library only, no SDK bootstrap.

Start suspended, assign to a kill-on-close Job Object, then resume. The job
handle is never inherited. Closing the controller lease or killing this owner
therefore terminates the owned tree, including detached descendants.
"""

import base64
import ctypes
import json
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes as w


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("user", ctypes.c_int64),
        ("job_user", ctypes.c_int64),
        ("flags", w.DWORD),
        ("minimum", ctypes.c_size_t),
        ("maximum", ctypes.c_size_t),
        ("active", w.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", w.DWORD),
        ("scheduling", w.DWORD),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process", ctypes.c_size_t),
        ("peak_job", ctypes.c_size_t),
    ]


class Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4),
        ("faults", w.DWORD),
        ("total", w.DWORD),
        ("active", w.DWORD),
        ("terminated", w.DWORD),
    ]


class Startup(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD),
        ("reserved", w.LPWSTR),
        ("desktop", w.LPWSTR),
        ("title", w.LPWSTR),
        ("x", w.DWORD),
        ("y", w.DWORD),
        ("xsize", w.DWORD),
        ("ysize", w.DWORD),
        ("xchars", w.DWORD),
        ("ychars", w.DWORD),
        ("fill", w.DWORD),
        ("flags", w.DWORD),
        ("show", w.WORD),
        ("reserved_size", w.WORD),
        ("reserved_data", w.LPBYTE),
        ("stdin", w.HANDLE),
        ("stdout", w.HANDLE),
        ("stderr", w.HANDLE),
    ]


class StartupEx(ctypes.Structure):
    _fields_ = [("startup", Startup), ("attributes", w.LPVOID)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class WindowsJob:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Windows speech ownership requires Windows")
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        definitions = {
            "CreateJobObjectW": ([w.LPVOID, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
            "QueryInformationJobObject": (
                [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID],
                w.BOOL,
            ),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "GetCurrentProcess": ([], w.HANDLE),
            "DuplicateHandle": (
                [w.HANDLE, w.HANDLE, w.HANDLE, ctypes.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD],
                w.BOOL,
            ),
            "CreateProcessW": (
                [
                    w.LPCWSTR,
                    w.LPWSTR,
                    w.LPVOID,
                    w.LPVOID,
                    w.BOOL,
                    w.DWORD,
                    w.LPVOID,
                    w.LPCWSTR,
                    ctypes.POINTER(StartupEx),
                    ctypes.POINTER(ProcessInfo),
                ],
                w.BOOL,
            ),
            "InitializeProcThreadAttributeList": (
                [w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)],
                w.BOOL,
            ),
            "UpdateProcThreadAttribute": (
                [w.LPVOID, w.DWORD, ctypes.c_size_t, w.LPVOID, ctypes.c_size_t, w.LPVOID, w.LPVOID],
                w.BOOL,
            ),
            "DeleteProcThreadAttributeList": ([w.LPVOID], None),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
        }
        for name, (arguments, result) in definitions.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.job = self.api.CreateJobObjectW(None, None)
        self.process = None
        if not self.job:
            raise OSError("Windows speech job creation failed")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not self.api.SetInformationJobObject(
            self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            self.close()
            raise OSError("Windows speech job limits unavailable")

    def launch(self, command, content):
        import msvcrt

        copies = []
        info = ProcessInfo()
        attributes = None
        try:
            with tempfile.TemporaryFile() as source:
                source.write(content)
                source.seek(0)
                extended = StartupEx()
                startup = extended.startup
                startup.cb, startup.flags = ctypes.sizeof(extended), 0x100  # STARTF_USESTDHANDLES
                current = self.api.GetCurrentProcess()
                for name, stream in (
                    ("stdin", source),
                    ("stdout", sys.stdout),
                    ("stderr", sys.stderr),
                ):
                    handle = w.HANDLE()
                    if not self.api.DuplicateHandle(
                        current,
                        msvcrt.get_osfhandle(stream.fileno()),
                        current,
                        ctypes.byref(handle),
                        0,
                        True,
                        2,
                    ):
                        raise OSError("Windows speech stream duplication failed")
                    copies.append(handle)
                    setattr(startup, name, handle)
                # Explicit inheritance excludes the controller lease, job handle
                # and any handles inherited by this private owner.
                size = ctypes.c_size_t()
                self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                storage = ctypes.create_string_buffer(size.value)
                if not self.api.InitializeProcThreadAttributeList(
                    storage, 1, 0, ctypes.byref(size)
                ):
                    raise OSError("Windows speech handle list unavailable")
                attributes = storage
                handles = (w.HANDLE * len(copies))(*(handle.value for handle in copies))
                if not self.api.UpdateProcThreadAttribute(
                    attributes, 0, 0x20002, handles, ctypes.sizeof(handles), None, None
                ):
                    raise OSError("Windows speech handle restriction failed")
                extended.attributes = ctypes.cast(attributes, w.LPVOID)
                arguments = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
                if not self.api.CreateProcessW(
                    None,
                    arguments,
                    None,
                    None,
                    True,
                    4 | 0x80000,
                    None,
                    None,
                    ctypes.byref(extended),
                    ctypes.byref(info),
                ):
                    raise OSError("Windows speech worker creation failed")
                self.process = info.process
                if not self.api.AssignProcessToJobObject(self.job, self.process):
                    raise OSError("Windows speech job assignment failed")
                if self.api.ResumeThread(info.thread) == 0xFFFFFFFF:
                    raise OSError("Windows speech worker resume failed")
        except BaseException:
            if self.process:
                # Assignment can fail under incompatible enclosing jobs. Never
                # resume an unowned child or leave its suspended process alive.
                self.api.TerminateProcess(self.process, 125)
                self.api.WaitForSingleObject(self.process, 3000)
            raise
        finally:
            if attributes is not None:
                self.api.DeleteProcThreadAttributeList(attributes)
            if info.thread:
                self.api.CloseHandle(info.thread)
            for handle in copies:
                self.api.CloseHandle(handle)

    def poll(self):
        state = self.api.WaitForSingleObject(self.process, 0)
        if state == 258:  # WAIT_TIMEOUT
            return None
        if state != 0:
            raise OSError("Windows speech process wait failed")
        result = w.DWORD()
        if not self.api.GetExitCodeProcess(self.process, ctypes.byref(result)):
            raise OSError("Windows speech exit status unavailable")
        return result.value

    def stop(self):
        if not self.api.TerminateJobObject(self.job, 125):
            raise OSError("Windows speech tree termination failed")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            counts = Accounting()
            if not self.api.QueryInformationJobObject(
                self.job, 1, ctypes.byref(counts), ctypes.sizeof(counts), None
            ):
                raise OSError("Windows speech cleanup cannot be confirmed")
            if counts.active == 0:
                return
            time.sleep(0.01)
        raise OSError("Windows speech descendant cleanup remains unconfirmed")

    def close(self):
        if self.job:
            self.api.CloseHandle(self.job)
            self.job = None
        if self.process:
            self.api.CloseHandle(self.process)
            self.process = None


def supervise(command, *, lease=None, job_factory=WindowsJob):
    lease = lease if lease is not None else sys.stdin.buffer
    header = lease.readline(1048577)
    if not header:
        return 0
    if len(header) > 1048576 or not header.endswith(b"\n"):
        raise ValueError("Invalid local speech frame")
    payload = json.loads(header)
    if not isinstance(payload, dict) or set(payload) != {"input"}:
        raise ValueError("Invalid local speech frame")
    content = payload["input"]
    if content is not None and not isinstance(content, str):
        raise ValueError("Invalid local speech frame")
    content = base64.b64decode(content, validate=True) if content is not None else b""
    stopped = threading.Event()

    def watch():
        try:
            lease.read(1)
        finally:
            stopped.set()

    threading.Thread(target=watch, daemon=True).start()
    job = job_factory()
    try:
        if stopped.is_set():
            return 0
        job.launch(command, content)
        while not stopped.wait(0.02):
            result = job.poll()
            if result is not None:
                return result
        return 0
    finally:
        try:
            job.stop()
        finally:
            job.close()


def main():
    command = sys.argv[1:]
    if command[:2] != ["--speech", "--"] or len(command) < 3:
        raise SystemExit(2)
    try:
        result = supervise(command[2:])
    except (OSError, ValueError):
        # Never expose paths, command arguments, text or audio in launch errors.
        print(
            "Windows speech supervision could not initialize or confirm cleanup.", file=sys.stderr
        )
        result = 125
    raise SystemExit(result)


if __name__ == "__main__":
    main()

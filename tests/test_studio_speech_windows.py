"""Portable Windows ownership guards; these do not certify native Win32 behavior."""

import base64
import ctypes
import io
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nexus.core import studio_desktop_sidecar as sidecar
from nexus.core import studio_speech_windows as windows


@pytest.mark.parametrize(
    "frame", [b"{}\n", b'{"input":123}\n', b'{"input":"!"}\n', b'{"input":null}', b"x" * 1048577]
)
def test_invalid_frame_never_constructs_or_launches_a_job(frame):
    factory = Mock(side_effect=AssertionError("No worker may start"))
    with pytest.raises(ValueError):
        windows.supervise(["not-launched"], lease=io.BytesIO(frame), job_factory=factory)
    factory.assert_not_called()


def test_closed_lease_never_constructs_job():
    factory = Mock()
    assert windows.supervise(["not-launched"], lease=io.BytesIO(), job_factory=factory) == 0
    factory.assert_not_called()


class Lease:
    def __init__(self, content=b"finite input"):
        self.frame = b'{"input":"' + base64.b64encode(content) + b'"}\n'
        self.closed = threading.Event()

    def readline(self, _limit):
        return self.frame

    def read(self, _count):
        self.closed.wait(2)
        return b""


@pytest.mark.parametrize("stop", [False, True])
def test_lease_loss_and_command_exit_both_confirm_cleanup(stop):
    lease = Lease()
    job = Mock()
    job.launch.side_effect = lambda *_: lease.closed.set() if stop else None
    job.poll.return_value = 7
    try:
        result = windows.supervise(["literal; argument"], lease=lease, job_factory=lambda: job)
        assert result == (0 if stop else 7)
        job.launch.assert_called_once_with(["literal; argument"], b"finite input")
        job.stop.assert_called_once_with()
        job.close.assert_called_once_with()
    finally:
        lease.closed.set()


@pytest.mark.parametrize("failure", ["launch", "stop"])
def test_failure_still_closes_job_and_never_claims_success(failure):
    lease = Lease()
    job = Mock()
    job.poll.return_value = 0
    getattr(job, failure).side_effect = OSError("controlled failure")
    try:
        with pytest.raises(OSError, match="controlled failure"):
            windows.supervise(["worker"], lease=lease, job_factory=lambda: job)
        job.close.assert_called_once_with()
    finally:
        lease.closed.set()


def fake_api(monkeypatch):
    api = Mock()
    api.CreateJobObjectW.return_value = 71
    api.SetInformationJobObject.return_value = 1
    api.InitializeProcThreadAttributeList.side_effect = lambda _a, _b, _c, size: (
        setattr(size._obj, "value", 64) or 1
    )
    handles = iter([91, 92, 93])

    def duplicate(_current, _source, _target, output, *_):
        output._obj.value = next(handles)
        return 1

    def create(*args):
        info = args[-1]._obj
        info.process, info.thread = 81, 82
        return 1

    api.DuplicateHandle.side_effect = duplicate
    api.CreateProcessW.side_effect = create
    api.AssignProcessToJobObject.return_value = 1
    api.ResumeThread.return_value = 1
    monkeypatch.setattr(windows.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: api, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
    monkeypatch.setattr(windows.sys, "stdout", SimpleNamespace(fileno=lambda: 1))
    monkeypatch.setattr(windows.sys, "stderr", SimpleNamespace(fileno=lambda: 2))
    return api


def test_worker_is_assigned_before_resume_and_inherits_only_selected_streams(monkeypatch):
    api = fake_api(monkeypatch)
    job = windows.WindowsJob()
    job.launch(["worker.exe", "literal; $(argument)"], b"text")
    names = [call[0] for call in api.mock_calls if not call[0].endswith(".__bool__")]
    assert (
        names.index("CreateProcessW")
        < names.index("AssignProcessToJobObject")
        < names.index("ResumeThread")
    )
    create = api.CreateProcessW.call_args.args
    assert create[5] == 4 | 0x80000  # Suspended + explicit extended startup attributes.
    assert create[1].value == 'worker.exe "literal; $(argument)"'
    attribute = api.UpdateProcThreadAttribute.call_args.args
    assert attribute[2] == 0x20002 and list(attribute[3]) == [91, 92, 93]
    limits = api.SetInformationJobObject.call_args.args[2]._obj
    assert limits.basic.flags == 0x2000
    job.close()


@pytest.mark.parametrize("failure", ["AssignProcessToJobObject", "ResumeThread"])
def test_unowned_or_unresumable_worker_is_terminated_without_running(monkeypatch, failure):
    api = fake_api(monkeypatch)
    getattr(api, failure).return_value = 0 if failure.startswith("Assign") else 0xFFFFFFFF
    job = windows.WindowsJob()
    with pytest.raises(OSError):
        job.launch(["worker.exe"], b"")
    if failure.startswith("Assign"):
        api.ResumeThread.assert_not_called()
    api.TerminateProcess.assert_called_once_with(81, 125)
    api.WaitForSingleObject.assert_called_once_with(81, 3000)
    api.DeleteProcThreadAttributeList.assert_called_once()
    job.close()


def test_windows_worker_source_and_frozen_dispatch_are_explicit(monkeypatch):
    module = "nexus.core.studio_speech_windows"
    assert sidecar.worker_command(module, "--speech", "--", "worker")[1] == "-I"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert sidecar.worker_command(module, "--speech", "--", "worker")[1:] == [
        "--nexus-worker",
        module,
        "--speech",
        "--",
        "worker",
    ]


@pytest.mark.parametrize("failure", ["SetInformationJobObject", "UpdateProcThreadAttribute"])
def test_missing_ownership_prerequisites_prevent_worker_creation(monkeypatch, failure):
    api = fake_api(monkeypatch)
    getattr(api, failure).return_value = 0
    job = None
    try:
        with pytest.raises(OSError):
            job = windows.WindowsJob()
            job.launch(["never-start"], b"")
        api.CreateProcessW.assert_not_called()
    finally:
        if job:
            job.close()


def test_cleanup_requires_zero_active_processes(monkeypatch):
    api = fake_api(monkeypatch)
    api.TerminateJobObject.return_value = 1
    counts = iter([2, 0])

    def query(_job, _kind, output, *_):
        output._obj.active = next(counts)
        return 1

    api.QueryInformationJobObject.side_effect = query
    job = windows.WindowsJob()
    job.stop()
    assert api.QueryInformationJobObject.call_count == 2
    job.close()


def test_cleanup_query_failure_is_not_success(monkeypatch):
    api = fake_api(monkeypatch)
    api.TerminateJobObject.return_value = 1
    api.QueryInformationJobObject.return_value = 0
    job = windows.WindowsJob()
    with pytest.raises(OSError, match="cannot be confirmed"):
        job.stop()
    job.close()


def test_windows_local_speech_uses_owned_runner(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock

    from nexus.core import studio_speech, studio_speech_process

    runner = AsyncMock(return_value="owned result")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(studio_speech_process, "run", runner)
    assert asyncio.run(studio_speech.local_process(["worker"], input=b"text")) == "owned result"
    runner.assert_awaited_once_with(["worker"], input=b"text")


@pytest.mark.parametrize("result, failure", [(7, False), (0xC0000005, False), (125, True)])
def test_private_owner_exits_after_explicit_cleanup_without_finalizing_reader(
    monkeypatch, result, failure
):
    calls = []
    monkeypatch.setattr(sys, "argv", ["owner", "--speech", "--", "worker"])

    def finish(_command):
        calls.append("cleanup finished")
        if failure:
            raise OSError("controlled cleanup failure")
        return result

    monkeypatch.setattr(windows, "supervise", finish)
    monkeypatch.setattr(
        sys, "stdout", SimpleNamespace(flush=lambda: calls.append("stdout flushed"))
    )
    monkeypatch.setattr(
        sys,
        "stderr",
        SimpleNamespace(write=lambda _text: None, flush=lambda: calls.append("stderr flushed")),
    )

    def exit_owner(code):
        calls.append(("private exit", code))
        raise SystemExit(code)

    monkeypatch.setattr(windows.os, "_exit", exit_owner)
    with pytest.raises(SystemExit) as error:
        windows.main()
    assert error.value.code == ctypes.c_int32(125 if failure else result).value
    assert calls[0] == "cleanup finished"
    assert calls[-3:] == ["stdout flushed", "stderr flushed", ("private exit", error.value.code)]

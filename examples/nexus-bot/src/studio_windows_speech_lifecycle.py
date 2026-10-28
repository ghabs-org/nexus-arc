"""Native Windows acceptance consumer for framework speech tree ownership.

Run from the repository with PYTHONPATH=. python
examples/nexus-bot/src/studio_windows_speech_lifecycle.py. No models, accounts,
installation or microphone are used. This is an explicit native check, not a
portable mock or a certification of installed desktop packaging.
"""

import argparse
import asyncio
import base64
import ctypes
import json
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from nexus.core import studio_desktop_sidecar
from nexus.core.studio import StudioService
from nexus.core.studio_speech import local_process


def worker(path):
    child = f"from pathlib import Path;import os,time;Path({str(path)!r}).write_text(str(os.getpid()));time.sleep(60)"
    return [
        sys.executable,
        "-c",
        "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"
        + repr(child)
        + "],creationflags=8);time.sleep(60)",
    ]


def child_handle(path):
    deadline = time.monotonic() + 10
    while not path.exists() or not path.read_text():
        if time.monotonic() > deadline:
            raise AssertionError("Controlled Windows speech child did not become ready")
        time.sleep(0.02)
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    handle = api.OpenProcess(0x100001, False, int(path.read_text()))
    if not handle:
        raise AssertionError("Cannot observe controlled child")
    return api, handle


def removed(api, handle):
    try:
        assert api.WaitForSingleObject(handle, 3000) == 0, "Detached speech child survived"
    finally:
        # Last-resort teardown targets only this already-opened fixture identity.
        if api.WaitForSingleObject(handle, 0) != 0:
            api.TerminateProcess(handle, 1)
        api.CloseHandle(handle)


def service_cases(root, *, command_factory=worker, observe=child_handle, confirm_removed=removed):
    """Exercise real Studio jobs; replace only model execution with a synthetic command."""
    results = {}
    for case in ("service_cancel", "service_shutdown", "queued_cancel"):
        directory = root / case
        directory.mkdir()
        ready = directory / "ready"
        model = directory / "fixture-model.pt"
        model.touch()
        dispatched = []
        original_command = studio_desktop_sidecar.worker_command

        def dispatch(
            module,
            *arguments,
            _original=original_command,
            _model=model,
            _dispatched=dispatched,
            _ready=ready,
            **options,
        ):
            if module != "nexus.core.studio_speech_worker":
                return _original(module, *arguments, **options)
            # Replace only the model engine. The actual service queue, input
            # ownership, isolated supervisor and subprocess cleanup stay active.
            assert Path(arguments[0]) == _model.resolve()
            assert Path(arguments[1]).read_bytes() == b"Synthetic recorded audio"
            _dispatched.append(module)
            return command_factory(_ready)

        studio = StudioService(
            directory / "workspace",
            speech_options={
                "profiles": {
                    "controlled-whisper": {
                        "kind": "whisper",
                        "action": "transcribe",
                        "model_path": str(model),
                    }
                }
            },
        )
        released = threading.Event()
        observed = None
        closed = False
        try:
            with patch.object(studio_desktop_sidecar, "worker_command", dispatch):
                if case == "queued_cancel":
                    # Fill the real executor before dispatch, without replacing
                    # submit or allowing the recording to reach a model process.
                    for _ in range(4):
                        studio.executor.submit(released.wait, 20)
                run = studio.speech.queue(
                    {
                        "profile": "controlled-whisper",
                        "format": "wav",
                        "audio_base64": base64.b64encode(b"Synthetic recorded audio").decode(),
                    },
                    None,
                )
                if case == "queued_cancel":
                    assert studio.run(run["id"])["status"] == "queued"
                    studio.cancel_run(run["id"])
                    assert list(studio.speech.directory.iterdir()) == []
                    assert studio.records("speech-input-owners") == []
                    released.set()
                    studio.close()
                    closed = True
                    assert not dispatched and not ready.exists()
                else:
                    observed = observe(ready)
                    assert dispatched == ["nexus.core.studio_speech_worker"]
                    if case == "service_shutdown":
                        studio.close()
                        closed = True
                    else:
                        studio.cancel_run(run["id"])
                    deadline = time.monotonic() + 5
                    while studio.run(run["id"])["status"] in {"running", "queued"}:
                        if time.monotonic() >= deadline:
                            raise AssertionError("Speech service did not settle its run")
                        time.sleep(0.01)
                    identity, observed = observed, None
                    confirm_removed(*identity)
                expected = "interrupted" if case == "service_shutdown" else "cancelled"
                assert studio.run(run["id"])["status"] == expected
                assert list(studio.speech.directory.iterdir()) == []
                assert studio.records("speech-input-owners") == []
                assert studio.catalog.list("artifacts") == []
                results[case] = (
                    "run settled; no child, temporary recording, owner record or artifact"
                )
        finally:
            released.set()
            if not closed:
                studio.close()
            if observed is not None:
                confirm_removed(*observed)
    return results


async def exercise(root):
    results = await asyncio.to_thread(service_cases, root)
    for case in ("cancel", "timeout"):
        ready = root / case
        task = asyncio.create_task(
            local_process(
                worker(ready),
                timeout=15 if case == "timeout" else 30,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        )
        api, handle = await asyncio.to_thread(child_handle, ready)
        if case == "cancel":
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, subprocess.TimeoutExpired) as error:
            assert isinstance(
                error, asyncio.CancelledError if case == "cancel" else subprocess.TimeoutExpired
            )
        else:
            raise AssertionError("Expected cancellation or timeout")
        finally:
            await asyncio.to_thread(removed, api, handle)
        results[case] = "owned detached child removed"
    payload = b"finite input\n" * 10000
    command = [
        sys.executable,
        "-c",
        "import sys;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stderr.write('diagnostic');sys.exit(7)",
    ]
    result = await local_process(
        command, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
    )
    assert result.stdout == payload and result.stderr == b"diagnostic" and result.returncode == 7
    try:
        await local_process(
            command,
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        assert error.returncode == 7
    else:
        raise AssertionError("Command failure was lost")
    results["finite_input"] = "bytes, stderr and nonzero status retained"
    ready = root / "owner-death"
    controller = subprocess.Popen([sys.executable, __file__, "--owner", str(ready)])
    try:
        api, handle = await asyncio.to_thread(child_handle, ready)
        controller.kill()
        controller.wait(timeout=5)
        await asyncio.to_thread(removed, api, handle)
    finally:
        if controller.poll() is None:
            controller.kill()
            controller.wait(timeout=5)
    results["owner_death"] = "owned detached child removed"
    return results


if __name__ == "__main__":
    if sys.platform != "win32":
        raise SystemExit(
            "Native Windows is required; portable checks do not replace this acceptance"
        )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", type=Path)
    arguments = parser.parse_args()
    if arguments.owner:
        asyncio.run(local_process(worker(arguments.owner), timeout=60))
    else:
        with tempfile.TemporaryDirectory(prefix="nexus-windows-speech-") as temporary:
            print(json.dumps(asyncio.run(exercise(Path(temporary))), sort_keys=True))

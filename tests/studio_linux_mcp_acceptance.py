"""Explicit Linux kernel/SDK acceptance; missing capabilities fail, never skip."""

import json
import os
import runpy
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

CONTROLLER = r"""
import asyncio,sys
from nexus.core.studio import StudioService
root,fixture,persistent=sys.argv[1:]
studio=StudioService(root+'/workspace')
record=studio.catalog.save('connectors',{'name':'Escaped fixture','data':{
    'type':'mcp','command':sys.executable,'args':['-I',fixture,root],
    'persistent':persistent=='true','enabled':True,'actions':['call'],
    'tools':['wait'],'read_only_tools':['wait']}})
asyncio.run(studio.connections.execute(record['id'],{'action':'call','tool':'wait'}))
"""


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("mode", ["close", "owner-kill"])
def test_escaped_stdio_descendants(tmp_path, persistent, mode):
    assert sys.platform == "linux" and hasattr(os, "pidfd_open")
    fixture = Path(__file__).with_name("fixtures") / "studio_mcp_escape.py"
    sentinel = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"])
    process = None
    handles = {}
    studio = None
    caller = None
    errors = []
    try:
        if mode == "owner-kill":
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    CONTROLLER,
                    str(tmp_path),
                    str(fixture),
                    str(persistent).lower(),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            import asyncio
            import threading

            from nexus.core.studio import StudioService

            studio = StudioService(tmp_path / "workspace")
            record = studio.catalog.save(
                "connectors",
                {
                    "name": "Escaped fixture",
                    "data": {
                        "type": "mcp",
                        "command": sys.executable,
                        "args": ["-I", str(fixture), str(tmp_path)],
                        "persistent": persistent,
                        "enabled": True,
                        "actions": ["call"],
                        "tools": ["wait"],
                        "read_only_tools": ["wait"],
                    },
                },
            )

            def invoke():
                try:
                    asyncio.run(
                        studio.connections.execute(record["id"], {"action": "call", "tool": "wait"})
                    )
                except BaseException as error:
                    errors.append(type(error).__name__)

            caller = threading.Thread(target=invoke, daemon=True)
            caller.start()
        deadline = time.monotonic() + 15
        while not (tmp_path / "called").exists():
            assert time.monotonic() < deadline, errors
            if process:
                assert process.poll() is None, "MCP owner exited before fixture invocation"
            time.sleep(0.02)
        details = json.loads((tmp_path / "pids.json").read_text())
        assert details["daemon_group"] != details["group"]
        for role in ("server", "daemon"):
            handles[role] = os.pidfd_open(details[role])
        started = time.monotonic()
        if mode == "owner-kill":
            process.kill()
            process.wait(timeout=3)
        else:
            studio.close()
            caller.join(3)
            assert not caller.is_alive()
        for role, descriptor in handles.items():
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            assert poller.poll(2500), role + " survived owner shutdown outside the SDK group"
        assert time.monotonic() - started < 8
        assert sentinel.poll() is None, "Unrelated process was signalled"
        assert (tmp_path / "calls.log").read_text().splitlines() == ["wait"]
        print(
            json.dumps(
                {
                    "mode": mode,
                    "persistent": persistent,
                    "server_stopped": True,
                    "escaped_daemon_stopped": True,
                    "unrelated_process_alive": True,
                    "invocations": 1,
                    "seconds": round(time.monotonic() - started, 3),
                }
            )
        )
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        if studio:
            studio.close()
        for descriptor in handles.values():
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(descriptor)
        if caller:
            caller.join(6)
        sentinel.terminate()
        sentinel.wait(timeout=3)


@pytest.mark.parametrize("persistent", [False, True])
def test_thin_example_stops_detached_child(persistent):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/nexus-bot/src/studio_mcp_lifecycle.py")
    )
    result = example["exercise"](persistent, detached=True)
    assert result["detached"] and result["child_stopped"] and result["server_stopped"]
    assert result["invocations"] == 1


def test_owner_already_gone_never_starts_server(tmp_path):
    from nexus.core.studio_desktop_sidecar import worker_command

    marker = tmp_path / "must-not-start"
    read, write = os.pipe()
    os.close(write)
    try:
        result = subprocess.run(
            worker_command(
                "nexus.core.studio_mcp_supervisor",
                "--",
                sys.executable,
                "-I",
                "-c",
                "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()",
            ),
            stdin=read,
            capture_output=True,
            timeout=3,
        )
    finally:
        os.close(read)
    assert result.returncode == 0 and not marker.exists()


def test_owner_eof_still_stops_backpressured_stdin(tmp_path):
    from nexus.core.studio_desktop_sidecar import worker_command

    marker = tmp_path / "child-pid"
    command = worker_command(
        "nexus.core.studio_mcp_supervisor",
        "--",
        sys.executable,
        "-I",
        "-c",
        "import os,time; from pathlib import Path; Path("
        + repr(str(marker))
        + ").write_text(str(os.getpid())); time.sleep(120)",
    )
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    descriptor = None
    try:
        deadline = time.monotonic() + 3
        while not marker.exists():
            assert time.monotonic() < deadline and process.poll() is None
            time.sleep(0.01)
        descriptor = os.pidfd_open(int(marker.read_text()))
        os.set_blocking(process.stdin.fileno(), False)
        deadline, total = time.monotonic() + 0.5, 0
        while time.monotonic() < deadline:
            try:
                total += os.write(process.stdin.fileno(), b"x" * 65536)
            except BlockingIOError:
                time.sleep(0.005)
        assert total > 65536, "The relay did not reach its bounded backpressure path"
        process.stdin.close()
        assert process.wait(timeout=3) == 0
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        assert poller.poll(1000)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        if descriptor is not None:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(descriptor)

"""Exercise Studio shutdown with a real stdio server and a resistant child."""

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from nexus.core.studio import StudioService
from nexus.core.studio_desktop import is_process_alive


def exercise(persistent=False, detached=False):
    if os.name != "posix":
        raise RuntimeError(
            "Process-group acceptance requires a POSIX host; Windows cleanup remains separate"
        )
    if detached:
        from nexus.tools.mcp_supervision import available

        if sys.platform != "linux" or not available():
            raise RuntimeError("Detached-child acceptance requires Linux procfs and pidfds")
    fixture = Path(__file__).with_name("fixtures") / "mcp_lifecycle_server.py"
    with tempfile.TemporaryDirectory(prefix="nexus-mcp-shutdown-") as directory:
        root = Path(directory)
        studio = StudioService(root / "workspace")
        state, errors, pids = {}, [], {}
        record = studio.catalog.save(
            "connectors",
            {
                "name": "Owned MCP fixture",
                "data": {
                    "type": "mcp",
                    "command": sys.executable,
                    "args": [str(fixture), str(root), *(["--detached"] if detached else [])],
                    "persistent": persistent,
                    "enabled": True,
                    "actions": ["discover", "call"],
                    "tools": ["wait"],
                    "read_only_tools": ["wait"],
                },
            },
        )

        def run():
            async def invoke():
                state.update(loop=asyncio.get_running_loop(), task=asyncio.current_task())
                return await studio.connections.execute(
                    record["id"], {"action": "call", "tool": "wait"}
                )

            try:
                asyncio.run(invoke())
            except BaseException as error:
                errors.append(type(error).__name__)

        caller = threading.Thread(target=run)
        caller.start()
        closed = False
        try:
            deadline = time.monotonic() + 15
            while not (root / "called").exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert (root / "called").exists(), errors
            pids = json.loads((root / "pids.json").read_text())
            assert pids["group"] in {pids["server"], pids["parent"]}
            started = time.monotonic()
            studio.close()
            closed = True
            caller.join(2)
            assert not caller.is_alive()
            assert not any(is_process_alive(pids[key]) for key in ["server", "child"])
            assert not studio.active_tasks
            assert (root / "calls.log").read_text().splitlines() == ["wait"]
            return {
                "persistent": persistent,
                "detached": detached,
                "server_stopped": True,
                "child_stopped": True,
                "caller_settled": True,
                "invocations": 1,
                "close_seconds": round(time.monotonic() - started, 3),
                "external_accounts_used": False,
            }
        finally:
            if caller.is_alive():
                state["loop"].call_soon_threadsafe(state["task"].cancel)
                caller.join(12)
            if pids and pids["group"] in {pids["server"], pids["parent"]}:
                try:
                    os.killpg(pids["group"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not closed:
                studio.close()


def exercise_owner_crash(persistent=False):
    """Exercise owner SIGKILL with one synthetic server group and a sentinel."""
    if os.name != "posix":
        raise RuntimeError("Owner-crash acceptance requires a supported POSIX host")
    from nexus.tools.mcp_supervision import available

    if not available():
        raise RuntimeError("Owner-crash acceptance requires per-session supervision")
    fixture = Path(__file__).with_name("fixtures") / "mcp_lifecycle_server.py"
    controller = """import asyncio,sys
from nexus.core.studio import StudioService
root,fixture,persistent=sys.argv[1:]
studio=StudioService(root+'/workspace')
record=studio.catalog.save('connectors',{'name':'Crash fixture','data':{
 'type':'mcp','command':sys.executable,'args':[fixture,root],
 'persistent':persistent=='true','enabled':True,'actions':['call'],
 'tools':['wait'],'read_only_tools':['wait']}})
asyncio.run(studio.connections.execute(record['id'],{'action':'call','tool':'wait'}))
"""
    with tempfile.TemporaryDirectory(prefix="nexus-mcp-owner-crash-") as directory:
        root, pids = Path(directory), {}
        sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"])
        owner = subprocess.Popen(
            [sys.executable, "-c", controller, directory, str(fixture), str(persistent).lower()],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 15
            while not (root / "called").exists():
                assert owner.poll() is None, "Owner exited before fixture invocation"
                assert time.monotonic() < deadline, "Fixture invocation timeout"
                time.sleep(0.02)
            pids = json.loads((root / "pids.json").read_text())
            assert pids["group"] != os.getpgrp()
            assert pids["group"] in {pids["server"], pids["parent"]}
            owner.kill()
            owner.wait(3)
            deadline = time.monotonic() + 3
            while any(is_process_alive(pids[key]) for key in ("server", "child")):
                assert time.monotonic() < deadline, "Fixture survived owner SIGKILL"
                time.sleep(0.02)
            assert sentinel.poll() is None
            assert (root / "calls.log").read_text().splitlines() == ["wait"]
            return {
                "persistent": persistent,
                "owner_sigkill": True,
                "server_stopped": True,
                "child_stopped": True,
                "unrelated_process_alive": True,
                "invocations": 1,
                "escaped_daemon_containment_tested": False,
                "external_accounts_used": False,
            }
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(3)
            if not pids and (root / "pids.json").exists():
                pids = json.loads((root / "pids.json").read_text())
            if pids and any(is_process_alive(pids[key]) for key in ("server", "child")):
                group = pids.get("group")
                if group != os.getpgrp() and group in {pids.get("server"), pids.get("parent")}:
                    try:
                        os.killpg(group, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            sentinel.terminate()
            sentinel.wait(3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persistent", action="store_true")
    parser.add_argument(
        "--detached", action="store_true", help="Verify Linux detached-child cleanup"
    )
    parser.add_argument(
        "--owner-crash", action="store_true", help="Verify same-group cleanup after owner SIGKILL"
    )
    args = parser.parse_args()
    if args.owner_crash and args.detached:
        parser.error("Choose owner-crash or detached acceptance separately")
    result = (
        exercise_owner_crash(args.persistent)
        if args.owner_crash
        else exercise(args.persistent, args.detached)
    )
    print(json.dumps(result, indent=2))

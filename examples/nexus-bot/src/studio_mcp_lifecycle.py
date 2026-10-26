"""Exercise Studio shutdown with a real stdio server and a resistant child."""

import argparse
import asyncio
import json
import os
import signal
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

        if not available():
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persistent", action="store_true")
    parser.add_argument(
        "--detached", action="store_true", help="Verify Linux detached-child cleanup"
    )
    args = parser.parse_args()
    print(json.dumps(exercise(args.persistent, args.detached), indent=2))

"""Explicit native macOS MCP owner-crash acceptance; no VM or installer."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nexus.core.studio_desktop import is_process_alive

CONTROLLER = """
import asyncio,sys
from nexus.core.studio import StudioService
root,fixture,persistent=sys.argv[1:]
studio=StudioService(root+'/workspace')
record=studio.catalog.save('connectors',{'name':'Crash fixture','data':{
 'type':'mcp','command':sys.executable,'args':[fixture,root],
 'persistent':persistent=='true','enabled':True,'actions':['call'],
 'tools':['wait'],'read_only_tools':['wait']}})
asyncio.run(studio.connections.execute(record['id'],{'action':'call','tool':'wait'}))
"""


@pytest.mark.parametrize("persistent", [False, True])
def test_stdio_group_stops_after_macos_owner_sigkill(tmp_path, persistent):
    assert sys.platform == "darwin", "Run this explicit gate on macOS"
    fixture = (
        Path(__file__).resolve().parents[1]
        / "examples/nexus-bot/src/fixtures/mcp_lifecycle_server.py"
    )
    sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"])
    owner = subprocess.Popen(
        [sys.executable, "-c", CONTROLLER, str(tmp_path), str(fixture), str(persistent).lower()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pids = {}
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "called").exists():
            assert owner.poll() is None, "Owner exited before fixture invocation"
            assert time.monotonic() < deadline, "Fixture invocation timeout"
            time.sleep(0.02)
        pids = json.loads((tmp_path / "pids.json").read_text())
        assert pids["group"] != os.getpgrp(), "Fixture must own a separate process group"
        assert pids["group"] in {pids["server"], pids["parent"]}
        owner.kill()
        owner.wait(3)
        deadline = time.monotonic() + 3
        while any(is_process_alive(pids[role]) for role in ("server", "child")):
            assert time.monotonic() < deadline, "MCP fixture survived owner SIGKILL"
            time.sleep(0.02)
        assert sentinel.poll() is None, "Unrelated process was signalled"
        assert (tmp_path / "calls.log").read_text().splitlines() == ["wait"]
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(3)
        # Only the acknowledged synthetic fixture group is eligible for cleanup.
        if not pids and (tmp_path / "pids.json").exists():
            pids = json.loads((tmp_path / "pids.json").read_text())
        group = pids.get("group")
        if group and group != os.getpgrp() and group in {pids.get("server"), pids.get("parent")}:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        sentinel.terminate()
        sentinel.wait(3)
